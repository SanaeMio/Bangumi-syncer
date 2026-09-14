"""Archive 只读查询接口

职责：
- 按 active 指针连接当前服务的 SQLite 库
- 提供 subject / episode / relations 等只读查询
- 字段映射到 BangumiApi 返回结构（供短路层透明替换）

第二期 A：接入业务读路径作为 Archive 短路的数据源。
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

import json
import sqlite3
import threading
from collections import deque
from pathlib import Path
from typing import Any, Optional

from ...core.logging import logger
from ..bangumi_constants import (
    RELATION_ID_PREQUEL,
    RELATION_ID_SEQUEL,
    RELATIONS,
)
from ._archive import bangumi_archive
from ._wiki_parser import parse_infobox

# 同 IP / 同系列关系图闭包所采用的关系类型集合（库 dump 编号）。
# 数据依据（真实库 a.db 的 subject_relation.relation_type 分布）：
#   1 相同系列 2132 / 2 前传 13249 / 3 续集 13281 / 4 外传 1189 /
#   7 改编(同作者宇宙) 3903 / 8 同世界观 3084 / 9 续集(系列) 1352 /
#   10 劇場版·总集编 6531 / 12 同系列 3864 —— 均属「同一作品/IP 宇宙」边
# 排除噪声边（会把无关条目连进闭包，如 CLANNAD→京都动画粉丝感谢活动）：
#   5 角色出演 1190 / 6 其他 2815 / 11 其他(恶搞/活动) 2794 /
#   14 其他 465 / 99 其他·现实活动 3745
#
# 注意：此处的 relation_type 是 bangumi-data dump 的编号体系，与
# bangumi_constants.RELATIONS（官方 web API 编号体系）不同——两套仅 2(前传)/3(续集) 重合。
# 故离线（find_franchise_closure）直接用本元组按 relation_type IN 查库；
# 在线降级（_bfs_franchise_closure_online）必须用 FRANCHISE_RELATION_CN_SET
# 匹配 Bangumi 官方 API 返回的 relation 中文字段，二者不可混用。
FRANCHISE_RELATION_TYPES = (1, 2, 3, 4, 7, 8, 9, 10, 12)

# 在线降级（Bangumi 官方 web API）对应的「同 IP 宇宙」relation 中文名集合。
# 来源：FRANCHISE_RELATION_TYPES 的库 dump 语义映射到官方 API 的中文名
#   库1 相同系列 → 官方无直接对应，以「主线故事」近似（同一主线作品系列）
#   库2/3 前传/续集 → 官方「前传」「续集」（编号一致）
#   库4 外传 → 官方「番外篇」（官方 6）
#   库7 改编(同作者宇宙) → 官方「改编」（官方 1）
#   库8 同世界观 → 官方「相同世界观」（官方 8）
#   库9 续集(系列) → 官方「续集」（已在集合内）
#   库10 劇場版·总集编 → 官方「总集篇」（官方 4）+「不同演绎」（官方 10，含剧场版改编）
#   库12 同系列 → 官方「主线故事」（官方 12）
# 排除：官方 7「角色出演」/ 9「不同世界观」/ 14「联动」/ 99「其他」/ 5「全集」
FRANCHISE_RELATION_CN_SET = frozenset(
    {
        "前传",
        "续集",
        "改编",
        "番外篇",
        "相同世界观",
        "总集篇",
        "不同演绎",
        "主线故事",
        "衍生",
    }
)


class ArchiveStore:
    """Archive 只读查询接口

    通过 bangumi_archive 单例获取当前 active 库路径。
    使用 sqlite3 连接（check_same_thread=False）+ 线程锁保证线程安全。

    返回数据结构对齐 BangumiApi：
    - get_subject 返回字段与 API subjects/{id} 一致
    - get_episodes 返回字段与 API episodes 一致
    - get_related_subjects 返回 list[dict]，含 relation（中文）/ type 字段
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._connected_path: Optional[Path] = None

    def _get_connection(self) -> Optional[sqlite3.Connection]:
        """获取当前 active 库的连接

        active 指针变化时自动重连。
        库不存在或损坏时返回 None（调用方应降级到 API）。
        """
        with self._lock:
            active_path = bangumi_archive.get_active_db_path()
            if not active_path.exists():
                return None

            # 检测 active 切换：路径变化时重连
            if self._conn is not None and self._connected_path != active_path:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
                self._connected_path = None

            if self._conn is None:
                try:
                    conn = sqlite3.connect(str(active_path), check_same_thread=False)
                    conn.execute("PRAGMA query_only=ON")  # 只读模式
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.row_factory = sqlite3.Row
                    self._conn = conn
                    self._connected_path = active_path
                except sqlite3.Error as e:
                    logger.warning(
                        f"bangumi_archive: 连接 active 库失败 {active_path}: {e}"
                    )
                    return None

            # 简单健康检查（连接断开时重连）
            try:
                self._conn.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
                self._connected_path = None
                return None

            return self._conn

    def close(self) -> None:
        """关闭连接"""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
                self._connected_path = None

    # ===== 查询接口 =====

    def get_subject(
        self, subject_id: int, subject_type: Optional[int] = None
    ) -> Optional[dict[str, Any]]:
        """按 ID 查询条目

        Args:
            subject_id: 条目 ID
            subject_type: 可选过滤（如 SUBJECT_TYPE_ANIME=2），None 不过滤

        Returns:
            dict（对齐 BangumiApi 返回结构）或 None（未命中时）

        字段映射说明：
        - Archive 的 infobox 是原始 wiki 串，与 API 一致
        - tags/score/score_details/meta_tags 在 Archive 中是 JSON 字符串，这里反序列化为 list/dict
        - date 字段对应 API 的 date
        """
        conn = self._get_connection()
        if conn is None:
            return None
        try:
            sql = (
                "SELECT id, type, name, name_cn, infobox, platform, summary, "
                "nsfw, date, favorite, series, tags, score, score_details, "
                "rank, meta_tags FROM subject WHERE id = ?"
            )
            params: tuple = (subject_id,)
            if subject_type is not None:
                sql += " AND type = ?"
                params = (subject_id, subject_type)
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            return self._adapt_subject_row(dict(row))
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive get_subject 失败: {e}")
            return None

    def get_episodes(
        self,
        subject_id: int,
        episode_type: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """查询条目的章节

        Args:
            subject_id: 条目 ID
            episode_type: 可选过滤（如 EPISODE_TYPE_NORMAL=0），None 返回全部

        Returns:
            list[dict]（对齐 BangumiApi episodes 返回结构）
        """
        conn = self._get_connection()
        if conn is None:
            return []
        try:
            sql = (
                "SELECT id, name, name_cn, description, airdate, disc, "
                "duration, subject_id, sort, type FROM episode "
                "WHERE subject_id = ?"
            )
            params: list[Any] = [subject_id]
            if episode_type is not None:
                sql += " AND type = ?"
                params.append(episode_type)
            sql += " ORDER BY sort"
            rows = conn.execute(sql, params).fetchall()
            episodes = [dict(r) for r in rows]
            self._synthesize_ep_field(episodes)
            return [self._adapt_episode_row(e) for e in episodes]
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive get_episodes 失败: {e}")
            return []

    def get_episodes_by_airdate(
        self,
        start_date: str,
        end_date: str,
        subject_types: tuple[int, ...] = (2, 6),
        subject_ids: Optional[set[int]] = None,
    ) -> list[dict[str, Any]]:
        """按 airdate 范围查询放送日程，JOIN subject 取条目名

        用于"番剧放送日历"视图：给定日期范围，返回该范围内所有（或指定
        条目的）剧集，按 airdate 升序排列。

        Args:
            start_date: 起始日期 YYYY-MM-DD（含）
            end_date: 结束日期 YYYY-MM-DD（含）
            subject_types: 条目类型过滤，默认仅动画(2)+三次元(6)
            subject_ids: 非空时仅查询这些 subject 的剧集（用于"仅我在追"）；
                         None 表示不过滤

        Returns:
            list[dict]，每个 dict 含：
            - episode_id, subject_id, subject_name, subject_name_cn,
              subject_type, ep_name, ep_name_cn, ep_sort, airdate
        """
        conn = self._get_connection()
        if conn is None:
            return []
        try:
            type_placeholders = ",".join("?" * len(subject_types))
            sql = (
                "SELECT e.id AS episode_id, e.subject_id AS subject_id, "
                "e.name AS ep_name, e.name_cn AS ep_name_cn, "
                "e.sort AS ep_sort, e.airdate AS airdate, "
                "s.name AS subject_name, s.name_cn AS subject_name_cn, "
                "s.type AS subject_type "
                "FROM episode e "
                "JOIN subject s ON e.subject_id = s.id "
                "WHERE e.airdate BETWEEN ? AND ? "
                "AND e.airdate != '' "
                f"AND s.type IN ({type_placeholders})"
            )
            params: list[Any] = [start_date, end_date, *subject_types]
            if subject_ids is not None:
                if not subject_ids:  # 空集合：无匹配项
                    return []
                placeholders = ",".join("?" * len(subject_ids))
                sql += f" AND e.subject_id IN ({placeholders})"
                params.extend(subject_ids)
            sql += " ORDER BY e.airdate, s.id, e.sort"
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive get_episodes_by_airdate 失败: {e}")
            return []

    def get_related_subjects(self, subject_id: int) -> list[dict[str, Any]]:
        """查询条目的关联条目

        Returns:
            list[dict]，每个 dict 含：
            - id: 关联条目 subject_id（int）
            - relation: 关联类型中文名（str，如「续集」「前传」）
            - type: 关联类型 ID（int，用于精确过滤）
            - order: 排序字段（int）

        对齐 BangumiApi 的 `_find_next_sequel_id` / `_find_related_id_by_relation`
        使用 `item["relation"]` 中文匹配的模式。
        """
        conn = self._get_connection()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                'SELECT subject_id, relation_type, related_subject_id, "order" '
                "FROM subject_relation WHERE subject_id = ? "
                'ORDER BY "order"',
                (subject_id,),
            ).fetchall()
            return [self._adapt_relation_row(dict(r)) for r in rows]
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive get_related_subjects 失败: {e}")
            return []

    def find_related_by_relation(self, subject_id: int, relation_id: int) -> list[int]:
        """按关联类型 ID 查询关联条目

        Args:
            subject_id: 起始条目
            relation_id: bangumi_constants.RELATION_ID_*（如 RELATION_ID_SEQUEL=3）

        Returns:
            关联条目 ID 列表（按 order 排序）
        """
        conn = self._get_connection()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT related_subject_id FROM subject_relation "
                "WHERE subject_id = ? AND relation_type = ? "
                'ORDER BY "order"',
                (subject_id, relation_id),
            ).fetchall()
            return [r[0] for r in rows]
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive find_related_by_relation 失败: {e}")
            return []

    def find_related_by_relations(
        self, subject_id: int, relation_types: tuple[int, ...]
    ) -> list[int]:
        """按多个关联类型 ID 一次性查询关联条目（同 IP 闭包用）

        Args:
            subject_id: 起始条目
            relation_types: relation_type 元组（如 FRANCHISE_RELATION_TYPES）

        Returns:
            关联条目 ID 列表（按 order 排序）
        """
        if not relation_types:
            return []
        conn = self._get_connection()
        if conn is None:
            return []
        try:
            placeholders = ",".join("?" * len(relation_types))
            rows = conn.execute(
                f"SELECT related_subject_id FROM subject_relation "
                f"WHERE subject_id = ? AND relation_type IN ({placeholders}) "
                f'ORDER BY "order"',
                (subject_id, *relation_types),
            ).fetchall()
            return [r[0] for r in rows]
        except sqlite3.Error as e:
            logger.warning(f"bangumi_archive find_related_by_relations 失败: {e}")
            return []

    def find_sequel_chain(self, subject_id: int, max_hops: int = 30) -> list[int]:
        """预构图：沿续集链获取所有续作 subject_id

        Args:
            subject_id: 起始条目
            max_hops: 最大跳数（防环）

        Returns:
            续集链 subject_id 列表（不含起始条目）
        """
        conn = self._get_connection()
        if conn is None:
            return []
        # 使用 RELATION_ID_SEQUEL=3 常量（修复之前 relation_type=1 的 bug）
        visited: set[int] = {subject_id}
        chain: list[int] = []
        current = subject_id
        for _ in range(max_hops):
            try:
                row = conn.execute(
                    "SELECT related_subject_id FROM subject_relation "
                    "WHERE subject_id = ? AND relation_type = ? "
                    'ORDER BY "order" LIMIT 1',
                    (current, RELATION_ID_SEQUEL),
                ).fetchone()
            except sqlite3.Error as e:
                logger.warning(f"bangumi_archive find_sequel_chain 失败: {e}")
                break
            if row is None:
                break
            next_id = row[0]
            if next_id in visited:
                break
            visited.add(next_id)
            chain.append(next_id)
            current = next_id
        return chain

    def find_prequel_chain(self, subject_id: int, max_hops: int = 30) -> list[int]:
        """预构图：沿前传链获取所有前作 subject_id

        与 find_sequel_chain 对称，用于向回追溯前传（季数递减方向）。
        """
        from ..bangumi_constants import RELATION_ID_PREQUEL

        conn = self._get_connection()
        if conn is None:
            return []
        visited: set[int] = {subject_id}
        chain: list[int] = []
        current = subject_id
        for _ in range(max_hops):
            try:
                row = conn.execute(
                    "SELECT related_subject_id FROM subject_relation "
                    "WHERE subject_id = ? AND relation_type = ? "
                    'ORDER BY "order" LIMIT 1',
                    (current, RELATION_ID_PREQUEL),
                ).fetchone()
            except sqlite3.Error as e:
                logger.warning(f"bangumi_archive find_prequel_chain 失败: {e}")
                break
            if row is None:
                break
            next_id = row[0]
            if next_id in visited:
                break
            visited.add(next_id)
            chain.append(next_id)
            current = next_id
        return chain

    def find_series_closure(self, subject_id: int, max_hops: int = 64) -> list[int]:
        """续集图 BFS 闭包：从 subject_id 出发沿续集+前传双向收集全部可达节点（含分支）

        与 find_sequel_chain / find_prequel_chain（单链、每节点 LIMIT 1）不同，
        本方法对每个节点取【全部】续集/前传边做连通分量闭包，不丢失分支型 IP
        的兄弟续集/前传。

        Args:
            subject_id: 起始条目
            max_hops: 最大节点数（含起始），防环与失控

        Returns:
            闭包 subject_id 列表（不含起始 subject_id，按 BFS 层序）
        """
        if self._get_connection() is None:
            return []
        seen: set[int] = {subject_id}
        result: list[int] = []
        queue: deque[int] = deque([subject_id])
        while queue and len(seen) <= max_hops:
            cur = queue.popleft()
            if cur != subject_id:
                result.append(cur)
            # 合并续集+前传为单次 IN 查询，避免 N+1（与 find_franchise_closure 对称）
            nbr_ids = self.find_related_by_relations(
                cur, (RELATION_ID_SEQUEL, RELATION_ID_PREQUEL)
            )
            for rid in nbr_ids:
                if rid and rid not in seen:
                    seen.add(rid)
                    queue.append(rid)
        return result

    def find_franchise_closure(
        self,
        subject_id: int,
        relation_types: tuple[int, ...] = FRANCHISE_RELATION_TYPES,
        max_hops: int = 64,
    ) -> list[int]:
        """同 IP / 同系列关系图 BFS 闭包：从 subject_id 出发沿全部「同作品」关系
        类型（默认 FRANCHISE_RELATION_TYPES，含 相同系列/前传/续集/外传/改编/
        同世界观/劇場版/同系列）双向收集连通分量（含分支）。

        与 find_series_closure（仅 sequel+prequel）相比，额外并入 相同系列/外传/
        改编/同世界观/劇場版/同系列 等关系，对分支型 IP（如高达全系列、CLAMP 宇宙、
        Cartoon Network 动画宇宙）能多收回一个数量级的兄弟作品。已剔除 角色出演/
        其他/活动 等噪声边。

        Args:
            subject_id: 起始条目
            relation_types: 采用的关系类型集合（默认同 IP 集合，库 dump 编号）
            max_hops: 最大节点数（含起始），防环与失控，与 find_series_closure
                语义对齐。同 IP 宇宙可能很宽，调用方需要更多兄弟作品时可显式传
                更大值（如 4096）；默认 64 覆盖绝大多数系列。

        Returns:
            闭包 subject_id 列表（不含起始 subject_id，按 BFS 层序）
        """
        if self._get_connection() is None:
            return []
        if not relation_types:
            return []
        seen: set[int] = {subject_id}
        result: list[int] = []
        queue: deque[int] = deque([subject_id])
        while queue and len(seen) <= max_hops:
            cur = queue.popleft()
            if cur != subject_id:
                result.append(cur)
            nbr_ids = self.find_related_by_relations(cur, relation_types)
            for rid in nbr_ids:
                if rid and rid not in seen:
                    seen.add(rid)
                    queue.append(rid)
        return result

    def count_rows(self, table_name: str) -> int:
        """查询表行数（供状态展示）"""
        conn = self._get_connection()
        if conn is None:
            return 0
        try:
            row = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()
            return row[0] if row else 0
        except sqlite3.Error:
            return 0

    # ===== 批量查询（供 ArchiveShortcut 多策略匹配使用） =====

    # try_search 遍历 ids 上限
    # 避免精确命中大量同名 subject（如 infobox 脏数据「台版|」归一化后
    # 「台版」命中上千条目）时，逐条调用 archive_store.get_subject 拖慢查询
    # 至数秒（极端场景实测 4972ms）。正常场景 limit=5，遍历 200 条已足够
    # 找到 5 条合格结果；若 200 条都被 type/air_date 过滤掉，说明匹配质量
    # 极低，停止遍历不影响实际命中率。
    MAX_IDS_TO_FETCH = 200

    def get_subjects_by_ids_with_filter(
        self,
        ids: list[int],
        types_set: set[int],
        start_date: str,
        end_date: str,
        limit: int,
        skip_ids: Optional[set[int]] = None,
    ) -> list[dict[str, Any]]:
        """遍历 ids 拉取 subject + 按 type/air_date 过滤

        Args:
            ids: 待遍历的 subject_id 列表
            types_set: 允许的 type 集合
            start_date/end_date: air_date 区间过滤 [start, end)
            limit: 最大返回数
            skip_ids: 已遍历过的 subject_id（避免降级时重复遍历）

        遍历上限 MAX_IDS_TO_FETCH 防止极端场景拖慢查询。
        """
        results: list[dict[str, Any]] = []
        # 注意：必须用 `is not None` 而非 `or`，因为空 set 是 falsy，
        # `skip_ids or set()` 会创建新 set 导致跨步骤 skip_ids 共享失效，
        # 后续步骤重复遍历已处理 id，调用方期望 skip_ids 被填充用于共享
        skip = skip_ids if skip_ids is not None else set()
        for idx, sid in enumerate(ids):
            if idx >= self.MAX_IDS_TO_FETCH:
                logger.warning(
                    f"bangumi_archive 遍历 ids 达上限 "
                    f"{self.MAX_IDS_TO_FETCH}（共 {len(ids)} 个），停止遍历"
                )
                break
            if sid in skip:
                continue
            skip.add(sid)
            subject = self.get_subject(sid)
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
        return results

    # ===== 字段适配（Archive → BangumiApi 返回结构） =====

    @staticmethod
    def _adapt_subject_row(row: dict[str, Any]) -> dict[str, Any]:
        """将 Archive subject 行适配为 BangumiApi 返回结构

        关键差异：
        - tags/score/score_details/meta_tags 在 Archive 中是 JSON 字符串，反序列化为 list/dict
        - infobox 在 Archive 中是原始 wiki 串（如 {{Infobox|key=value}}），
          这里通过 wiki_parser 解析为 API 兼容的 list[dict] 格式；
          解析失败时回退为空列表（与 API 返回空 infobox 行为一致）
        """
        for json_field in ("tags", "score", "score_details", "meta_tags"):
            val = row.get(json_field)
            if isinstance(val, str) and val:
                try:
                    row[json_field] = json.loads(val)
                except (ValueError, TypeError):
                    # 保留原始字符串，不破坏数据
                    pass

        # infobox: 原始 wiki 串 → API 兼容的 list[dict]
        infobox_raw = row.get("infobox")
        if isinstance(infobox_raw, str):
            if infobox_raw:
                parsed = parse_infobox(infobox_raw)
                # 解析失败（非 Infobox 模板/格式异常）时回退为空列表，
                # 与 BangumiApi 在 infobox 字段为空时返回 [] 的行为对齐
                row["infobox"] = parsed if parsed else []
            else:
                # 空字符串视为空 infobox
                row["infobox"] = []
        elif infobox_raw is None:
            # None 视为空 infobox
            row["infobox"] = []
        # 已是 list/dict 的异常情况保持原样

        return row

    @staticmethod
    def _adapt_episode_row(row: dict[str, Any]) -> dict[str, Any]:
        """将 Archive episode 行适配为 BangumiApi 返回结构

        Archive 的字段名与 API 一致，仅 airdate 对应 API 的 airdate。
        """
        return row

    @staticmethod
    def _synthesize_ep_field(episodes: list[dict[str, Any]]) -> None:
        """为 Archive episode 补全季内话数 ep 字段

        Archive 按全局连续 sort 存储，不提供 API 中的季内集编号 ep。
        下游匹配层（episodes.py）以 ep 作为季内话数，需在数据边界处补全：
        取 type=0 的常规话，按 sort 升序给出 1-based 季内位置作为 ep。

        同一 subject 内 sort 每季重置为 1 时（多季合并到同一条目），季边界无法由
        sort 推得，此时不补全，交由下游 episodes.py 依据 sort 重置（大于 1 跳回 1
        判定新季起点）定位季边界。
        """
        type0 = [e for e in episodes if e.get("type", 0) == 0]

        # 按 id 升序遍历（反映章节录入顺序），sort 由大于 1 跳回 1 即为新季起点
        prev_sort = None
        for e in sorted(type0, key=lambda e: e.get("id") or 0):
            sort_num = e.get("sort") or 0
            if prev_sort is not None and sort_num == 1 and prev_sort > 1:
                return
            prev_sort = sort_num

        for i, e in enumerate(sorted(type0, key=lambda e: e.get("sort") or 0), start=1):
            e["ep"] = i

    @staticmethod
    def _adapt_relation_row(row: dict[str, Any]) -> dict[str, Any]:
        """将 Archive relation 行适配为 BangumiApi 返回结构

        Archive 字段：subject_id, relation_type(int), related_subject_id, order
        API 字段：id, relation(中文), type(int)

        映射规则：
        - id ← related_subject_id（关联的目标条目 ID）
        - relation ← RELATIONS[relation_type] 中文名
        - type ← relation_type（保留 int 供精确过滤）
        - order ← order
        """
        relation_type = row.get("relation_type")
        return {
            "id": row.get("related_subject_id"),
            "relation": RELATIONS.get(relation_type, "其他")
            if relation_type is not None
            else "其他",
            "type": relation_type,
            "order": row.get("order", 0),
        }


# 全局单例
archive_store = ArchiveStore()
