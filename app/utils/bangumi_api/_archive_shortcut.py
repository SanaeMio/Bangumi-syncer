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

标题归一化、多策略标题匹配、subject 批量过滤已下沉到
bangumi_archive 模块（_title_normalize / _title_index / _store），
本模块仅负责命中判断 + 降级原因封装。
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

import re
from typing import Any, NamedTuple, Optional

from ...core.config import config_manager
from ...core.logging import logger
from ..bangumi_archive._store import archive_store
from ..bangumi_archive._title_index import archive_title_index
from ..bangumi_archive._title_normalize import (
    _MEDIA_PREFIX_VARIANTS,
    MATCH_METHOD_EXACT,
    MATCH_METHOD_FUZZY,
    MATCH_METHOD_PREFIX_VARIANT,
    _split_title_segments,
    _strip_season_episode_suffix,
    build_search_variants,
)
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
        match_method: 命中时的匹配方式（预测性匹配标注），供调用方如实反映
            本次命中是"精确匹配"还是"靠剥离/前缀预测推导"，取值见
            _title_normalize.MATCH_METHOD_*。未命中时为 ""。
    """

    hit: bool
    data: Any
    reason: str
    match_method: str = ""


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
            logger.debug("bangumi_archive 短路已启用，读操作将优先走 Archive")

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

        防止 archive 不完整时静默返回空数据：
        底层 archive_store.get_episodes 在 subject 不存在时也返回空列表 []，
        无法区分"subject 在 archive 但无章节"和"subject 不在 archive"。
        因此当章节为空时，追加 archive_store.get_subject 校验 subject 存在性：
        - subject 存在 → hit=True（确实无章节，避免 API 重复调用）
        - subject 不存在 → hit=False, reason="archive_miss"，降级到 API
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            data = archive_store.get_episodes(subject_id, episode_type=episode_type)
            # 章节非空：archive 命中
            if data:
                return ShortcutResult(True, data, "archive_hit")
            # 章节为空：需校验 subject 是否在 archive 中
            # 避免archive 不完整（新增条目/冷门条目缺失）时静默返回空数据导致 sync 失败
            subject = archive_store.get_subject(subject_id)
            if subject is not None:
                # subject 存在但无章节（如剧场版只有正片无章节记录）
                return ShortcutResult(True, data, "archive_hit")
            # subject 不在 archive 中：降级到 API
            return ShortcutResult(False, None, "archive_miss")
        except Exception as e:
            logger.warning(f"bangumi_archive 短路 get_episodes 异常: {e}")
            return ShortcutResult(False, None, "archive_error")

    def try_get_related_subjects(self, subject_id: int) -> ShortcutResult:
        """短路 get_related_subjects

        Returns:
            hit=True 时 data 是 list[dict]（对齐 API 返回结构，含 relation 中文）
            hit=False 时 data 为 None，调用方应走 API

        防止 archive 不完整时静默返回空数据：
        同 try_get_episodes，底层 archive_store.get_related_subjects 在 subject
        不存在时也返回空列表 []。空列表时追加 get_subject 校验存在性，
        避免续集链/前传链查找因 archive 不完整而断链。
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            data = archive_store.get_related_subjects(subject_id)
            # 关联非空：archive 命中
            if data:
                return ShortcutResult(True, data, "archive_hit")
            # 关联为空：需校验 subject 是否在 archive 中
            subject = archive_store.get_subject(subject_id)
            if subject is not None:
                # subject 存在但无关联记录
                return ShortcutResult(True, data, "archive_hit")
            # subject 不在 archive 中：降级到 API
            return ShortcutResult(False, None, "archive_miss")
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

    def try_find_prequel_chain(
        self, subject_id: int, max_hops: int = 30
    ) -> ShortcutResult:
        """短路前传链查找（与 try_find_sequel_chain 对称）

        用于 search_previous_subjects / get_series_subject_ids 的前传方向优化：
        一次拿完整前传链，避免逐跳 API 调用。

        Returns:
            hit=True 时 data 是 list[int]（前传链 subject_id 列表，不含起始）
            hit=False 时 data 为 None
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            chain = archive_store.find_prequel_chain(subject_id, max_hops=max_hops)
            # 判断 subject 是否在 Archive 中存在
            if not chain:
                subject = archive_store.get_subject(subject_id)
                if subject is None:
                    return ShortcutResult(False, None, "archive_miss")
            return ShortcutResult(True, chain, "archive_hit")
        except Exception as e:
            logger.warning(f"bangumi_archive 短路 find_prequel_chain 异常: {e}")
            return ShortcutResult(False, None, "archive_error")

    def try_find_series_closure(
        self, subject_id: int, max_hops: int = 64
    ) -> ShortcutResult:
        """短路续集图 BFS 闭包（双向：续集+前传，含分支）

        用于 get_series_subject_ids_bfs / 场景 J 验证：一次拿完整系列闭包，
        避免逐跳 LIMIT 1 丢失分支型 IP 的兄弟续集/前传。

        Returns:
            hit=True 时 data 是 list[int]（闭包 subject_id 列表，不含起始）
            hit=False 时 data 为 None
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            closure = archive_store.find_series_closure(subject_id, max_hops=max_hops)
            # 空闭包时需判断 Archive 是否含该 subject，以区分「确认无关联」与 miss
            if not closure:
                subject = archive_store.get_subject(subject_id)
                if subject is None:
                    return ShortcutResult(False, None, "archive_miss")
            return ShortcutResult(True, closure, "archive_hit")
        except Exception as e:
            logger.warning(f"bangumi_archive 短路 find_series_closure 异常: {e}")
            return ShortcutResult(False, None, "archive_error")

    def try_find_franchise_closure(
        self, subject_id: int, max_hops: int = 64
    ) -> ShortcutResult:
        """短路同 IP 关系图 BFS 闭包（相同系列/前传/续集/外传/改编/同世界观/劇場版/同系列，含分支）

        用于 get_franchise_subject_ids_bfs / 场景 Q 验证：一次拿完整同 IP 闭包，
        相对 try_find_series_closure（仅 sequel+prequel）多收回一个数量级的兄弟作品
        （高达全系列、CLAMP 宇宙、Cartoon Network 动画宇宙等分支型 IP）。

        Returns:
            hit=True 时 data 是 list[int]（闭包 subject_id 列表，不含起始）
            hit=False 时 data 为 None
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            from ..bangumi_archive._store import FRANCHISE_RELATION_TYPES

            closure = archive_store.find_franchise_closure(
                subject_id,
                relation_types=FRANCHISE_RELATION_TYPES,
                max_hops=max_hops,
            )
            # 空闭包时需判断 Archive 是否含该 subject，以区分「确认无关联」与 miss
            if not closure:
                subject = archive_store.get_subject(subject_id)
                if subject is None:
                    return ShortcutResult(False, None, "archive_miss")
            return ShortcutResult(True, closure, "archive_hit")
        except Exception as e:
            logger.warning(f"bangumi_archive 短路 find_franchise_closure 异常: {e}")
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

        分场景匹配策略（按优先级从最可靠到最不可靠）：
        0. 媒体前缀变体优先（劇場版/剧场版/映画/映画版 + 原始 title）：
           仅当原始 title 不在 archive 中精确命中时触发，避免被部分同名的
           TV 版（如「X Season 2」主段匹配）抢占。
           场景 D：媒体库推送「進撃の巨人 Season2〜覚醒の咆哮〜」（剥离劇場版
           前缀后的核心标题）时，archive 没有「進撃の巨人 Season2〜覚醒の咆哮〜」
           但有「劇場版 進撃の巨人 Season2〜覚醒の咆哮〜」，应优先返回剧场版。
           条件：原始 title 不在 archive 中精确命中，避免对 archive 中已存在的
           name（A 场景 query=archive name）误判为劇場版。
        1. 精确匹配（原始 + 剥离季后缀精确）：is_exact=True 时直接返回
        2. 媒体前缀变体（劇場版/剧场版/映画/映画版 + 剥离后核心标题）：
           仅在精确匹配未命中合格结果时尝试，避免被模糊兜底抢占
           场景：查询「クドわふたー」精确命中同名游戏（type=4）被过滤，
           尝试「劇場版 クドわふたー」精确命中剧场版动画（type=2）
        3. 标题分割（按 : / - / ～ 拆分，用主段精确匹配）：
           场景：查询「魔法少女小圆：叛逆的物语」时 archive 仅存「魔法少女小圆」
        4. 模糊兜底：所有精确策略失败后，用原始标题模糊匹配
           优先级最低，避免公共子串误命中（如查询「進撃の巨人 Season2〜覚醒の咆哮〜」
           命中 TV 版「進撃の巨人」而非期望的剧场版完整同名条目）

        命中后通过 archive_store 拉取完整 subject 数据并按 type/air_date 过滤，
        对齐 API 的 filter 行为（type 默认 [2]，air_date 区间为 [start, end)）。

        索引未就绪时（首次启动或后台构建期间）返回 archive_miss 降级到 API，
        同时懒触发后台构建，避免阻塞调用方。

        Returns:
            hit=True 时 data 是 list[dict]（对齐 API data 字段内容）
            hit=False 时 data 为 None，调用方应走 API
        """
        if not self._enabled:
            return ShortcutResult(False, None, "archive_disabled")
        try:
            # 索引未就绪时降级到 API，并懒触发后台构建
            if not archive_title_index.is_ready:
                archive_title_index.build_in_background()
                return ShortcutResult(False, None, "archive_miss")

            # API 默认 type=[2]（与 search() 一致），None/空列表时也用 [2]
            types_set: set[int] = set(subject_types) if subject_types else {2}
            skip_ids: set[int] = set()

            # G1：从 start_date 抽取首播年份，显式透传给标题精确匹配做年份消歧。
            # 此前 year 仅能从查询字符串自动抽取，媒体库推送带首播 metadata 但
            # 标题不含年份时（如「銀魂」+2006），同名多年版消歧不触发。
            # start_date 形如 "2006-01-01"，无日期/格式不符时 year=None（退化原行为）。
            year: Optional[int] = None
            if start_date:
                m = re.search(r"(?:19|20)\d{2}", start_date)
                if m:
                    year = int(m.group(0))

            # 步骤 0：媒体前缀变体优先尝试（仅当原始 title 不在 archive 中精确命中时）
            # 场景 D：媒体库推送「X」（剥离劇場版前缀后的核心标题）时，archive 中
            # 没有「X」（完整同名），但有「劇場版 X」（剧场版完整同名）。应优先返回
            # 剧场版，避免被部分同名的 TV 版（如「X Season 2」主段匹配）抢占。
            # 条件：原始 title 不在 archive 中精确命中，避免对 archive 中已存在的
            # name（A 场景）误判为劇場版。
            if not any(title.startswith(p) for p in _MEDIA_PREFIX_VARIANTS):
                raw_ids = archive_title_index.find_subject_ids_by_title(
                    title, year=year
                )
                if not raw_ids:  # 原始 title 不在 archive 中精确命中
                    for prefix in _MEDIA_PREFIX_VARIANTS:
                        variant = f"{prefix}{title}"
                        variant_ids = archive_title_index.find_subject_ids_by_title(
                            variant, year=year
                        )
                        if not variant_ids:
                            continue
                        results = archive_store.get_subjects_by_ids_with_filter(
                            variant_ids,
                            types_set,
                            start_date,
                            end_date,
                            limit,
                            skip_ids,
                        )
                        if results:
                            return ShortcutResult(
                                True,
                                results,
                                "archive_hit",
                                MATCH_METHOD_PREFIX_VARIANT,
                            )

            # 步骤 1：精确匹配（原始 + 剥离季后缀）
            ids, is_exact = archive_title_index.find_subject_ids_for_query_title(
                title, year=year
            )
            if ids:
                results = archive_store.get_subjects_by_ids_with_filter(
                    ids, types_set, start_date, end_date, limit, skip_ids
                )
                if results:
                    method = MATCH_METHOD_EXACT if is_exact else MATCH_METHOD_FUZZY
                    return ShortcutResult(True, results, "archive_hit", method)
                # 精确命中但被 type/air_date 过滤，继续尝试其他精确策略
                # 模糊命中时也继续尝试（不直接降级模糊兜底）

            # 步骤 2-4：复用共享变体生成 build_search_variants，按 archive 优先级消费。
            # 与 API 兜底路径（bgm_search）共用同一份候选池，消除两路径变体策略漂移；
            # 优先级保持 archive 语义：媒体前缀变体早试 → 标题分割主段 → 书名号剥离
            # （精确匹配由步骤 1 覆盖，模糊兜底由步骤 5 覆盖）。
            variants = build_search_variants(title)

            stripped = _strip_season_episode_suffix(title)
            base_title = stripped if stripped and stripped != title else title

            def _lookup(q: str) -> Optional[list]:
                if not q or q == title:
                    return None
                v_ids = archive_title_index.find_subject_ids_by_title(q, year=year)
                if not v_ids:
                    return None
                return archive_store.get_subjects_by_ids_with_filter(
                    v_ids, types_set, start_date, end_date, limit, skip_ids
                )

            # 步骤 2：媒体前缀变体（核心标题拼 劇場版/剧场版/映画/映画版）
            if not any(base_title.startswith(p) for p in _MEDIA_PREFIX_VARIANTS):
                expected_prefix = {f"{p}{base_title}" for p in _MEDIA_PREFIX_VARIANTS}
                for v in variants:
                    if v.query in expected_prefix:
                        res = _lookup(v.query)
                        if res:
                            return ShortcutResult(
                                True, res, "archive_hit", MATCH_METHOD_PREFIX_VARIANT
                            )

            # 步骤 3：标题分割主段（主段长度 >= 4）
            split_mains = set()
            for _base in (title, stripped):
                _segs = _split_title_segments(_base)
                if len(_segs) >= 2 and len(_segs[0]) >= 2:
                    split_mains.add(_segs[0])
            for v in variants:
                if any(v.query.startswith(p) for p in _MEDIA_PREFIX_VARIANTS):
                    continue
                if v.query == title:
                    continue
                if v.query not in split_mains:
                    continue
                res = _lookup(v.query)
                if res:
                    return ShortcutResult(True, res, "archive_hit", v.method)

            # 步骤 4：剥离书名号/方括号包裹后再精确匹配
            for v in variants:
                if any(v.query.startswith(p) for p in _MEDIA_PREFIX_VARIANTS):
                    continue
                if v.query == title:
                    continue
                if v.query in split_mains:
                    continue
                res = _lookup(v.query)
                if res:
                    return ShortcutResult(True, res, "archive_hit", v.method)

            # 步骤 5：模糊兜底（最后尝试）
            # 仅在所有精确策略都未命中合格结果时使用
            # 注意：步骤 1 的模糊兜底结果可能在 type 过滤后为空，
            # 此处用原始标题再试一次（air_date 可能不同导致差异）
            if not is_exact and ids:
                # ids 已是模糊结果，跳过重复查询
                pass
            else:
                fuzzy_ids = [
                    sid for sid, _ in archive_title_index.find_subject_ids_fuzzy(title)
                ]
                if fuzzy_ids:
                    results = archive_store.get_subjects_by_ids_with_filter(
                        fuzzy_ids,
                        types_set,
                        start_date,
                        end_date,
                        limit,
                        skip_ids,
                    )
                    if results:
                        return ShortcutResult(
                            True,
                            results,
                            "archive_hit",
                            MATCH_METHOD_FUZZY,
                        )

            return ShortcutResult(False, None, "archive_miss")
        except Exception as e:
            logger.warning(f"bangumi_archive 短路 search 异常: {e}")
            return ShortcutResult(False, None, "archive_error")

    def try_search_old(
        self,
        title: str,
        subject_type: int = 2,
    ) -> ShortcutResult:
        """已弃用：无日期搜索现统一走 try_search（start/end_date 传空字符串）。

        保留方法签名仅为过渡期兼容，内部直接委托 try_search。
        新代码请改用 try_search(title, start_date="", end_date="", subject_types=[subject_type])。
        """
        return self.try_search(
            title,
            start_date="",
            end_date="",
            limit=15,
            subject_types=[subject_type],
        )


# 全局单例
archive_shortcut = ArchiveShortcut()
