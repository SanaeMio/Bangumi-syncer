"""Bangumi 条目关系遍历公共 API（深度结合 Archive 短路）

对标 kookxiang/jellyfin-plugin-bangumi：
- SearchNextSubject            -> search_next_subject
- SearchPreviousSubjects       -> search_previous_subjects
- GetAllAnimeSeriesSubjectIds  -> get_series_subject_ids

所有方法优先走 Archive 短路（离线可用），未命中再降级到 Bangumi 在线 API。
"""

from typing import Optional

from ..bangumi_constants import (
    RELATION_ID_PREQUEL,
    RELATION_ID_SEQUEL,
    RELATIONS,
    SUBJECT_TYPE_ANIME,
    SUBJECT_TYPE_REAL,
)

# 系列遍历最大跳数（对手默认 1024 请求；Archive 为本地库，放宽到 64 跳足够覆盖绝大多数长篇系列）
_SERIES_MAX_HOPS = 64


class RelationMixin:
    # ===== 续集 / 前传单跳 =====
    def search_next_subject(self, subject_id: int) -> Optional[int]:
        """沿 Sequel 关系查找续集（对应对手 SearchNextSubject）

        优先走 Archive 短路，返回第一个有效（动画/三次元）续集 subject_id。
        """
        res = self._archive.try_find_next_sequel_id(subject_id)
        if res.hit:
            return res.data  # 单个 id 或 None
        # 在线降级
        for rel in self.get_related_subjects(subject_id) or []:
            if rel.get("relation") != RELATIONS[RELATION_ID_SEQUEL]:
                continue
            rid = rel.get("id")
            if rid and self._is_anime_or_real(rid):
                return rid
        return None

    def search_previous_subjects(
        self, subject_id: int, max_hops: int = _SERIES_MAX_HOPS
    ) -> list[int]:
        """沿 Prequel 关系回溯前传全集（对应对手 SearchPreviousSubjects）

        Returns:
            前传 subject_id 扁平列表（不含起始，由近到远）
        """
        res = self._archive.try_find_prequel_chain(subject_id, max_hops=max_hops)
        if res.hit:
            return res.data  # 空列表/None 均表示 Archive 已确认结果，不降级到 API
        return self._walk_prequel_flat(subject_id, max_hops=max_hops)

    # ===== 系列全集 =====
    def get_series_subject_ids(
        self, series_id: int, max_hops: int = _SERIES_MAX_HOPS
    ) -> list[int]:
        """收集整个系列全集（前传链 ∪ 续集链，去重）

        对应对手 GetAllAnimeSeriesSubjectIds：BFS 遍历 Sequel/Prequel，
        仅保留动画/三次元。离线（Archive）可用。
        """
        seen: set[int] = set()
        result: list[int] = []

        def _add(sid: int) -> None:
            if sid and sid not in seen and self._is_anime_or_real(sid):
                seen.add(sid)
                result.append(sid)

        _add(series_id)
        for sid in self._collect_chain(series_id, forward=True, max_hops=max_hops):
            _add(sid)
        for sid in self._collect_chain(series_id, forward=False, max_hops=max_hops):
            _add(sid)
        return result

    # ===== 内部 =====
    def _is_anime_or_real(self, subject_id: int) -> bool:
        subj = self.get_subject(subject_id)
        if not subj:
            return False
        return subj.get("type") in (SUBJECT_TYPE_ANIME, SUBJECT_TYPE_REAL)

    def _prequel_one_hop(self, subject_id: int) -> Optional[int]:
        res = self._archive.try_find_related_id_by_relation(
            subject_id, RELATIONS[RELATION_ID_PREQUEL]
        )
        if res.hit and res.data:
            return res.data
        for rel in self.get_related_subjects(subject_id) or []:
            if rel.get("relation") == RELATIONS[RELATION_ID_PREQUEL]:
                rid = rel.get("id")
                if rid and self._is_anime_or_real(rid):
                    return rid
        return None

    def _collect_chain(
        self, subject_id: int, forward: bool, max_hops: int
    ) -> list[int]:
        chain: list[int] = []
        seen: set[int] = {subject_id}
        cur = subject_id
        for _ in range(max_hops):
            nxt = (
                self.search_next_subject(cur) if forward else self._prequel_one_hop(cur)
            )
            if not nxt or nxt in seen:
                break
            chain.append(nxt)
            seen.add(nxt)
            cur = nxt
        return chain

    def _walk_prequel_flat(self, subject_id: int, max_hops: int) -> list[int]:
        chain: list[int] = []
        seen: set[int] = {subject_id}
        cur = subject_id
        for _ in range(max_hops):
            nxt = self._prequel_one_hop(cur)
            if not nxt or nxt in seen:
                break
            chain.append(nxt)
            seen.add(nxt)
            cur = nxt
        return chain
