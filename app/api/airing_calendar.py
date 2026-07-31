"""番剧放送日历 API

提供：
- GET /api/airing-calendar   获取未来 N 天的放送日程

数据源：Bangumi Archive 的 episode.airdate（仅在 Archive 启用且已导入数据时可用）。
"仅我在追"模式额外调用 Bangumi API 获取用户在看列表（带 1 小时 TTL 缓存）。
"""

# ruff: noqa: UP045 — Pydantic v2 在 Python 3.9 下解析 ``str | None`` 会失败，保留 Optional

from __future__ import annotations

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
    status: str = Field(description="状态")
    days: list[AiringDay] = Field(description="按日期分组的放送日程")
    total_episodes: int = Field(description="总放送集数")
    only_watching: bool = Field(description="是否仅展示在追番剧")
    archive_enabled: bool = Field(description="Archive 是否启用")


@router.get("", response_model=AiringCalendarResponse)
async def get_airing_calendar(
    days: int = Query(30, description="查询天数（7/14/30）"),
    only_watching: bool = Query(True, description="仅展示在追番剧"),
    subject_type: int = Query(
        0, description="条目类型筛选（0=全部, 2=动画, 6=三次元）"
    ),
    refresh: bool = Query(False, description="强制刷新在看列表缓存"),
    user: dict = Depends(get_current_user_flexible),
) -> AiringCalendarResponse:
    """获取未来 N 天的番剧放送日程

    **前置条件**：Bangumi Archive 已启用且已导入数据。
    "仅我在追"模式需配置 Bangumi 账号（调用收藏列表 API）。
    """
    # 仅在 Archive 开启时可用
    # reload_config 可能因配置非法（int 转换失败）或目录创建失败抛异常，
    # 此时应降级为 archive_disabled 而非让 API 500
    try:
        bangumi_archive.reload_config()
    except Exception as e:
        logger.warning(
            f"airing-calendar: bangumi_archive.reload_config 失败，降级为 archive_disabled: {e}"
        )
        return AiringCalendarResponse(
            status="archive_disabled",
            days=[],
            total_episodes=0,
            only_watching=False,
            archive_enabled=False,
        )
    if not bangumi_archive.enabled:
        return AiringCalendarResponse(
            status="archive_disabled",
            days=[],
            total_episodes=0,
            only_watching=False,
            archive_enabled=False,
        )
    active_db = bangumi_archive.get_active_db_path()
    if not active_db.exists():
        return AiringCalendarResponse(
            status="archive_not_imported",
            days=[],
            total_episodes=0,
            only_watching=False,
            archive_enabled=True,
        )

    # 规范化 days 到允许值
    if days not in _ALLOWED_DAYS:
        days = 30

    # 计算日期范围：使用调度器配置时区的"今日"，避免服务器系统时区
    # （Docker 默认 UTC）与 [scheduler] timezone 不一致导致日期错位
    today = config_manager.today_in_scheduler_tz()
    end_date = today + timedelta(days=days - 1)
    start_str = today.isoformat()
    end_str = end_date.isoformat()

    # 类型筛选：0=全部(2,6), 2=动画, 6=三次元
    if subject_type == 2:
        subject_types: tuple[int, ...] = (2,)
    elif subject_type == 6:
        subject_types = (6,)
    else:
        subject_types = (2, 6)

    # "仅我在追"过滤
    subject_ids: Optional[set[int]] = None
    actual_only_watching = False
    if only_watching:
        api = _build_bangumi_api()
        if api is not None:
            try:
                if refresh:
                    invalidate_watching_cache(api.username)
                subject_ids = get_watching_subject_ids(api)
                actual_only_watching = True
            except Exception as e:
                logger.warning(f"获取在看列表失败，降级为全部放送: {e}")
                subject_ids = None
            finally:
                # 临时构造的 BangumiApi 持有 httpx.Client 连接池，需显式释放
                api.close()
        # 无 Bangumi 配置或获取失败：降级为全部放送
    # 查询 Archive
    rows = archive_store.get_episodes_by_airdate(
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
        only_watching=actual_only_watching,
        archive_enabled=True,
    )
