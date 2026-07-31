"""番剧放送日历 API

提供：
- GET /api/airing-calendar   获取未来 N 天的放送日程

数据源：Bangumi Archive 的 episode.airdate（仅在 Archive 启用且已导入数据时可用）。
"仅我在追"模式额外调用 Bangumi API 获取用户在看列表（带 1 小时 TTL 缓存）。
"""

# ruff: noqa: UP045 — Pydantic v2 在 Python 3.9 下解析 ``str | None`` 会失败，保留 Optional

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..core.config import config_manager
from ..core.logging import logger
from ..utils.bangumi_api import BangumiApi
from ..utils.bangumi_api.collection import get_watching_subject_ids
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


def _build_bangumi_api() -> Optional[BangumiApi]:
    """从配置构造 BangumiApi 实例（取第一个有效 bangumi 账号配置）

    Returns:
        BangumiApi 实例；若无有效配置返回 None
    """
    configs = config_manager.get_bangumi_configs()
    if not configs:
        return None
    cfg = next(iter(configs.values()))
    dev_snapshot = config_manager.get_dev_http_snapshot()
    return BangumiApi(
        username=cfg["username"],
        access_token=cfg["access_token"],
        private=cfg.get("private", False),
        http_proxy=dev_snapshot["script_proxy"],
        ssl_verify=dev_snapshot["ssl_verify"],
        bgm_api_proxy=dev_snapshot["bgm_api_proxy"],
        bgm_next_proxy=dev_snapshot["bgm_next_proxy"],
    )


@router.get("", response_model=AiringCalendarResponse)
async def get_airing_calendar(
    days: int = Query(14, description="查询天数（7/14/30）"),
    only_watching: bool = Query(True, description="仅展示在追番剧"),
    user: dict = Depends(get_current_user_flexible),
) -> AiringCalendarResponse:
    """获取未来 N 天的番剧放送日程

    **前置条件**：Bangumi Archive 已启用且已导入数据。
    "仅我在追"模式需配置 Bangumi 账号（调用收藏列表 API）。
    """
    # 仅在 Archive 开启时可用
    bangumi_archive.reload_config()
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
        days = 14

    # 计算日期范围
    today = date.today()
    end_date = today + timedelta(days=days - 1)
    start_str = today.isoformat()
    end_str = end_date.isoformat()

    # "仅我在追"过滤
    subject_ids: Optional[set[int]] = None
    actual_only_watching = False
    if only_watching:
        api = _build_bangumi_api()
        if api is not None:
            try:
                subject_ids = get_watching_subject_ids(api)
                actual_only_watching = True
            except Exception as e:
                logger.warning(f"获取在看列表失败，降级为全部放送: {e}")
                subject_ids = None
        # 无 Bangumi 配置或获取失败：降级为全部放送
    # 查询 Archive
    rows = archive_store.get_episodes_by_airdate(
        start_date=start_str,
        end_date=end_str,
        subject_ids=subject_ids,
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
