"""待确认候选条目仓库

当匹配失败但存在候选时，将候选沉淀到 pending_candidates 表，
供用户在 WebUI 手动确认后写入自定义映射。
"""

import json
from datetime import datetime
from typing import Any

from ..logging import logger
from .agent_runs import apply_cancelled, apply_succeeded
from .base_repository import BaseRepository

# candidates_json 中 llm 建议条目的来源标记
_LLM_CANDIDATE_SOURCE = "llm_assist"

# 候选已被用户处理（恢复续跑/迟到提交竞态）时 run 的取消原因
_STOP_REASON_USER_RESOLVED = "user_resolved"


def _merge_llm_candidate(existing: list, new_cand: dict) -> None:
    """将 llm 建议并入候选列表（原地修改）。

    保持「同 subject_id 不重复追加」去重语义；但 candidates_json 是唯一真相源，
    命中去重时仍需把 ``source='llm_assist'`` 与最新 ``reason`` 写回既有条目，
    否则读取投影（``_project_llm_fields``）会丢失本次 LLM 建议。
    """
    sid = str(new_cand.get("subject_id"))
    for cand in reversed(existing):
        if isinstance(cand, dict) and str(cand.get("subject_id")) == sid:
            cand["source"] = "llm_assist"
            cand["reason"] = new_cand.get("reason", "")
            return
    existing.append(new_cand)


