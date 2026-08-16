"""Bangumi 条目关系遍历公共 API（深度结合 Archive 短路）

对标 kookxiang/jellyfin-plugin-bangumi：
- SearchNextSubject            -> search_next_subject
- SearchPreviousSubjects       -> search_previous_subjects
- GetAllAnimeSeriesSubjectIds  -> get_series_subject_ids

所有方法优先走 Archive 短路（离线可用），未命中再降级到 Bangumi 在线 API。
"""

from collections import deque
from typing import Optional

from ..bangumi_archive._store import FRANCHISE_RELATION_CN_SET
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

    # ===== 系列全集（双向 BFS 闭包） =====
    def get_series_subject_ids_bfs(
        self, seed_id: int, max_hops: int = _SERIES_MAX_HOPS
    ) -> list[int]:
        """续集图 BFS 闭包：从 seed 双向（续集+前传）收集全部可达节点（含分支）

        区别于 get_series_subject_ids（两条单链、每节点 LIMIT 1，分支型 IP 丢
        兄弟续集/前传）：本方法做真正连通分量闭包，收回完整系列所有分支。

        离线优先走 Archive 短路（try_find_series_closure）；Archive 未启用/未命中
        时降级到在线 get_related_subjects 做双向 BFS。返回去重后的 anime/real 节点，
        seed 置于首位。

        Args:
            seed_id: 起始条目
            max_hops: 最大节点数（含 seed），防环与失控

        Returns:
            闭包 subject_id 列表（seed 在前，其余按 BFS 层序）
        """
        # 离线优先：Archive 短路已算出完整双向闭包
        res = self._archive.try_find_series_closure(seed_id, max_hops=max_hops)
        if res.hit:
            closure_raw = list(res.data or [])
        else:
            # Archive 未启用/未命中：在线基于 get_related_subjects 双向 BFS
            closure_raw = self._bfs_closure_online(seed_id, max_hops=max_hops)

        seen: set[int] = {seed_id}
        result: list[int] = [seed_id]
        for sid in closure_raw:
            if sid and sid not in seen and self._is_anime_or_real(sid):
                seen.add(sid)
                result.append(sid)
        return result

    def get_franchise_subject_ids_bfs(
        self, seed_id: int, max_hops: int = _SERIES_MAX_HOPS
    ) -> list[int]:
        """同 IP 关系图 BFS 闭包：从 seed 沿全部「同作品」关系类型（默认
        FRANCHISE_RELATION_TYPES：相同系列/前传/续集/外传/改编/同世界观/劇場版/
        同系列）收集连通分量（含分支）。

        相对 get_series_subject_ids_bfs（仅 sequel+prequel）多收回一个数量级的兄弟
        作品（高达全系列、CLAMP 宇宙、Cartoon Network 动画宇宙等分支型 IP），是媒体
        库按「同一 IP / 系列」分组的更完整召回。

        离线优先走 Archive 短路（try_find_franchise_closure）；Archive 未启用/未命中
        时降级到在线 get_related_subjects 做双向 BFS。返回去重后的 anime/real 节点，
        seed 置于首位。

        Args:
            seed_id: 起始条目
            max_hops: 最大节点数（含 seed），防环与失控

        Returns:
            闭包 subject_id 列表（seed 在前，其余按 BFS 层序）
        """
        # 离线优先：Archive 短路已算出完整同 IP 闭包
        res = self._archive.try_find_franchise_closure(seed_id, max_hops=max_hops)
        if res.hit:
            closure_raw = list(res.data or [])
        else:
            # Archive 未启用/未命中：在线基于 get_related_subjects 双向 BFS
            closure_raw = self._bfs_franchise_closure_online(seed_id, max_hops=max_hops)

        seen: set[int] = {seed_id}
        result: list[int] = [seed_id]
        for sid in closure_raw:
            if sid and sid not in seen and self._is_anime_or_real(sid):
                seen.add(sid)
                result.append(sid)
        return result

    def _bfs_closure_online(
        self, seed_id: int, max_hops: int = _SERIES_MAX_HOPS
    ) -> list[int]:
        """在线降级：基于 get_related_subjects 做双向 BFS 闭包（无 Archive 时）

        Returns:
            原始闭包 subject_id 列表（不含 seed，按 BFS 层序，未做类型过滤）
        """
        seen: set[int] = {seed_id}
        order: list[int] = []
        queue: deque[int] = deque([seed_id])
        while queue and len(seen) <= max_hops:
            cur = queue.popleft()
            if cur != seed_id:
                order.append(cur)
            for rel in self.get_related_subjects(cur) or []:
                rid = rel.get("id")
                if rel.get("relation") in (
                    RELATIONS[RELATION_ID_SEQUEL],
                    RELATIONS[RELATION_ID_PREQUEL],
                ):
                    if rid and rid not in seen:
                        seen.add(rid)
                        queue.append(rid)
        return order

    def _bfs_franchise_closure_online(
        self, seed_id: int, max_hops: int = _SERIES_MAX_HOPS
    ) -> list[int]:
        """在线降级：基于 get_related_subjects 做同 IP 双向 BFS 闭包（无 Archive 时）

        采用 FRANCHISE_RELATION_CN_SET（与 Bangumi 官方 web API 返回的 relation
        中文名对齐），剔除 角色出演/不同世界观/联动/其他 等噪声边。

        注意：FRANCHISE_RELATION_CN_SET 是官方 API 编号体系的中文名，不可与
        FRANCHISE_RELATION_TYPES（库 dump 编号）混用——二者仅 2(前传)/3(续集) 重合，
        直接用 RELATIONS[库编号] 转换会纳入官方 7「角色出演」噪声边且漏掉同 IP 边。

        Returns:
            原始闭包 subject_id 列表（不含 seed，按 BFS 层序，未做类型过滤）
        """
        seen: set[int] = {seed_id}
        order: list[int] = []
        queue: deque[int] = deque([seed_id])
        while queue and len(seen) <= max_hops:
            cur = queue.popleft()
            if cur != seed_id:
                order.append(cur)
            for rel in self.get_related_subjects(cur) or []:
                rid = rel.get("id")
                if rel.get("relation") in FRANCHISE_RELATION_CN_SET:
                    if rid and rid not in seen:
                        seen.add(rid)
                        queue.append(rid)
        return order

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
