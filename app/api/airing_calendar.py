"""番剧放送日历 API（"我的追番"卡片数据源）

提供：
- GET /api/airing-calendar           获取未来 N 天的在追番剧放送日程
- GET /api/airing-calendar/accounts   列出已配置的 Bangumi 账号（供多用户切换）

定位：仪表板"我的追番"卡片，**仅展示当前 Bangumi 账号"在看"番剧的放送日程**。
不再支持"全部放送"模式——"我的追番"语义下全部放送对用户无意义，获取在看列表
失败时返回 watching_unavailable 状态，由前端提示用户检查账号配置。

多账号场景下（DB 中账号数 > 1），前端可通过 ``account`` 参数指定要查看
的 Bangumi 账号段名，实现卡片内切换账号。

数据源：Bangumi Archive 的 episode.airdate（仅在 Archive 启用且已导入数据时可用）。
"""

# ruff: noqa: UP045 — Pydantic v2 在 Python 3.9 下解析 ``str | None`` 会失败，保留 Optional

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..core.config import config_manager
from ..core.logging import logger
from ..utils.bangumi_api.collection import (
    get_watching_subject_ids,
    invalidate_watching_cache,
)
from ..utils.bangumi_api.factory import (
    build_bangumi_api_from_active_config as _build_bangumi_api,
)
from ..utils.bangumi_archive import bangumi_archive
from ..utils.bangumi_archive._store import archive_store
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/airing-calendar", tags=["airing-calendar"])

# 允许的查询天数（防止过大范围拖慢查询）
_ALLOWED_DAYS = (7, 14, 30)

# reload_config 短 TTL 缓存：仪表板每次刷新都会调用本接口，
# 每次都重读配置文件开销不必要。30 秒内复用上次结果，配置变更最迟 30 秒生效。
_RELOAD_CONFIG_TTL_SEC = 30.0
_reload_config_lock = asyncio.Lock()
_last_reload_config_ts: float = 0.0


async def _reload_archive_config_cached() -> None:
    """带短 TTL 缓存的 bangumi_archive.reload_config()

    仪表板高频刷新时避免重复读 config.ini + 创建目录。
    配置变更（如 Web 界面保存触发 apply_config_after_save）会主动调用
    reload_config，本缓存仅减少 API 路径上的重复调用。
    """
    global _last_reload_config_ts
    now = time.monotonic()
    if (now - _last_reload_config_ts) < _RELOAD_CONFIG_TTL_SEC:
        return
    async with _reload_config_lock:
        # double-check：持锁后再判一次，避免多个并发请求同时穿透
        if (time.monotonic() - _last_reload_config_ts) < _RELOAD_CONFIG_TTL_SEC:
            return
        # reload_config 是同步 IO（读文件），用 to_thread 避免阻塞事件循环
        await asyncio.to_thread(bangumi_archive.reload_config)
        _last_reload_config_ts = time.monotonic()


class AiringEpisode(BaseModel):
    episode_id: int = Field(description="章节 ID")
    subject_id: int = Field(description="条目 ID")
    subject_name: str = Field(description="条目原名")
    subject_name_cn: Optional[str] = Field(None, description="条目中文名")
    subject_type: int = Field(description="条目类型（2=动画, 6=三次元）")
    episode_name: Optional[str] = Field(None, description="章节原名")
    episode_name_cn: Optional[str] = Field(None, description="章节中文名")
    episode_sort: Optional[int] = Field(None, description="章节排序号")
    airdate: str = Field(description="放送日期 YYYY-MM-DD")


class AiringDay(BaseModel):
    date: str = Field(description="日期 YYYY-MM-DD")
    weekday: int = Field(description="星期（0=周一 … 6=周日）")
    episodes: list[AiringEpisode] = Field(
        default_factory=list, description="当日放送列表"
    )


