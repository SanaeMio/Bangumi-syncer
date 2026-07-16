"""待确认候选条目仓库

当匹配失败但存在候选时，将候选沉淀到 pending_candidates 表，
供用户在 WebUI 手动确认后写入自定义映射。
"""

import json
from datetime import datetime
from typing import Any, Optional

from .base_repository import BaseRepository


class PendingCandidatesRepository(BaseRepository):
    """待确认候选的增删改查"""

    def log_pending_candidate(
        self,
        request_title: str,
        request_ori_title: str = "",
        request_season: int = 1,
        request_episode: int = 0,
        user_name: str = "",
        source: str = "",
        candidates: Optional[list[dict[str, Any]]] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> Optional[int]:
        """沉淀一条待确认候选，返回新记录 id（失败时 None）"""

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cand_json = (
                json.dumps(candidates, ensure_ascii=False) if candidates else "[]"
            )
            trace_json = json.dumps(trace, ensure_ascii=False) if trace else "{}"
            cursor = conn.execute(
                """
                INSERT INTO pending_candidates
                (created_at, request_title, request_ori_title, request_season,
                 request_episode, user_name, source, candidates_json, trace_json,
                 status, confirmed_subject_id, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', NULL)
                """,
                (
                    local_time,
                    request_title,
                    request_ori_title,
                    request_season,
                    request_episode,
                    user_name,
                    source,
                    cand_json,
                    trace_json,
                ),
            )
            return cursor.lastrowid

        return self._run_write(_write, error_msg="沉淀待确认候选失败", default=None)

    def get_pending_candidates(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> dict[str, Any]:
        """获取待确认候选列表，返回 {records, total, limit, offset}"""

        def _read(conn):
            where_conditions = []
            params: list[Any] = []
            if status:
                where_conditions.append("status = ?")
                params.append(status)
            where_clause = (
                f"WHERE {' AND '.join(where_conditions)}" if where_conditions else ""
            )

            cursor = conn.execute(
                f"SELECT COUNT(*) AS total FROM pending_candidates {where_clause}",
                params,
            )
            total = cursor.fetchone()[0]

            cursor = conn.execute(
                f"""
                SELECT id, created_at, request_title, request_ori_title,
                       request_season, request_episode, user_name, source,
                       candidates_json, status, confirmed_subject_id, resolved_at
                FROM pending_candidates
                {where_clause}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            )
            cols = [d[0] for d in cursor.description]
            records = [dict(zip(cols, row)) for row in cursor.fetchall()]
            return {
                "records": records,
                "total": total,
                "limit": limit,
                "offset": offset,
            }

        return self._run_read(
            _read,
            error_msg="获取待确认候选失败",
            default={"records": [], "total": 0, "limit": limit, "offset": offset},
        )

    def get_pending_candidate_by_id(
        self, candidate_id: int
    ) -> Optional[dict[str, Any]]:
        """获取单条待确认候选详情（含 trace_json）"""

        def _read(conn):
            cursor = conn.execute(
                """
                SELECT id, created_at, request_title, request_ori_title,
                       request_season, request_episode, user_name, source,
                       candidates_json, trace_json, status, confirmed_subject_id,
                       resolved_at
                FROM pending_candidates WHERE id = ?
                """,
                (candidate_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))

        return self._run_read(_read, error_msg="获取待确认候选详情失败", default=None)

    def update_pending_candidate_status(
        self,
        candidate_id: int,
        status: str,
        confirmed_subject_id: str = "",
    ) -> bool:
        """更新候选状态（confirmed/rejected），记录确认的 subject_id 与时间"""

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor = conn.execute(
                """
                UPDATE pending_candidates
                SET status = ?, confirmed_subject_id = ?, resolved_at = ?
                WHERE id = ?
                """,
                (status, confirmed_subject_id, local_time, candidate_id),
            )
            return cursor.rowcount > 0

        return self._run_write(
            _write, error_msg="更新待确认候选状态失败", default=False
        )

    def delete_pending_candidate(self, candidate_id: int) -> bool:
        """删除一条待确认候选"""

        def _write(conn):
            cursor = conn.execute(
                "DELETE FROM pending_candidates WHERE id = ?",
                (candidate_id,),
            )
            return cursor.rowcount > 0

        return self._run_write(_write, error_msg="删除待确认候选失败", default=False)