def _project_llm_fields(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """以 candidates_json 为唯一真相源，投影 llm_subject_id / llm_reason。

    规则：
    - candidates_json 非法 JSON / 非 list → 原样返回（保留列值，兼容旧数据）
    - 倒序找第一条 ``source == "llm_assist"`` 的条目；无则保留列值（旧数据回退）
    - ``llm_subject_id`` 取该条目 ``subject_id``（为空回退列值）
    - ``llm_reason`` 取该条目 ``reason``（缺失/为空回退列值）

    原地更新 record 并返回，便于在读取路径链式调用。
    """
    if record is None:
        return record
    raw = record.get("candidates_json")
    try:
        parsed = json.loads(raw) if raw else []
    except (ValueError, TypeError) as e:
        # 脏数据不应中断读取，但必须可观测（保留列值）
        logger.warning(
            f"[pending_candidates] candidates_json 解析失败（保留列值）: {e}"
        )
        return record
    if not isinstance(parsed, list):
        logger.info(
            f"[pending_candidates] candidates_json 非列表"
            f"（类型={type(parsed).__name__}，保留列值）"
        )
        return record
    llm_entry = next(
        (
            c
            for c in reversed(parsed)
            if isinstance(c, dict) and c.get("source") == _LLM_CANDIDATE_SOURCE
        ),
        None,
    )
    if llm_entry is None:
        return record
    subject_id = llm_entry.get("subject_id")
    if subject_id not in (None, ""):
        record["llm_subject_id"] = str(subject_id)
    reason = llm_entry.get("reason")
    if reason not in (None, ""):
        record["llm_reason"] = str(reason)
    return record


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
        candidates: list[dict[str, Any]] | None = None,
        trace: dict[str, Any] | None = None,
        sync_record_id: int | None = None,
        business_key: str = "",
    ) -> int | None:
        """沉淀一条待确认候选，返回记录 id（失败时 None）。

        去重策略：
        - business_key 非空时，按 (business_key, status='pending') 查询/更新，
          business_key 对齐 agent_runs 业务身份：(user_name, normalize(title), season)，
          跨 source 共享同一 pending 行。
        - business_key 为空时，保留旧 4 元组 (request_title, request_season, user_name, source)
          去重行为（向后兼容既有测试与老数据）。

        sync_record_id：关联的 sync_records 行 id，用于候选确认后回写原记录状态。
        去重 UPDATE 时也会刷新为最新值（同一标题多次失败时以最新 sync_record 为准）。
        """

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cand_json = (
                json.dumps(candidates, ensure_ascii=False) if candidates else "[]"
            )
            trace_json = json.dumps(trace, ensure_ascii=False) if trace else "{}"

            if business_key:
                # 按 business_key 去重（跨 source 共享）
                cursor = conn.execute(
                    """
                    SELECT id FROM pending_candidates
                    WHERE business_key = ? AND status = 'pending'
                    """,
                    (business_key,),
                )
            else:
                # business_key 为空：保留旧 4 元组去重
                cursor = conn.execute(
                    """
                    SELECT id FROM pending_candidates
                    WHERE request_title = ? AND request_season = ?
                      AND user_name = ? AND source = ? AND status = 'pending'
                    """,
                    (request_title, request_season, user_name, source),
                )
            row = cursor.fetchone()

            if row:
                # 已有 pending 行，更新候选和 trace（刷新时间）
                existing_id = row[0]
                conn.execute(
                    """
                    UPDATE pending_candidates
                    SET created_at = ?, request_ori_title = ?, request_episode = ?,
                        candidates_json = ?, trace_json = ?,
                        confirmed_subject_id = '', resolved_at = NULL,
                        sync_record_id = ?
                    WHERE id = ?
                    """,
                    (
                        local_time,
                        request_ori_title,
                        request_episode,
                        cand_json,
                        trace_json,
                        sync_record_id,
                        existing_id,
                    ),
                )
                return existing_id

            # 无 pending 行，插入新行
            cursor = conn.execute(
                """
                INSERT INTO pending_candidates
                (created_at, request_title, request_ori_title, request_season,
                 request_episode, user_name, source, candidates_json, trace_json,
                 status, confirmed_subject_id, resolved_at, sync_record_id,
                 business_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', NULL, ?, ?)
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
                    sync_record_id,
                    business_key,
                ),
            )
            return cursor.lastrowid

        return self._run_write(_write, error_msg="沉淀待确认候选失败", default=None)

    def persist_llm_suggestion(
        self,
        *,
        run_id: str,
        sync_record_id: int | None,
        sync_record: dict,
        business_key: str,
        subject_id: str,
        reason: str,
        stop_reason: str,
        total_tokens: int = 0,
        bgm_title: str = "",
    ) -> int | None:
        """在单一事务内：写 pending_candidates（candidates_json 唯一写入源）+ 置 succeeded。

        （由 ``llm_assist._persist_llm_candidate`` 下沉而来，语义逐字保留；
        服务层仅保留薄封装。）

        ``candidates_json`` 追加/复用含 ``source='llm_assist'`` 与 ``reason`` 的条目；
        ``llm_subject_id`` / ``llm_reason`` 两列不再写入（读取时由仓储层投影）。

        ``business_key`` 为**必填**业务身份键（``build_match_business_key`` 口径），
        与 ``enqueue_match_run`` / ``log_pending_candidate`` 一致：新建行必须写入该列，
        否则 ``_decide_reuse_from_candidate`` 按 business_key 查询永远 miss，导致同一剧集
        反复重跑 LLM + 重复通知。既有行若为历史空键则在同一事务内回填。

        ``bgm_title`` 必须在事务外预取（见 ``llm_assist._prefetch_bgm_name``），
        事务内只做纯 DB 操作（将外部 HTTP 调用移出事务，保持原子性语义不变）。

        **竞态守卫**：若同 sync_record 的既有候选已被用户处理
        （status != 'pending'），或带守卫 UPDATE 时被并发处理（rowcount=0），
        则不复活该行，改为把本 run 标 ``cancelled``（``stop_reason='user_resolved'``）
        并返回 ``None``（跳过信号，调用方不得发送通知）。

        返回 pending_candidates 行 id（正常路径）；跳过时返回 ``None``。
        异常时整体回滚（``_run_write(reraise=True)``：锁层 rollback 后向上抛）。
        """

        def _write(conn):
            # 取既有行：按 id 倒序取最新一条（不区分状态；历史注释曾称「优先 pending」，
            # 但实现从未按状态过滤）
            row = conn.execute(
                "SELECT id, candidates_json, status, business_key FROM pending_candidates "
                "WHERE sync_record_id=? ORDER BY id DESC LIMIT 1",
                (sync_record_id,),
            ).fetchone()

            if row is not None and row[2] != "pending":
                # 用户已处理（confirmed/rejected）→ 不复活候选，run 标 cancelled 并跳过。
                # 事务内直调 apply_cancelled（单一 SQL 入口，不二次取锁），
                # SQL 与源状态守卫与 mark_cancelled 完全一致。
                apply_cancelled(conn, run_id, stop_reason=_STOP_REASON_USER_RESOLVED)
                logger.info(
                    f"[llm_assist] run {run_id} 候选(pending_candidates.id={row[0]}) "
                    f"状态={row[2]} 已被用户处理，跳过落库并标 run cancelled"
                )
                return None

            name = bgm_title

            new_cand = {
                "subject_id": subject_id,
                "name": name,
                "name_cn": name,
                "score": 1.0,
                "source": "llm_assist",
                "reason": reason,
            }

            if row:
                existing_id = row[0]
                existing_business_key = row[3] or ""
                try:
                    existing = json.loads(row[1]) if row[1] else []
                except (ValueError, TypeError):
                    existing = []
                if not isinstance(existing, list):
                    existing = []
                _merge_llm_candidate(existing, new_cand)
                # CAS 乐观锁：除状态守卫外，追加**内容型**比对（SELECT 时的原始
                # candidates_json），把幂等的 ``SET status='pending' WHERE
                # status='pending'`` 升级为变更型 CAS。多进程下两个进程各自 SELECT
                # 到同一旧值、各自 UPDATE 时，只有先提交者内容匹配，后提交者因
                # candidates_json 已变而 rowcount=0 → 不双写、不双通知。
                # COALESCE 兼容 NULL（历史行）/空串：SELECT 侧原始值统一由
                # ``row[1] or ""`` 归一为空串，两侧口径一致。
                original_json = row[1] or ""
                # 历史行（无业务键）在同一事务内回填：否则后续 enqueue 复用判定
                # 按 business_key 查询永远 miss，导致重复重跑 LLM。已在展示的候选行
                # 不覆盖既有非空键（保持原身份，避免改写已被引用的业务身份）。
                if not existing_business_key and business_key:
                    cursor = conn.execute(
                        "UPDATE pending_candidates SET candidates_json=?, status='pending', "
                        "business_key=? WHERE id=? AND status='pending' "
                        "AND COALESCE(candidates_json, '') = ?",
                        (
                            json.dumps(existing, ensure_ascii=False),
                            business_key,
                            existing_id,
                            original_json,
                        ),
                    )
                else:
                    cursor = conn.execute(
                        "UPDATE pending_candidates SET candidates_json=?, status='pending' "
                        "WHERE id=? AND status='pending' "
                        "AND COALESCE(candidates_json, '') = ?",
                        (
                            json.dumps(existing, ensure_ascii=False),
                            existing_id,
                            original_json,
                        ),
                    )
                if cursor.rowcount == 0:
                    # SELECT 后行被并发处理或内容被并发修改：带守卫 + 内容 CAS 的
                    # UPDATE 未命中，同样不复活。事务内直调 apply_cancelled
                    # （单一 SQL 入口，不二次取锁）。
                    apply_cancelled(
                        conn, run_id, stop_reason=_STOP_REASON_USER_RESOLVED
                    )
                    logger.info(
                        f"[llm_assist] run {run_id} 候选(pending_candidates.id="
                        f"{existing_id}) 在落库瞬间状态变更/内容被并发修改，"
                        f"跳过落库并标 run cancelled"
                    )
                    return None
                candidate_id = existing_id
            else:
                # 无候选场景：新建行，candidates_json 仅含 LLM 推荐
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cur = conn.execute(
                    """
                    INSERT INTO pending_candidates
                    (created_at, request_title, request_ori_title, request_season,
                     request_episode, user_name, source, candidates_json, trace_json,
                     status, confirmed_subject_id, resolved_at, sync_record_id,
                     business_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', NULL, ?, ?)
                    """,
                    (
                        now,
                        sync_record.get("title", ""),
                        sync_record.get("ori_title") or "",
                        int(sync_record.get("season", 1) or 1),
                        int(sync_record.get("episode", 0) or 0),
                        sync_record.get("user_name", ""),
                        sync_record.get("source", ""),
                        json.dumps([new_cand], ensure_ascii=False),
                        "{}",
                        sync_record_id,
                        business_key,
                    ),
                )
                candidate_id = cur.lastrowid

            # 同一事务内更新 agent_runs 为 succeeded（原子）
            # ended_at 使用 epoch 秒整数，与 mark_succeeded 一致。
            # **源状态守卫**：仅 pending/processing 活性态可转 succeeded；
            # run 已被并发路径终态化（cancelled/failed/...）时命中 0 行，
            # 不翻回 succeeded（否则通知与 DB 终态矛盾），跳过通知但保留候选写入。
            if not apply_succeeded(
                conn,
                run_id,
                stop_reason=stop_reason,
                total_tokens=total_tokens,
            ):
                # 并发终态化：读当前状态供日志定位（best-effort，读失败不遮蔽主流程）
                try:
                    current = conn.execute(
                        "SELECT status FROM agent_runs WHERE run_id=?", (run_id,)
                    ).fetchone()
                    current_status = current[0] if current else "missing"
                except Exception as e:  # 读状态失败仅降级日志，不影响返回契约
                    current_status = "unknown"
                    logger.warning(
                        f"[llm_assist] run {run_id} succeeded 守卫命中 0 行后"
                        f"读当前状态失败: {e}"
                    )
                logger.warning(
                    f"[llm_assist] run {run_id} succeeded 守卫命中 0 行"
                    f"（当前状态={current_status}），跳过通知"
                )
                return None
            return candidate_id

        return self._run_write(
            _write,
            error_msg="[llm_assist] 候选落库失败（已回滚）",
            reraise=True,
        )

    def get_pending_candidates(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
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
                       candidates_json, status, confirmed_subject_id, resolved_at,
                       sync_record_id, llm_subject_id, llm_reason
                FROM pending_candidates
                {where_clause}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            )
            cols = [d[0] for d in cursor.description]
            records = [
                _project_llm_fields(dict(zip(cols, row, strict=True)))
                for row in cursor.fetchall()
            ]
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

    def get_pending_candidate_by_id(self, candidate_id: int) -> dict[str, Any] | None:
        """获取单条待确认候选详情（含 trace_json）"""

        def _read(conn):
            cursor = conn.execute(
                """
                SELECT id, created_at, request_title, request_ori_title,
                       request_season, request_episode, user_name, source,
                       candidates_json, trace_json, status, confirmed_subject_id,
                       resolved_at, sync_record_id, llm_subject_id, llm_reason
                FROM pending_candidates WHERE id = ?
                """,
                (candidate_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return _project_llm_fields(dict(zip(cols, row, strict=True)))

        return self._run_read(_read, error_msg="获取待确认候选详情失败", default=None)

    def get_pending_candidate_by_sync_record_id(
        self, sync_record_id: int
    ) -> dict[str, Any] | None:
        """按 sync_record_id 查询最新候选记录（含 trace_json）

        用于 records 页「查看候选」入口：根据同步记录跳转到关联的候选详情。
        优先返回 pending 行；若都已处理则返回最近一条（按 id DESC）。
        """
        # 先查 pending 行（最相关）
        row = self._read_sync_record(sync_record_id, "pending")
        if row:
            return row
        # 无 pending 行时返回最近一条（任意状态）
        return self._read_sync_record(sync_record_id, None)

    def _read_sync_record(
        self, sync_record_id: int, status: str | None
    ) -> dict[str, Any] | None:
        """内部读：按 sync_record_id 查候选，可按 status 过滤"""
        sql = """
            SELECT id, created_at, request_title, request_ori_title,
                   request_season, request_episode, user_name, source,
                   candidates_json, trace_json, status, confirmed_subject_id,
                   resolved_at, sync_record_id, llm_subject_id, llm_reason
            FROM pending_candidates
            WHERE sync_record_id = ?
        """
        params: list[Any] = [sync_record_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT 1"

        def _read(conn):
            cursor = conn.execute(sql, tuple(params))
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return _project_llm_fields(dict(zip(cols, row, strict=True)))

        return self._run_read(
            _read, error_msg="按 sync_record_id 查候选失败", default=None
        )

    def find_latest_by_business_key(self, business_key: str) -> dict[str, Any] | None:
        """按 business_key 查最近一条候选（pending/confirmed/rejected，按 id DESC）。

        供 run 入队决策的「结果复用由业务子状态驱动」判定：
        - confirmed → 校验映射有效则复用（不限时间）；映射无效 → 重新评估
        - pending → 复用（无限期）
        - rejected → 保留窗口内复用，出窗重新评估

        空 business_key 直接返回 None（不参与去重）。无匹配返回 None。
        """
        if not business_key:
            return None

        def _read(conn):
            cursor = conn.execute(
                """
                SELECT * FROM pending_candidates
                WHERE business_key = ? AND status IN ('pending', 'confirmed', 'rejected')
                ORDER BY id DESC LIMIT 1
                """,
                (business_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return _project_llm_fields(dict(zip(cols, row, strict=True)))

        return self._run_read(
            _read, error_msg="按 business_key 查候选失败", default=None
        )

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

    def resolve_similar_pending_candidates(
        self,
        request_title: str,
        request_season: int,
        user_name: str,
        source: str,
        status: str,
        confirmed_subject_id: str = "",
        exclude_id: int | None = None,
        business_key: str = "",
    ) -> int:
        """批量更新同 key 的 pending 候选状态，返回受影响行数。

        用于 confirm_pending_candidate 后清理同标题的残留 pending 行。

        策略：
        - business_key 非空时按 business_key 批量更新（exclude_id 逻辑保留）；
        - business_key 为空时保持旧 4 元组行为。
        """

        def _write(conn):
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if business_key:
                # 按 business_key 批量更新
                if exclude_id is not None:
                    cursor = conn.execute(
                        """
                        UPDATE pending_candidates
                        SET status = ?, confirmed_subject_id = ?, resolved_at = ?
                        WHERE business_key = ?
                          AND status = 'pending' AND id != ?
                        """,
                        (
                            status,
                            confirmed_subject_id,
                            local_time,
                            business_key,
                            exclude_id,
                        ),
                    )
                else:
                    cursor = conn.execute(
                        """
                        UPDATE pending_candidates
                        SET status = ?, confirmed_subject_id = ?, resolved_at = ?
                        WHERE business_key = ?
                          AND status = 'pending'
                        """,
                        (
                            status,
                            confirmed_subject_id,
                            local_time,
                            business_key,
                        ),
                    )
            else:
                # business_key 为空：保留旧 4 元组行为
                if exclude_id is not None:
                    cursor = conn.execute(
                        """
                        UPDATE pending_candidates
                        SET status = ?, confirmed_subject_id = ?, resolved_at = ?
                        WHERE request_title = ? AND request_season = ?
                          AND user_name = ? AND source = ?
                          AND status = 'pending' AND id != ?
                        """,
                        (
                            status,
                            confirmed_subject_id,
                            local_time,
                            request_title,
                            request_season,
                            user_name,
                            source,
                            exclude_id,
                        ),
                    )
                else:
                    cursor = conn.execute(
                        """
                        UPDATE pending_candidates
                        SET status = ?, confirmed_subject_id = ?, resolved_at = ?
                        WHERE request_title = ? AND request_season = ?
                          AND user_name = ? AND source = ?
                          AND status = 'pending'
                        """,
                        (
                            status,
                            confirmed_subject_id,
                            local_time,
                            request_title,
                            request_season,
                            user_name,
                            source,
                        ),
                    )
            return cursor.rowcount

        return self._run_write(_write, error_msg="批量更新待确认候选失败", default=0)