class AiringCalendarResponse(BaseModel):
    status: str = Field(
        description=(
            "状态：ok=正常；archive_disabled=Archive 未启用；"
            "archive_not_imported=Archive 未导入数据；"
            "watching_unavailable=获取在看列表失败（未配置账号或 API 异常）"
        )
    )
    days: list[AiringDay] = Field(description="按日期分组的放送日程")
    total_episodes: int = Field(description="总放送集数")
    archive_enabled: bool = Field(description="Archive 是否启用")
    today: str = Field(description="调度器时区的今日日期 YYYY-MM-DD（前端以此为准）")


class BangumiAccountInfo(BaseModel):
    section_name: str = Field(description="配置段名（切换账号时作为 account 参数传回）")
    username: str = Field(description="Bangumi 用户名（展示用）")


class BangumiAccountsResponse(BaseModel):
    mode: str = Field(description="同步模式：single=单用户，multi=多用户")
    accounts: list[BangumiAccountInfo] = Field(description="已配置的 Bangumi 账号列表")
    active: Optional[str] = Field(
        None, description="当前活跃账号段名（单用户为 'bangumi'，多用户取首个映射）"
    )


@router.get("/accounts", response_model=BangumiAccountsResponse)
async def list_bangumi_accounts(
    user: dict = Depends(get_current_user_flexible),
) -> BangumiAccountsResponse:
    """列出已配置的 Bangumi 账号（供"我的追番"卡片多用户切换）

    DB 为唯一真相源：返回 ``bangumi_accounts`` 表中所有有 username 的账号。
    仅返回 ``section_name`` 与 ``username``，不暴露 access_token。

    ``mode`` 字段由账号数量推导（1=single，>1=multi），兼容前端 dropdown 显示逻辑。
    """
    from app.core.accounts import (
        get_active_bangumi_account,
        list_bangumi_accounts as _list_accounts,
    )

    accounts: list[BangumiAccountInfo] = []
    for acc in _list_accounts():
        if acc.get("username"):
            accounts.append(
                BangumiAccountInfo(
                    section_name=acc["section_name"],
                    username=acc["username"],
                )
            )

    active_acc = get_active_bangumi_account()
    active = active_acc.get("section_name") if active_acc else None

    # 列表长度=1 即单用户，无需 sync.mode 判断；mode 字段仅供前端 dropdown 显示判断
    mode = "multi" if len(accounts) > 1 else "single"
    return BangumiAccountsResponse(mode=mode, accounts=accounts, active=active)


