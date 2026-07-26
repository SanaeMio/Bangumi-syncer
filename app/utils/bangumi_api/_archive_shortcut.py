"""BangumiApi 内部 Archive 短路协调器

职责边界（必须严守，为第三期容灾留扩展点）：
- 仅负责 Archive 命中判断与数据返回
- 不感知 Bangumi 网络可达性（归 HttpLayerMixin 管）
- 不感知业务调用上下文

每个 try_* 方法返回 ShortcutResult 命名元组：
- hit=True: Archive 命中，调用方应直接返回 data
- hit=False: Archive 未启用或未命中，调用方应继续走 API
- reason: 命中/未命中原因，第三期容灾层可据此决策

第二期 A 接入路径：BangumiApi 的读方法首行先调 try_* 短路，
命中即返回，未命中降级到原 API 调用（保持现有行为完全一致）。
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

from typing import Any, NamedTuple, Optional

from ...core.config import config_manager
from ...core.logging import logger
from ..bangumi_archive._store import archive_store
from ..bangumi_constants import RELATION_ID_SEQUEL


class ShortcutResult(NamedTuple):
    """短路结果

    Attributes:
        hit: 是否命中 Archive
        data: 命中时返回的数据（dict / list / None）
        reason: 命中/未命中原因，供第三期容灾层决策
            - archive_disabled: Archive 未启用
            - archive_miss: Archive 启用但未命中该 id
            - archive_hit: Archive 命中
            - archive_error: Archive 查询异常
    """

    hit: bool
    data: Any
    reason: str


class ArchiveShortcut:
    """BangumiApi 内部 Archive 短路协调器

    线程安全说明：
    - enabled 状态读取无锁（config 变更不频繁，偶发不一致可接受）
    - 实际查询委托 archive_store（其内部有锁）
    """

    def __init__(self) -> None:
        self._enabled: bool = bool(
            config_manager.get("bangumi-archive", "enabled", fallback=False)
        )

    def reload_config(self) -> None:
        """从配置重新加载 enabled 状态（配置保存后由 config.py 调用）"""
        self._enabled = bool(
            config_manager.get("bangumi-archive", "enabled", fallback=False)
        )
        if self._enabled:
            logger.info("bangumi_archive 短路已启用，读操作将优先走 Archive")

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ===== 短路方法 =====

    def try_get_subject(self, subject_id: int) -> ShortcutResult:
        """短路 get_subject

        Returns:
            ShortcutResult.hit=True 时 data 是 dict（对齐 API 返回）
            ShortcutResult.hit=False 时 data 为 None
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            data = archive_store.get_subject(subject_id)
            if data is not None:
                return ShortcutResult(True, data, "archive_hit")
            return ShortcutResult(False, None, "archive_miss")
        except Exception as e:
            logger.warning(f"bangumi_archive 短路 get_subject 异常: {e}")
            return ShortcutResult(False, None, "archive_error")

    def try_get_episodes(
        self, subject_id: int, episode_type: Optional[int] = None
    ) -> ShortcutResult:
        """短路 get_episodes

        Args:
            subject_id: 条目 ID
            episode_type: 可选过滤（如 EPISODE_TYPE_NORMAL=0）

        Returns:
            hit=True 时 data 是 list[dict]（可能为空列表，表示该条目确实无章节）
            hit=False 时 data 为 None，调用方应走 API

        注意：Archive 命中但章节为空时，仍返回 hit=True，
        避免对空条目重复调用 API。
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            data = archive_store.get_episodes(subject_id, episode_type=episode_type)
            # Archive 命中（即使空列表也算命中，避免 API 重复调用）
            return ShortcutResult(True, data, "archive_hit")
        except Exception as e:
            logger.warning(f"bangumi_archive 短路 get_episodes 异常: {e}")
            return ShortcutResult(False, None, "archive_error")

    def try_get_related_subjects(self, subject_id: int) -> ShortcutResult:
        """短路 get_related_subjects

        Returns:
            hit=True 时 data 是 list[dict]（对齐 API 返回结构，含 relation 中文）
            hit=False 时 data 为 None
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            data = archive_store.get_related_subjects(subject_id)
            # Archive 命中（即使空列表也算命中）
            return ShortcutResult(True, data, "archive_hit")
        except Exception as e:
            logger.warning(f"bangumi_archive 短路 get_related_subjects 异常: {e}")
            return ShortcutResult(False, None, "archive_error")

    def try_find_next_sequel_id(self, subject_id: int) -> ShortcutResult:
        """短路 _find_next_sequel_id

        返回单个续集 subject_id（int）或 None。

        Returns:
            hit=True 时 data 是 int 或 None
                - int: 找到续集
                - None: 该条目无续集（Archive 已确认）
            hit=False 时 data 为 None，调用方应走 API
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            sequel_ids = archive_store.find_related_by_relation(
                subject_id, RELATION_ID_SEQUEL
            )
            if sequel_ids:
                return ShortcutResult(True, sequel_ids[0], "archive_hit")
            # 需要进一步判断：Archive 中是否有该 subject_id 的关联记录？
            # 简化：若该 subject 在 Archive 中存在但无续集关联，认为命中 None
            # 否则降级到 API
            subject = archive_store.get_subject(subject_id)
            if subject is not None:
                # subject 存在但无续集关联
                return ShortcutResult(True, None, "archive_hit")
            return ShortcutResult(False, None, "archive_miss")
        except Exception as e:
            logger.warning(f"bangumi_archive 短路 find_next_sequel_id 异常: {e}")
            return ShortcutResult(False, None, "archive_error")

    def try_find_related_id_by_relation(
        self, subject_id: int, relation_cn: str
    ) -> ShortcutResult:
        """短路 _find_related_id_by_relation

        Args:
            subject_id: 起始条目
            relation_cn: 关联中文名（如「续集」「前传」「主线故事」）
                        与 bangumi_api/episodes.py 现有签名一致

        Returns:
            hit=True 时 data 是 int 或 None
            hit=False 时 data 为 None
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            # 中文 → relation_id（通过 RELATIONS 反查）
            from ..bangumi_constants import RELATION_CN_TO_ID

            relation_id = RELATION_CN_TO_ID.get(relation_cn)
            if relation_id is None:
                # 未知关联类型，降级到 API
                return ShortcutResult(False, None, "archive_miss")

            related_ids = archive_store.find_related_by_relation(
                subject_id, relation_id
            )
            if related_ids:
                return ShortcutResult(True, related_ids[0], "archive_hit")
            # 判断 subject 是否在 Archive 中存在
            subject = archive_store.get_subject(subject_id)
            if subject is not None:
                return ShortcutResult(True, None, "archive_hit")
            return ShortcutResult(False, None, "archive_miss")
        except Exception as e:
            logger.warning(
                f"bangumi_archive 短路 find_related_id_by_relation 异常: {e}"
            )
            return ShortcutResult(False, None, "archive_error")

    def try_find_sequel_chain(
        self, subject_id: int, max_hops: int = 30
    ) -> ShortcutResult:
        """短路续集链查找

        用于 find_episode_across_seasons 优化：一次拿完整续集链，
        避免逐跳 API 调用。

        Returns:
            hit=True 时 data 是 list[int]（续集链 subject_id 列表，不含起始）
            hit=False 时 data 为 None
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            chain = archive_store.find_sequel_chain(subject_id, max_hops=max_hops)
            # 判断 subject 是否在 Archive 中存在
            if not chain:
                subject = archive_store.get_subject(subject_id)
                if subject is None:
                    return ShortcutResult(False, None, "archive_miss")
            return ShortcutResult(True, chain, "archive_hit")
        except Exception as e:
            logger.warning(f"bangumi_archive 短路 find_sequel_chain 异常: {e}")
            return ShortcutResult(False, None, "archive_error")

    def try_search(
        self,
        title: str,
        start_date: str = "",
        end_date: str = "",
        limit: int = 5,
        subject_types: Optional[list[int]] = None,
    ) -> ShortcutResult:
        """短路 search API（标题搜索）

        优先精确匹配标题索引，未命中时降级到 rapidfuzz 模糊匹配。
        命中后通过 archive_store 拉取完整 subject 数据并按 type/air_date 过滤，
        对齐 API 的 filter 行为（type 默认 [2]，air_date 区间为 [start, end)）。

        Returns:
            hit=True 时 data 是 list[dict]（对齐 API data 字段内容）
            hit=False 时 data 为 None，调用方应走 API
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            from ..bangumi_archive._title_index import archive_title_index

            # 1. 精确匹配
            ids = archive_title_index.find_subject_ids_by_title(title)

            # 2. 精确未命中时模糊匹配
            if not ids:
                fuzzy = archive_title_index.find_subject_ids_fuzzy(title)
                ids = [sid for sid, _ in fuzzy]

            if not ids:
                return ShortcutResult(False, None, "archive_miss")

            # 3. 拉取完整 subject + 过滤
            # API 默认 type=[2]（与 search() 一致），None/空列表时也用 [2]
            types_set: set[int] = set(subject_types) if subject_types else {2}
            results: list[dict[str, Any]] = []
            for sid in ids:
                subject = archive_store.get_subject(sid)
                if subject is None:
                    continue
                # type 过滤
                if subject.get("type") not in types_set:
                    continue
                # air_date 过滤：API filter 为 [">=start", "<end"]
                # subject.date 缺失时不参与过滤（避免误删无日期条目）
                subj_date = subject.get("date")
                if isinstance(subj_date, str) and subj_date:
                    if start_date and subj_date < start_date:
                        continue
                    if end_date and subj_date >= end_date:
                        continue
                results.append(subject)
                if len(results) >= limit:
                    break

            if not results:
                return ShortcutResult(False, None, "archive_miss")
            return ShortcutResult(True, results, "archive_hit")
        except Exception as e:
            logger.warning(f"bangumi_archive 短路 search 异常: {e}")
            return ShortcutResult(False, None, "archive_error")

    def try_search_old(
        self,
        title: str,
        subject_type: int = 2,
    ) -> ShortcutResult:
        """短路 search_old API（旧版标题搜索）

        旧版接口返回结构为 {results, list}，list_only=True 时取 list 字段。
        与 try_search 不同：旧版接口不支持 air_date 过滤，仅按 type 过滤。

        命中后调用方（bgm_search）会取前 N 条调 get_subject 拉详情，
        由于 get_subject 也接入了 Archive 短路，整条链路全走 Archive。

        Returns:
            hit=True 时 data 是 list[dict]（对齐旧版 API 的 list 字段）
            hit=False 时 data 为 None，调用方应走 API
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            from ..bangumi_archive._title_index import archive_title_index

            # 1. 精确匹配
            ids = archive_title_index.find_subject_ids_by_title(title)

            # 2. 精确未命中时模糊匹配
            if not ids:
                fuzzy = archive_title_index.find_subject_ids_fuzzy(title)
                ids = [sid for sid, _ in fuzzy]

            if not ids:
                return ShortcutResult(False, None, "archive_miss")

            # 3. 拉取完整 subject + type 过滤
            # 旧版接口的 subject_type 即 API type（如 2=动画）
            results: list[dict[str, Any]] = []
            for sid in ids:
                subject = archive_store.get_subject(sid)
                if subject is None:
                    continue
                # type 过滤（旧版接口按单一 type 查询）
                if subject.get("type") != subject_type:
                    continue
                results.append(subject)

            if not results:
                return ShortcutResult(False, None, "archive_miss")
            return ShortcutResult(True, results, "archive_hit")
        except Exception as e:
            logger.warning(f"bangumi_archive 短路 search_old 异常: {e}")
            return ShortcutResult(False, None, "archive_error")


# 全局单例
archive_shortcut = ArchiveShortcut()
