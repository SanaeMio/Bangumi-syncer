"""Archive 标题索引（兼容门面，委托给 FTS5 实现）

本模块为历史兼容层，保留原对外接口名：
- ArchiveTitleIndex（类名）
- archive_title_index（全局单例）
- _normalize_key（归一化函数）

实际实现已迁移到 _fts_query.py（基于 SQLite FTS5 trigram），
内存占用从 200-350MB 降到接近 0，查询性能提升 10-100 倍。

迁移说明：
- 原 _title_to_ids / _bigram_index 内存结构已移除
- 原磁盘缓存（.index 文件）已废弃，FTS5 表由 SQLite 自管理
- 原 _build_internal / _save_to_disk / _load_from_disk 已移除
- 原 build_in_background 改为轻量同步初始化（FTS5 表导入时已构建，
  旧库升级时可能触发同步 FTS 构建）

多策略标题匹配（find_subject_ids_for_query_title / _try_main_segment_match）
从 _archive_shortcut.py 下沉至此，让本模块统一管理标题查询能力。
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

from ._fts_query import (
    ArchiveFTSQuery,
    _normalize_key,
)
from ._title_normalize import (
    _MEDIA_PREFIX_VARIANTS,
    _split_title_segments,
    _strip_season_episode_suffix,
)

__all__ = ["ArchiveTitleIndex", "archive_title_index", "_normalize_key"]


class ArchiveTitleIndex(ArchiveFTSQuery):
    """Archive 标题索引（兼容类名，委托给 ArchiveFTSQuery）

    保留此类名仅为兼容现有 import：
        from app.utils.bangumi_archive._title_index import ArchiveTitleIndex

    实际逻辑全部继承自 ArchiveFTSQuery。
    """

    # 兼容原测试访问的内部属性（FTS5 方案下不再有这些结构，
    # 但部分测试直接访问，提供空值避免 AttributeError）
    @property
    def _title_to_ids(self) -> dict[str, list[int]]:
        """已废弃：FTS5 方案下无内存索引"""
        return {}

    @property
    def _bigram_index(self) -> dict[str, list[str]]:
        """已废弃：FTS5 方案下无 bigram 索引"""
        return {}

    # ===== 多策略标题匹配（从 _archive_shortcut.py 下沉） =====

    def find_subject_ids_for_query_title(self, title: str) -> tuple[list[int], bool]:
        """标题 → subject_id 列表 + 是否精确命中

        匹配优先级（从最可靠到最不可靠）：
        1. 剥离季数/集数后缀后的精确匹配（仅当标题含可识别后缀时触发）
           场景 C：媒体库推送「X Season 2」「X 第二季」时，archive 中既有
           「X」（主条目）又有「X Season 2」（衍生条目，如分季汇总）。
           剥离后用核心标题「X」匹配，优先返回主条目，避免被同名的衍生条目屏蔽。
           仅当 stripped != title 时触发，无后缀时跳过此步骤。
        2. 原始标题精确匹配（最直接命中）
           无后缀剥离时的主路径；有后缀剥离但剥离后未命中时的兜底。
        3. 标题分割主段精确匹配
           场景 A：archive 同时存在「X 第N期」(含季数) 和「X」(通用版本)，
           查询「X 第N期：副标题」时剥离 `第N期：副标题` 命中通用版本「X」，
           但主段「X 第N期」更具体，应优先返回主段对应 subject。
           条件（有后缀剥离时）：主段长度 > 剥离后标题长度，避免
           「Crow: The Legend S01E01」剥离后「Crow: The Legend」(17)
           比主段「Crow」(4) 更具体时误用主段。
           场景 B：纯主副标题分隔（无季数后缀），如查询
           「宇宙戦艦ヤマト：叛逆的物语」时 archive 存「宇宙戦艦ヤマト」，
           主段即主标题，应直接精确匹配。
           条件（无后缀剥离时）：主段长度 >= 4，避免过短主段误匹配。
        4. 剥离后模糊匹配（核心标题可能存在细微差异）
        5. 原始标题模糊匹配（最后兜底，可能因公共子串误命中）

        Args:
            title: 查询标题（可能含季数后缀/主副分隔/包裹符）

        Returns:
            (ids, is_exact) 元组：
            - ids: 命中的 subject_id 列表
            - is_exact: True 表示前三个优先级精确命中；
                        False 表示仅模糊命中（兜底），调用方可据此
                        决定是否尝试媒体前缀/标题分割后再回退模糊
        """
        # 1. 原始标题精确匹配
        ids = self.find_subject_ids_by_title(title)

        # 2. 剥离季数/集数后缀后的精确匹配（与原始精确合并，原始优先）
        # 场景 C：媒体库推送「X Season 2」时，archive 中既有「X」（主条目）
        # 又有「X Season 2」（衍生条目）。剥离后用核心标题匹配，主条目
        # 必须在返回列表中，避免被衍生条目屏蔽。
        # 场景 A：archive name 本身含季数（如「X 第2期」），查询同名时
        # ids 含期望条目（X 第2期），stripped_ids 含不同条目（X 主条目）。
        # 必须同时返回两者，否则会丢期望条目（回归 96.5% → 99%+）。
        # 顺序：原始 ids 优先（更具体），stripped_ids 作为补充（去重）。
        # 场景 N：剥离后命中核心标题（如「快乐星猫」），但期望是含季数的
        # 主段（如「快乐星猫第三季」）。尝试主段匹配，主段优先。
        stripped = _strip_season_episode_suffix(title)
        if stripped != title and stripped:
            stripped_ids = self.find_subject_ids_by_title(stripped)
            if stripped_ids:
                # 尝试主段匹配（主段含季数更具体）
                # 场景 N：查询「快乐星猫第三季：叛逆的物语」剥离后命中
                # 「快乐星猫」(116142)，但期望是「快乐星猫第三季」(417287)。
                # 主段「快乐星猫第三季」(6 字) > stripped「快乐星猫」(4 字)，
                # 主段精确匹配命中 417287，优先返回。
                main_ids = self._try_main_segment_match(title, stripped)
                if main_ids:
                    # 主段优先（含季数更具体），stripped_ids 作为补充（去重）
                    seen = set(main_ids)
                    extra = [i for i in stripped_ids if i not in seen]
                    return main_ids + extra, True
                if ids:
                    seen = set(ids)
                    extra = [i for i in stripped_ids if i not in seen]
                    return ids + extra, True
                return stripped_ids, True

        # 3. 主段精确匹配（即使 ids 为空也尝试）
        # 场景 N：查询「Bewitched (Season 2) —后传—」时，剥离后是
        # 「Bewitched (」（不完整，archive 中无此 name），原始精确也未命中，
        # 但主段「Bewitched (Season 2)」精确匹配能直接命中期望 ID 118940。
        # 若跳过主段匹配走模糊匹配，会命中多个 Bewitched Season N，期望 ID
        # 被 limit 截断。所以即使 ids 为空，也要尝试主段匹配。
        # 场景 B（无后缀剥离）：查询「宇宙戦艦ヤマト：叛逆的物语」时
        # 原始精确未命中，主段「宇宙戦艦ヤマト」精确命中多个 subject。
        # 场景 A（有后缀剥离）：archive 有「ふたりエッチ 第1期」(50598) 和
        # 「ふたりエッチ」(102797)，查询「ふたりエッチ 第1期：叛逆的物语」
        # 剥离后命中 102797（通用），但主段「ふたりエッチ 第1期」(11 字) >
        # 剥离后「ふたりエッチ」(6 字)，主段更具体，优先返回 50598。
        # 反例：查询「Crow: The Legend S01E01」剥离后「Crow: The Legend」(17 字)
        # 比主段「Crow」(4 字) 更具体，不应触发主段精确匹配。
        # 场景 C（无后缀剥离 + 媒体前缀变体）：查询
        # 「前橋ウィッチーズ ～魔女見習いのエモエモリーズ～」（剥离劇場版前缀后）
        # 时主段「前橋ウィッチーズ」命中 TV 版，但 archive 中有
        # 「劇場版 前橋ウィッチーズ ～魔女見習いのエモエモリーズ～」，
        # 应优先返回劇場版（更具体）。
        main_ids = self._try_main_segment_match(title, stripped)
        if main_ids:
            if stripped == title and ids:
                # 无后缀剥离 + ids 非空：原始 ids 是完整标题精确匹配，最具体。
                # 仅当存在媒体前缀变体（劇場版/OVA/OAD + 原标题）时
                # 优先返回变体，原始 ids 作为补充（去重）。
                # 场景：查询「前橋ウィッチーズ ～副标题～」时主段命中 TV 版，
                # 但 archive 中有「劇場版 前橋ウィッチーズ ～副标题～」，
                # 应优先返回劇場版（更具体）。
                # 反例（D1）：查询「牙狼〈GARO〉-GOLDSTORM- 翔」时
                # ids=[124627, 124628] 含期望，main_ids=[29864] 不含期望，
                # 直接返回 main_ids 会丢弃期望条目，必须合并 ids。
                variant_ids_list: list[int] = []
                for prefix in _MEDIA_PREFIX_VARIANTS:
                    variant = f"{prefix}{title}"
                    v_ids = self.find_subject_ids_by_title(variant)
                    if v_ids:
                        variant_ids_list.extend(v_ids)
                if variant_ids_list:
                    # 媒体前缀变体优先，原始 ids 作为补充（去重）
                    seen = set(variant_ids_list)
                    fallback = [i for i in ids if i not in seen]
                    return variant_ids_list + fallback, True
                # 无变体：直接返回原始 ids（更具体的完整标题匹配）
                return ids, True
            # 有后缀剥离 或 ids 为空：main_ids 优先，原始 ids 作为补充（去重）
            seen = set(main_ids)
            fallback = [i for i in ids if i not in seen] if ids else []
            return main_ids + fallback, True

        # 4. 原始精确命中兜底
        if ids:
            return ids, True

        # 5. 剥离后模糊匹配
        if stripped != title and stripped:
            fuzzy = self.find_subject_ids_fuzzy(stripped)
            if fuzzy:
                return [sid for sid, _ in fuzzy], False

        # 6. 原始标题模糊匹配（兜底）
        fuzzy = self.find_subject_ids_fuzzy(title)
        return [sid for sid, _ in fuzzy], False

    def _try_main_segment_match(self, title: str, stripped: str) -> list[int]:
        """尝试主段精确匹配，返回主段对应的 subject_id 列表

        条件：
        - 有后缀剥离时（stripped != title）：主段长度 > stripped 长度
          （主段比 stripped 更具体，含季数信息）
          反例：「Crow: The Legend S01E01」剥离后「Crow: The Legend」(17 字)
          比主段「Crow」(4 字) 更具体，不应触发主段匹配。
        - 无后缀剥离时（stripped == title）：主段长度 >= 4
          （纯主副标题分隔，主段足够长避免误匹配）

        Args:
            title: 原始查询标题
            stripped: _strip_season_episode_suffix 剥离后的标题

        Returns:
            主段精确匹配的 subject_id 列表（可能为空）
        """
        segments = _split_title_segments(title)
        if len(segments) < 2:
            return []
        main_segment = segments[0]
        if stripped != title:
            # 有后缀剥离：要求主段比 stripped 更具体
            if len(main_segment) <= len(stripped):
                return []
        else:
            # 无后缀剥离：主段足够长即可
            if len(main_segment) < 4:
                return []
        return self.find_subject_ids_by_title(main_segment)


# 全局单例（保留原名称，指向 FTS5 实现）
archive_title_index: ArchiveTitleIndex = ArchiveTitleIndex()  # type: ignore[assignment]
