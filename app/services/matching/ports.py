"""Bangumi 只读搜索端口（阶段四）

收窄匹配管道对 BangumiApi 的依赖：step 只能调用此处声明的只读方法，
写操作（mark_episode_watched / ensure_subject_watching / change_collection_state）
仅在编排器标记阶段通过完整 BangumiApi 实例调用。

Protocol 是 lint/类型检查层面的约束，运行时 BangumiApi 实例天然满足，
无需显式继承或包装。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.services.sync_service.match_trace import MatchTrace


@runtime_checkable
class BangumiSearchPort(Protocol):
    """匹配管道只读端口

    声明 step 可能调用的只读方法。BangumiApi 实例结构化满足本协议，
    编排器注入完整实例时，匹配阶段只能"看到"这些方法（类型层面）。
    """

    # 搜索与候选排序
    def bgm_search(
        self,
        title: str,
        ori_title: str = "",
        premiere_date: str = "",
        is_movie: bool = False,
        subject_types: list[int] | None = None,
        trace: MatchTrace | None = None,
        out_meta: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None: ...

    def search(
        self,
        title: str,
        start_date: str = "",
        end_date: str = "",
        limit: int = 5,
        list_only: bool = True,
        subject_types: list[int] | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]: ...

    # 条目详情
    def get_subject(
        self, subject_id: int, use_archive: bool = True
    ) -> dict[str, Any]: ...

    def get_related_subjects(
        self, subject_id: int
    ) -> list[dict[str, Any]] | dict[str, Any]: ...

    def get_episodes(
        self, subject_id: int, episode_type: int = 0, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]: ...

    # 集数解析（只读：返回目标季与集 ID，不写收藏状态）
    def get_target_season_episode_id(
        self,
        subject_id: str,
        target_season: int,
        target_ep: int,
        is_season_subject_id: bool,
        release_date: str | None = None,
    ) -> tuple[str | int | None, str | int | None]: ...

    def get_movie_main_episode_id(
        self, subject_id: str, target_sort: int = 1
    ) -> tuple[str, int]: ...

    def find_episode_across_seasons(
        self, subject_id: str, target_sort: int, target_season: int = 1
    ) -> tuple[str, int] | None: ...

    # 相似度计算
    def title_diff_ratio(
        self, search_title: str, ori_title: str, candidate: dict[str, Any]
    ) -> float: ...

    # API 可达性（读状态）
    def is_api_unreachable(self) -> bool: ...

    # 已废弃的死状态属性（兼容保留，不再写入/读取）：
    # archive 命中来源改由 ctx.archive_hit 传递（ArchiveShortcutStep → APISearchStep），
    # 命中变体方法改由 bgm_search 的 out_meta 回传。保留声明仅为历史兼容。
    last_hit_source: str
    last_match_method: str