@router.get("", response_model=AiringCalendarResponse)
async def get_airing_calendar(
    days: int = Query(30, description="查询天数（7/14/30）"),
    subject_type: int = Query(
        0, description="条目类型筛选（0=全部, 2=动画, 6=三次元）"
    ),
    refresh: bool = Query(False, description="强制刷新在看列表缓存"),
    account: Optional[str] = Query(
        None, description="多用户模式下指定 Bangumi 账号段名（来自 /accounts）"
    ),
    user: dict = Depends(get_current_user_flexible),
) -> AiringCalendarResponse:
    """获取未来 N 天的在追番剧放送日程（"我的追番"卡片数据源）

    **定位**：仅展示当前 Bangumi 账号"在看"番剧的放送日程，不支持"全部放送"。

    **前置条件**：Bangumi Archive 已启用且已导入数据 + 已配置 Bangumi 账号。

    **失败语义**（不降级为全部放送）：
    - Archive 未启用 → status=archive_disabled
    - Archive 未导入 → status=archive_not_imported
    - 未配置账号或获取在看列表失败 → status=watching_unavailable
    """
    # 使用调度器配置时区的"今日"，避免服务器系统时区（Docker 默认 UTC）
    # 与 [scheduler] timezone 不一致导致日期错位。前端也以此为准。
    today = config_manager.today_in_scheduler_tz()
    today_str = today.isoformat()

    # 仅在 Archive 开启时可用
    # reload_config 可能因配置非法（int 转换失败）或目录创建失败抛异常，
    # 此时应降级为 archive_disabled 而非让 API 500
    try:
        await _reload_archive_config_cached()
    except Exception as e:
        logger.warning(
            f"airing-calendar: bangumi_archive.reload_config 失败，降级为 archive_disabled: {e}"
        )
        return AiringCalendarResponse(
            status="archive_disabled",
            days=[],
            total_episodes=0,
            archive_enabled=False,
            today=today_str,
        )
    if not bangumi_archive.enabled:
        return AiringCalendarResponse(
            status="archive_disabled",
            days=[],
            total_episodes=0,
            archive_enabled=False,
            today=today_str,
        )
    active_db = bangumi_archive.get_active_db_path()
    if not active_db.exists():
        return AiringCalendarResponse(
            status="archive_not_imported",
            days=[],
            total_episodes=0,
            archive_enabled=True,
            today=today_str,
        )

    # 规范化 days 到允许值
    if days not in _ALLOWED_DAYS:
        days = 30

    end_date = today + timedelta(days=days - 1)
    start_str = today_str
    end_str = end_date.isoformat()

    # 类型筛选：0=全部(2,6), 2=动画, 6=三次元
    if subject_type == 2:
        subject_types: tuple[int, ...] = (2,)
    elif subject_type == 6:
        subject_types = (6,)
    else:
        subject_types = (2, 6)

    # "我的追番"必须配置 Bangumi 账号：按 account 段名构造（多用户切换）
    api = _build_bangumi_api(account)
    if api is None:
        # 未配置 Bangumi 账号或指定段不存在：返回 watching_unavailable
        return AiringCalendarResponse(
            status="watching_unavailable",
            days=[],
            total_episodes=0,
            archive_enabled=True,
            today=today_str,
        )
    try:
        if refresh:
            invalidate_watching_cache(api.username)
        # HTTP 调用放线程池，避免阻塞事件循环
        subject_ids = await asyncio.to_thread(get_watching_subject_ids, api)
    except Exception as e:
        logger.warning(f"airing-calendar: 获取在看列表失败: {e}")
        # 不降级为全部放送，与"我的追番"语义对齐
        return AiringCalendarResponse(
            status="watching_unavailable",
            days=[],
            total_episodes=0,
            archive_enabled=True,
            today=today_str,
        )
    finally:
        # 临时构造的 BangumiApi 持有 httpx.Client 连接池，需显式释放
        api.close()

    # 查询 Archive（SQLite 同步 IO 放线程池）
    rows = await asyncio.to_thread(
        archive_store.get_episodes_by_airdate,
        start_date=start_str,
        end_date=end_str,
        subject_ids=subject_ids,
        subject_types=subject_types,
    )

    # 按日期分组
    days_map: dict[str, list[AiringEpisode]] = {}
    for row in rows:
        ep = AiringEpisode(
            episode_id=row["episode_id"],
            subject_id=row["subject_id"],
            subject_name=row.get("subject_name") or "",
            subject_name_cn=row.get("subject_name_cn") or None,
            subject_type=row.get("subject_type", 0),
            episode_name=row.get("ep_name") or None,
            episode_name_cn=row.get("ep_name_cn") or None,
            episode_sort=row.get("ep_sort"),
            airdate=row.get("airdate") or "",
        )
        days_map.setdefault(ep.airdate, []).append(ep)

    # 构造连续日期序列（含无放送的日期，前端渲染空格子）
    result_days: list[AiringDay] = []
    for i in range(days):
        d = today + timedelta(days=i)
        d_str = d.isoformat()
        # weekday: 周一=0 … 周日=6
        result_days.append(
            AiringDay(
                date=d_str,
                weekday=d.weekday(),
                episodes=days_map.get(d_str, []),
            )
        )

    return AiringCalendarResponse(
        status="ok",
        days=result_days,
        total_episodes=len(rows),
        archive_enabled=True,
        today=today_str,
    )
