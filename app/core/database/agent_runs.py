"""Agent 通用会话仓库（agent_runs / agent_steps / agent_run_sync_records）

承载通用 Agent 会话状态机与可重放 span 日志：
- agent_runs：一次会话（pending -> processing -> succeeded / failed / cancelled）
- agent_steps：每轮 LLM 调用 / 每次工具执行的 span
  （按 iteration, sequence, id 排序）
- agent_run_sync_records：run ↔ sync_record 关联（多对一：N 条集级 record 关联 1 个剧集级 run）

设计要点：
- 业务无关：不持有 applied/rejected 等业务特化终态；用户处理结果由
  pending_candidates（status + resolved_at）承载。
- 失败重试「每次新建 run」：累计失败次数 = SUM(total_attempts) WHERE
  business_key=? AND status='failed'，达上限不再新建；每个 run 失败转终态时
  total_attempts += 1（同一 run 只计一次）。
- 结果复用由业务子状态驱动（候选 pending/confirmed/rejected），非固定窗口一刀切。

关键并发与守卫语义：
- atomic_claim：原子 UPDATE `WHERE status='pending'`，受影响行数=0 视为抢占失败
- increment_attempts：调度轮次失败计数，>=3 单点置 failed（同事务携带 last_error）
- mark_failed / mark_succeeded：状态守卫 first-wins
  （仅 pending/processing 可转终态，非活性态 rowcount=0 → 返回 False，不覆盖先到终态）
- refresh_started_at：ts 为空/非正值取当前时间（保证 started_at>0 可被恢复扫描拾取）
- enqueue_match_run：单事务内完成 created / in_flight / 复用 / exhausted 决策；
  内部异常向上抛出（仅并发唯一索引冲突兜底复用），决策策略参数必填
- cleanup_expired：滑动窗口轮转，单条 DELETE（FK 级联删 steps / 关联行）
"""

import json
import sqlite3
import time
from datetime import datetime
from typing import Any

from ...utils.secret_redact import redact_secrets
from ..logging import logger
from .base_repository import BaseRepository

# 终态：除 pending / processing 外的全部状态（业务无关，不含 applied/rejected）
# 「成功结束但未产出建议」（预算耗尽 / 校验失败 / 明确放弃）**不是独立终态**：
# 它就是 succeeded，差异由 stop_reason 承载、诊断信息由 last_error 承载。
_TERMINAL_STATUSES = (
    "succeeded",
    "failed",
    "cancelled",
)

# 活性态：pending / processing（cleanup_expired 的活性分支判断用）
_ACTIVE_STATUSES = ("pending", "processing")

# resolved_at / created_at 等 DATETIME 文本列的存储格式（本地时间）
_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def _now() -> int:
    """当前 epoch 秒整数（与 agent_steps/agent_runs 时间列格式一致，便于整数比较）。"""
    return int(time.time())


def _resolved_within_window(resolved_at: Any, window_days: int, now_ts: int) -> bool:
    """resolved_at（本地时间字符串）是否落在 ``now_ts - window_days`` 之内。

    解析失败 / 缺失按出窗处理（重新评估更安全），并记录告警便于排查。
    """
    if not resolved_at:
        return False
    try:
        ts = int(datetime.strptime(str(resolved_at), _DATETIME_FMT).timestamp())
    except (TypeError, ValueError) as e:
        logger.warning(
            f"pending_candidates.resolved_at 解析失败（按出窗处理）: "
            f"{resolved_at!r}: {e}"
        )
        return False
    return ts >= now_ts - window_days * 86400


def _encrypt_replay_delta(raw: Any) -> str:
    """加密 replay_delta（best-effort：失败降级存明文，不中断主流程）。

    接受 str / dict / list 等类型：非字符串先序列化为 JSON 再加密。
    """
    if not raw:
        return raw if isinstance(raw, str) else ""
    try:
        from ..config_secret_crypto import encrypt

        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        return encrypt(text)
    except Exception as e:  # best-effort：加密失败降级存明文
        logger.warning(f"[agent_runs] replay_delta 加密失败（已降级存明文）: {e}")
        return raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)


def _decrypt_replay_delta(stored: Any) -> str:
    """解密 replay_delta（容错无前缀明文：原样返回）。"""
    if not stored:
        return stored if isinstance(stored, str) else ""
    try:
        from ..config_secret_crypto import decrypt

        return decrypt(stored)
    except Exception:  # 容错：解密失败原样返回
        return stored if isinstance(stored, str) else ""


def apply_cancelled(conn, run_id: str, *, stop_reason: str) -> bool:
    """事务内 cancelled 写入口：SQL 与源状态守卫与 mark_cancelled 完全相同。

    供已持有写锁/在事务内执行的调用方直接调用（**不二次取锁**，避免
    ``_run_write`` 嵌套写锁）。守卫为活性态 ``pending`` / ``processing``，
    非活性态（succeeded / failed / 已 cancelled）返回 False，
    原值不变；``ended_at`` 写 ``_now()``（epoch 秒，与其它终态口径一致）。

    返回是否真正改写（受影响行数 > 0）。
    """
    cursor = conn.execute(
        """
        UPDATE agent_runs
        SET status='cancelled', stop_reason=?, ended_at=?
        WHERE run_id=? AND status IN ('pending','processing')
        """,
        (stop_reason, _now(), run_id),
    )
    return cursor.rowcount > 0


def apply_succeeded(
    conn,
    run_id: str,
    *,
    stop_reason: str,
    last_error: str = "",
    total_tokens: int = 0,
    ended_at: int | None = None,
) -> bool:
    """事务内 succeeded 写入口：SQL 与源状态守卫与 mark_succeeded 完全相同。

    供已持有写锁/在事务内执行的调用方直接调用（**不二次取锁**，避免
    ``_run_write`` 嵌套写锁）。守卫为活性态 ``pending`` / ``processing``，
    非活性态（failed / cancelled / 已 succeeded）返回 False，
    原值不变；``ended_at`` 缺省取 ``_now()``（epoch 秒，与其它终态口径一致），
    显式传入时按调用方时钟写入（供与候选写入同事务的场景统一时间基准）。

    ``last_error`` 仅承载**诊断信息**（如「建议校验未通过的原因」），不影响
    ``succeeded`` 判定 —— 「成功结束但无建议」不是独立终态，差异由 ``stop_reason``
    表达（如 ``exhausted`` / ``give_up`` / ``end_turn``）。

    返回是否真正改写（受影响行数 > 0）。
    """
    cursor = conn.execute(
        """
        UPDATE agent_runs
        SET status='succeeded', stop_reason=?, last_error=?,
            total_tokens=?, ended_at=?
        WHERE run_id=? AND status IN ('pending','processing')
        """,
        (
            stop_reason,
            last_error,
            total_tokens,
            _now() if ended_at is None else ended_at,
            run_id,
        ),
    )
    return cursor.rowcount > 0


#: run 级可重试上限：attempts 达该值时由 increment_attempts 单点置终态（
#: runtime 的失败分流共用同一常量，避免魔法数字散落）
MAX_RUN_ATTEMPTS: int = 3


class AgentRunsRepository(BaseRepository):
    """agent_runs / agent_steps 的增删改查与状态机流转"""

    # ------------------------------------------------------------------
    # 写入：会话创建与状态流转
    # ------------------------------------------------------------------

    def create_pending(
        self,
        run_id: str,
        task_type: str,
        sync_record_id: int | None = None,
        *,
        business_key: str = "",
    ) -> int:
        """沉淀一条 pending 会话，返回记录 id（失败时 0）。

        通用原语：不做业务去重决策（业务入队请用 :meth:`enqueue_match_run`）。
        business_key 为业务键（写入该列，默认空字符串保持向后兼容）。
        """

        def _write(conn):
            ts = _now()
            cursor = conn.execute(
                """
                INSERT INTO agent_runs
                (run_id, task_type, sync_record_id, business_key, status, attempts,
                 total_attempts, created_at, last_attempt_at)
                VALUES (?, ?, ?, ?, 'pending', 0, 0, ?, ?)
                """,
                (run_id, task_type, sync_record_id, business_key, ts, ts),
            )
            return cursor.lastrowid

        return self._run_write(_write, error_msg="创建 agent_run 失败", default=0)

    def enqueue_match_run(
        self,
        run_id: str,
        business_key: str,
        *,
        sync_record_id: int | None = None,
        reuse_window_days: int,
        max_total_attempts: int,
        accepted_mapping_valid: bool,
    ) -> dict:
        """按 business_key 决策入队（created / in_flight / 复用 / exhausted）。

        在单个 ``_run_write`` 事务内完成决策（含候选子状态查询），返回
        ``{"decision": str, "run_id": str}``：
        - 无历史 run → 新建 pending → ``created``（返回传入 run_id）
        - 最新 run pending/processing → ``in_flight``（返回已有 run_id）
        - 最新 run failed → 累计失败 = SUM(total_attempts)（同键 failed 行）：
          < max_total_attempts → 新建 ``created``；≥ 上限 → ``exhausted``（不写库）
        - 最新 run succeeded（含「无建议」这类成功结束的情况）→ 按候选子状态复用：
          * confirmed + accepted_mapping_valid → ``reuse_accepted``（不限时间）
          * pending → ``reuse_holding``（无限期）
          * rejected 且 resolved_at 在保留窗口内 → ``reuse_holding``
          * rejected 出窗 / 无候选 → 新建 ``created``
        - 最新 run cancelled / 其他终态 → 新建 ``created``

        **决策 ↔ 候选状态术语映射**（历史文档沿用旧称，此处统一对齐）：
        ``pending_candidates.status`` 的 ``pending`` 即历史文档中的
        ``waiting_accept``（候选待用户接受）→ ``reuse_holding``（无限期）；
        ``confirmed``（用户已接受）→ ``reuse_accepted``；``rejected``（用户已拒绝）
        → 保留窗口内 ``reuse_holding``、出窗重新评估。

        复用/在途时 ``sync_record_id`` 非空会刷新该 run 的主指针；
        ``business_key`` 为空时保持去重禁用语义（每次直接新建）。

        **决策策略参数（配置类，禁止默认值兜底，由调用方显式传入）**：
        ``reuse_window_days``（rejected 候选复用窗口天数）、
        ``max_total_attempts``（同键累计失败上限）、
        ``accepted_mapping_valid``（accepted 候选映射是否仍有效；漏传会被
        误判为有效 → 无限期复用，故不设默认值）。

        **复用语义（reuse_accepted / reuse_holding）**：仅刷新 run↔record 关联与
        主指针，**不重跑 LLM、不重写历史候选展示**；候选由 ``business_key``
        唯一行承载，rejected 候选不复活展示。这是预期行为（复用=跳过重复评估）。

        落库异常**向上抛出**（``_run_write(reraise=True)``）：由调用方降级处理
        （orchestrator 捕获后走 ``enqueue_failed``），禁止谎报 ``created`` 导致
        任务静默丢失。唯一例外是并发唯一索引冲突（``sqlite3.IntegrityError``），
        此时兜底复用已在途 run 返回 ``in_flight``。
        """
        if not business_key:
            self.create_pending(
                run_id=run_id,
                task_type="match",
                sync_record_id=sync_record_id,
            )
            return {"decision": "created", "run_id": run_id}

        result_holder: list[dict] = []
        now_ts = _now()

        def _insert_pending(conn) -> None:
            conn.execute(
                """
                INSERT INTO agent_runs
                (run_id, task_type, sync_record_id, business_key, status,
                 attempts, total_attempts, created_at, last_attempt_at)
                VALUES (?, 'match', ?, ?, 'pending', 0, 0, ?, ?)
                """,
                (run_id, sync_record_id, business_key, now_ts, now_ts),
            )

        def _refresh_pointer(conn, target_run_id: str) -> None:
            if sync_record_id is None:
                return
            conn.execute(
                "UPDATE agent_runs SET sync_record_id=? WHERE run_id=?",
                (sync_record_id, target_run_id),
            )

        def _write(conn):
            row = self._find_latest_by_business_key(conn, business_key)
            if row is None:
                _insert_pending(conn)
                result_holder.append({"decision": "created", "run_id": run_id})
                return

            status = row["status"]
            existing_run_id = row["run_id"]

            if status in _ACTIVE_STATUSES:
                _refresh_pointer(conn, existing_run_id)
                result_holder.append(
                    {"decision": "in_flight", "run_id": existing_run_id}
                )
                return

            if status == "failed":
                total_failed = conn.execute(
                    "SELECT COALESCE(SUM(total_attempts), 0) FROM agent_runs "
                    "WHERE business_key=? AND status='failed'",
                    (business_key,),
                ).fetchone()[0]
                if total_failed < max_total_attempts:
                    _insert_pending(conn)
                    result_holder.append({"decision": "created", "run_id": run_id})
                else:
                    result_holder.append(
                        {"decision": "exhausted", "run_id": existing_run_id}
                    )
                return

            if status == "succeeded":
                reuse_decision = self._decide_reuse_from_candidate(
                    conn,
                    business_key,
                    reuse_window_days=reuse_window_days,
                    accepted_mapping_valid=accepted_mapping_valid,
                    now_ts=now_ts,
                )
                if reuse_decision is None:
                    _insert_pending(conn)
                    result_holder.append({"decision": "created", "run_id": run_id})
                else:
                    _refresh_pointer(conn, existing_run_id)
                    result_holder.append(
                        {"decision": reuse_decision, "run_id": existing_run_id}
                    )
                return

            # cancelled / 其他终态 → 新建重新评估
            _insert_pending(conn)
            result_holder.append({"decision": "created", "run_id": run_id})

        # 内部异常不吞并：_run_write(reraise=True) 记录日志后向上抛出，
        # 由调用方（orchestrator）走 enqueue_failed 降级，禁止谎报 created
        # 导致任务静默丢失。唯一例外是并发唯一索引冲突的兜底复用。
        try:
            self._run_write(_write, error_msg="匹配任务入队决策失败", reraise=True)
        except sqlite3.IntegrityError:
            # 并发兜底：同键在途重复插入被部分唯一索引拒绝 → 复用在途 run
            active_run_id = self._find_active_run_id(business_key)
            logger.warning(
                "同一 business_key 存在并发在途 run（唯一索引冲突），按 in_flight 复用: "
                f"business_key={business_key}, run_id={active_run_id or run_id}"
            )
            return {"decision": "in_flight", "run_id": active_run_id or run_id}

        if not result_holder:
            # 防御兜底：事务未产出决策（理论不可达），显式告警便于排查
            logger.warning(
                "匹配任务入队决策未产出结果（按 created 返回）: "
                f"business_key={business_key}, run_id={run_id}"
            )
            return {"decision": "created", "run_id": run_id}
        return result_holder[0]

    @staticmethod
    def _decide_reuse_from_candidate(
        conn,
        business_key: str,
        *,
        reuse_window_days: int,
        accepted_mapping_valid: bool,
        now_ts: int,
    ) -> str | None:
        """按业务候选子状态给出复用决策；返回 None 表示需新建重新评估。

        只读查询与主决策同事务执行（避免读写间隙竞态）。
        """
        candidate = conn.execute(
            "SELECT status, resolved_at FROM pending_candidates "
            "WHERE business_key=? AND status IN ('pending','confirmed','rejected') "
            "ORDER BY id DESC LIMIT 1",
            (business_key,),
        ).fetchone()
        if candidate is None:
            return None

        cand_status = candidate[0]
        resolved_at = candidate[1]

        if cand_status == "confirmed":
            # accepted：映射仍有效 → 不限时间复用；映射已删除 → 重新评估
            return "reuse_accepted" if accepted_mapping_valid else None
        if cand_status == "pending":
            # waiting_accept：候选存在即复用（无限期）
            return "reuse_holding"
        if cand_status == "rejected":
            if _resolved_within_window(resolved_at, reuse_window_days, now_ts):
                return "reuse_holding"
            return None
        # 防御兜底：SQL 已限定 status 枚举，理论不可达，显式告警便于排查
        logger.warning(
            f"pending_candidates 出现未预期 status={cand_status!r}（按重新评估处理）"
        )
        return None

    def _find_active_run_id(self, business_key: str) -> str | None:
        """按 business_key 查在途（pending/processing）run_id，无则 None。

        仅用于唯一索引并发冲突兜底（同键在途已存在时复用）。
        """

        def _read(conn):
            row = conn.execute(
                "SELECT run_id FROM agent_runs WHERE business_key=? "
                "AND status IN ('pending','processing') ORDER BY id DESC LIMIT 1",
                (business_key,),
            ).fetchone()
            return row[0] if row else None

        return self._run_read(_read, error_msg="查询在途 agent_run 失败", default=None)

    @staticmethod
    def _find_latest_by_business_key(conn, business_key: str) -> dict | None:
        """按 business_key 查最近一条 run（任意状态，按 id DESC），无则 None。"""
        cursor = conn.execute(
            "SELECT * FROM agent_runs WHERE business_key=? ORDER BY id DESC LIMIT 1",
            (business_key,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row, strict=True))

    def atomic_claim(self, run_id: str) -> bool:
        """原子抢占：仅当 status='pending' 时置 processing。

        受影响行数=0（已被其它调度器抢占）返回 False；否则 True。

        单 worker 部署下语义等价 mark_processing；保留原子抢占语义以支持多实例/并发场景。
        """

        def _write(conn):
            cursor = conn.execute(
                """
                UPDATE agent_runs
                SET status='processing', started_at=?
                WHERE run_id=? AND status='pending'
                """,
                (_now(), run_id),
            )
            return cursor.rowcount > 0

        return self._run_write(
            _write, error_msg="原子拾取 agent_run 失败", default=False
        )

    def mark_succeeded(
        self,
        run_id: str,
        stop_reason: str = "",
        last_error: str = "",
        total_tokens: int = 0,
    ) -> bool:
        """标记 run 成功结束（终态），记录终态时间。

        「产出建议并通过校验」与「成功结束但无建议」（预算耗尽 / 校验失败 /
        明确放弃）**同为 succeeded**：终态只回答「这次 run 结束了吗」，差异由
        ``stop_reason``（``exhausted`` / ``give_up`` / ``end_turn`` /
        ``submit_suggestion`` 等）承载，诊断信息由 ``last_error`` 承载。
        不再存在独立的 ``no_suggestion`` 终态。

        **状态守卫**：仅 ``pending`` / ``processing`` 活性态可转 succeeded；
        对 ``failed`` / ``cancelled`` / 已 ``succeeded``
        等非活性态不生效（受影响行数=0 → 返回 False），避免误改终态。

        守卫为 first-wins：双跑 / 超时取消后恢复续跑与原执行者竞态时，
        后到者的落库不覆盖先到终态。

        SQL 与源状态守卫收敛到模块级 :func:`apply_succeeded`（单一入口），
        本方法在其外层保留 ``_run_write`` 取锁与错误消息。
        """

        # 先对完整文本脱敏、后截断：避免调用方预截断（如 str(e)[:500]）
        # 把敏感值切在边界上导致模式失配而泄漏裸片段。
        last_error = (redact_secrets(last_error) or "")[:500]

        def _write(conn):
            return apply_succeeded(
                conn,
                run_id,
                stop_reason=stop_reason,
                last_error=last_error,
                total_tokens=total_tokens,
            )

        return self._run_write(
            _write, error_msg="标记 agent_run succeeded 失败", default=False
        )

    def mark_failed(
        self, run_id: str, stop_reason: str, last_error: str, total_tokens: int = 0
    ) -> bool:
        """标记失败（LLM 调用失败 attempts 达上限），记录终态时间。

        **状态守卫**：仅 ``pending`` / ``processing`` 活性态可转 failed；
        对 ``failed`` / ``cancelled`` / 已 ``succeeded``
        等非活性态不生效（受影响行数=0 → 返回 False），避免误改终态。

        失败累计：转终态时 ``total_attempts += 1``（同键 failed 行的
        ``SUM(total_attempts)`` 即累计失败次数）。守卫保证同一 run 仅在
        活性态首次调用时 +1（「一个 run 只计 1 次失败」），避免
        increment_attempts 达上限置终态后调用方再 mark_failed 造成双计。
        """

        # 先对完整文本脱敏、后截断（见 mark_succeeded 注释）。
        last_error = (redact_secrets(last_error) or "")[:500]

        def _write(conn):
            cursor = conn.execute(
                """
                UPDATE agent_runs
                SET status='failed', stop_reason=?, last_error=?, total_tokens=?,
                    ended_at=?,
                    total_attempts = total_attempts + 1
                WHERE run_id=? AND status IN ('pending','processing')
                """,
                (stop_reason, last_error, total_tokens, _now(), run_id),
            )
            return cursor.rowcount > 0

        return self._run_write(
            _write, error_msg="标记 agent_run failed 失败", default=False
        )

    def mark_cancelled(self, run_id: str, *, stop_reason: str = "") -> bool:
        """标记取消（用户已处理同 record 的候选，本次 LLM 建议作废，不再通知）。

        **状态守卫**：仅 ``pending`` / ``processing`` 活性态可转 cancelled；
        对 ``succeeded`` / ``failed`` / 已 ``cancelled``
        等非活性态不生效（受影响行数=0 → 返回 False），避免误改终态。

        写 ``ended_at``（epoch 秒，与其它终态方法口径一致）。

        SQL 与源状态守卫收敛到模块级 :func:`apply_cancelled`（单一入口），
        本方法在其外层保留 ``_run_write`` 取锁与错误消息。
        """

        def _write(conn):
            return apply_cancelled(conn, run_id, stop_reason=stop_reason)

        return self._run_write(
            _write, error_msg="标记 agent_run cancelled 失败", default=False
        )

    def increment_attempts(self, run_id: str, last_error: str = "") -> int:
        """累加调度轮次失败次数（仅 processing 态有效），返回最新 attempts。

        达上限（attempts>=3）时在**同一次调用事务内**单点置终态：
        status='failed'、stop_reason='failed'、last_error、ended_at，并
        ``total_attempts += 1``（本次 run 计一次累计失败）。
        调用方无需再自行 mark_failed（避免"失败置终态"双写）。

        非 processing 态 / run 不存在 → 不计数，返回 0。
        """

        # 先对完整文本脱敏、后截断（见 mark_succeeded 注释）。
        last_error = (redact_secrets(last_error) or "")[:500]

        def _write(conn):
            cursor = conn.execute(
                "UPDATE agent_runs SET attempts = attempts + 1, last_attempt_at=? "
                "WHERE run_id=? AND status='processing'",
                (_now(), run_id),
            )
            if cursor.rowcount == 0:
                return 0
            row = conn.execute(
                "SELECT attempts FROM agent_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            attempts = row[0] if row else 0
            if attempts >= MAX_RUN_ATTEMPTS:
                conn.execute(
                    "UPDATE agent_runs SET status='failed', stop_reason='failed', "
                    "last_error=?, ended_at=?, "
                    "total_attempts = total_attempts + 1 WHERE run_id=?",
                    (last_error, _now(), run_id),
                )
            return attempts

        return self._run_write(
            _write, error_msg="累加 agent_run attempts 失败", default=0
        )

    def refresh_started_at(
        self,
        run_id: str,
        ts: int | None = None,
        expected_started_at: int | None = None,
    ) -> bool:
        """恢复扫描时刷新 started_at（仅 processing 态有效）。

        ``ts`` 由调用方注入统一时间戳（重入防护抢占时保持同一时钟基准）；
        ``ts`` 为 None 或 <=0 时取当前时间——``list_stale_processing`` 以
        ``started_at > 0`` 为恢复扫描前提，写入非正值会使该 run 永不入选。

        防止下一轮重复恢复同一崩溃遗留 run；行数=0（非 processing）返回 False。

        **CAS 语义（跨进程恢复抢占）**：``expected_started_at`` 非 None 时追加
        ``AND started_at=?`` 条件，仅当当前值与扫描到的值一致（即无其他执行者
        已刷新过）才成功，返回 rowcount>0。用于避免两个调度进程在同一轮
        ``list_stale_processing`` 后都恢复同一 run。默认 None 保持向后兼容。
        """

        def _write(conn):
            ts_value = _now() if ts is None or ts <= 0 else ts
            if expected_started_at is None:
                cursor = conn.execute(
                    "UPDATE agent_runs SET started_at=? "
                    "WHERE run_id=? AND status='processing'",
                    (ts_value, run_id),
                )
            else:
                cursor = conn.execute(
                    "UPDATE agent_runs SET started_at=? "
                    "WHERE run_id=? AND status='processing' AND started_at=?",
                    (ts_value, run_id, expected_started_at),
                )
            return cursor.rowcount > 0

        return self._run_write(
            _write, error_msg="刷新 agent_run started_at 失败", default=False
        )

    def update_run_status(self, run_id: str, status: str) -> bool:
        """通用状态改写辅助（写入辅助；业务流转优先用上面的语义化方法）"""

        def _write(conn):
            cursor = conn.execute(
                "UPDATE agent_runs SET status=? WHERE run_id=?", (status, run_id)
            )
            return cursor.rowcount > 0

        return self._run_write(
            _write, error_msg="更新 agent_run 状态失败", default=False
        )

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def cleanup_expired(self, retention_days: int) -> int:
        """按滑动窗口轮转清理过期 runs（单条 DELETE，FK 级联删 steps）。

        两个分支 OR：
        - 终态分支：status IN (terminal) AND ended_at > 0 AND ended_at < cutoff
        - 活性分支：status IN (pending, processing) AND created_at < cutoff（过期死行一并删）

        retention_days <= 0 → 直接 return 0（永不清理语义，参照 sync_records.cleanup_old_records）。
        时间比较统一为 epoch 秒整数。
        """

        if retention_days <= 0:
            return 0

        cutoff = _now() - retention_days * 86400
        terminal_ph = ",".join("?" * len(_TERMINAL_STATUSES))
        active_ph = ",".join("?" * len(_ACTIVE_STATUSES))

        def _write(conn):
            cursor = conn.execute(
                f"""
                DELETE FROM agent_runs
                WHERE (status IN ({terminal_ph}) AND ended_at > 0 AND ended_at < ?)
                   OR (status IN ({active_ph}) AND created_at < ?)
                """,
                _TERMINAL_STATUSES + (cutoff,) + _ACTIVE_STATUSES + (cutoff,),
            )
            return cursor.rowcount

        return self._run_write(_write, error_msg="清理过期 agent_run 失败", default=0)

    # ------------------------------------------------------------------
    # 查询：调度器辅助 / run ↔ sync_record 关联
    # ------------------------------------------------------------------

    def add_run_sync_record_link(
        self, run_id: str, sync_record_id: int, decision: str = ""
    ) -> int:
        """写入 run ↔ sync_record 关联（多对一），返回新增行数（已存在/失败为 0）。

        INSERT OR IGNORE：同一 (run_id, sync_record_id) 重复写入幂等。
        decision 记录入队决策（created/in_flight/...），便于审计。
        """

        def _write(conn):
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO agent_run_sync_records
                (run_id, sync_record_id, decision, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, sync_record_id, decision, _now()),
            )
            return cursor.rowcount

        return self._run_write(
            _write, error_msg="写入 agent_run↔sync_record 关联失败", default=0
        )

    def update_run_sync_record_id(self, run_id: str, sync_record_id: int) -> bool:
        """刷新 run 的调度主指针 sync_record_id，返回是否更新成功。"""

        def _write(conn):
            cursor = conn.execute(
                "UPDATE agent_runs SET sync_record_id=? WHERE run_id=?",
                (sync_record_id, run_id),
            )
            return cursor.rowcount > 0

        return self._run_write(
            _write, error_msg="刷新 agent_run sync_record_id 失败", default=False
        )

    def find_latest_by_sync_record(self, sync_record_id: int) -> dict | None:
        """按 sync_record_id 查最新一条会话，无则 None。

        优先走关联表 agent_run_sync_records（多对一：N 条集级 record 关联
        1 个剧集级 run，按关联时间/run id 取最新）；无关联行时回退旧路径
        （agent_runs.sync_record_id 主指针），兼容历史数据。

        用于前端候选页：返回最新 agent run 的状态与 run_id（徽标 / 评估过程
        折叠区），不过滤状态，以便 run 终态后仍可回看。
        """

        def _read(conn):
            cursor = conn.execute(
                """
                SELECT r.* FROM agent_run_sync_records l
                JOIN agent_runs r ON r.run_id = l.run_id
                WHERE l.sync_record_id = ?
                ORDER BY l.created_at DESC, r.id DESC LIMIT 1
                """,
                (sync_record_id,),
            )
            row = cursor.fetchone()
            if row:
                cols = [d[0] for d in cursor.description]
                return dict(zip(cols, row, strict=True))

            # 兼容历史数据：回退按 agent_runs.sync_record_id 主指针查询
            cursor = conn.execute(
                "SELECT * FROM agent_runs WHERE sync_record_id=? "
                "ORDER BY id DESC LIMIT 1",
                (sync_record_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row, strict=True))

        return self._run_read(_read, error_msg="查询最新 agent_run 失败", default=None)

    # ------------------------------------------------------------------
    # 观测列表（分页 / 过滤）
    # ------------------------------------------------------------------

    @staticmethod
    def _runs_filter_sql(
        *,
        status: str | None,
        task_type: str | None,
        since: int | None,
        user_name: str | None,
    ) -> tuple[str, str, list]:
        """构造观测列表的 JOIN / WHERE / 参数（list_runs 与 count_runs 共用口径）。

        - ``user_name``：经 ``sync_records`` 归属过滤（agent_runs 无用户列，
          归属在同步记录上；run↔sync_record 一对一，JOIN 不放大行）。**INNER JOIN
          语义**：sync_record 缺失（如已清理）时无法判定归属，该 run 对按用户过滤
          的调用方不可见（admin 不带该过滤、无 JOIN，仍可见）——「无法判定归属
          不展示」的安全取舍；列表与 count 共用本构造，口径一致；
        - ``since``：epoch 秒下界（``created_at >= since``）。
        """
        joins = ""
        where: list[str] = []
        params: list = []
        if user_name is not None:
            joins += " JOIN sync_records s ON s.id = r.sync_record_id"
            where.append("s.user_name = ?")
            params.append(user_name)
        if status:
            where.append("r.status = ?")
            params.append(status)
        if task_type:
            where.append("r.task_type = ?")
            params.append(task_type)
        if since is not None:
            where.append("r.created_at >= ?")
            params.append(int(since))
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        return joins, where_sql, params

    def list_runs(
        self,
        *,
        status: str | None = None,
        task_type: str | None = None,
        since: int | None = None,
        user_name: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list:
        """列出会话（观测用）：可选过滤 + id 倒序（最新在最前）+ 分页。"""

        def _read(conn):
            joins, where_sql, params = self._runs_filter_sql(
                status=status, task_type=task_type, since=since, user_name=user_name
            )
            cursor = conn.execute(
                f"SELECT r.* FROM agent_runs r{joins}{where_sql} "
                "ORDER BY r.id DESC LIMIT ? OFFSET ?",
                [*params, int(limit), int(offset)],
            )
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, r, strict=True)) for r in cursor.fetchall()]

        return self._run_read(_read, error_msg="列出 agent_run 失败", default=[])

    def count_runs(
        self,
        *,
        status: str | None = None,
        task_type: str | None = None,
        since: int | None = None,
        user_name: str | None = None,
    ) -> int:
        """按与 :meth:`list_runs` 相同过滤口径统计总数。"""

        def _read(conn):
            joins, where_sql, params = self._runs_filter_sql(
                status=status, task_type=task_type, since=since, user_name=user_name
            )
            cursor = conn.execute(
                f"SELECT COUNT(*) FROM agent_runs r{joins}{where_sql}", params
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0

        return self._run_read(_read, error_msg="统计 agent_run 失败", default=0)

    def list_pending(self, limit: int = 50) -> list:
        """列出 pending 会话（供调度器拾取），按 id 升序"""

        def _read(conn):
            cursor = conn.execute(
                "SELECT * FROM agent_runs WHERE status='pending' "
                "ORDER BY id ASC LIMIT ?",
                (limit,),
            )
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, r, strict=True)) for r in cursor.fetchall()]

        return self._run_read(
            _read, error_msg="列出 pending agent_run 失败", default=[]
        )

    def list_stale_processing(self, timeout_seconds: int = 120) -> list:
        """列出 started_at 超时的 processing 会话（崩溃遗留恢复用）。
        时间比较统一为 epoch 秒整数。
        """

        cutoff = _now() - timeout_seconds

        def _read(conn):
            cursor = conn.execute(
                "SELECT * FROM agent_runs WHERE status='processing' "
                "AND started_at > 0 AND started_at < ? ORDER BY id ASC",
                (cutoff,),
            )
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, r, strict=True)) for r in cursor.fetchall()]

        return self._run_read(
            _read, error_msg="列出超时 processing agent_run 失败", default=[]
        )

    def get_run(self, run_id: str) -> dict | None:
        """按 run_id 查询单条会话"""

        def _read(conn):
            cursor = conn.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row, strict=True))

        return self._run_read(_read, error_msg="查询 agent_run 失败", default=None)

    # ------------------------------------------------------------------
    # agent_steps 写入与查询
    # ------------------------------------------------------------------

    def add_step(self, step: dict) -> int:
        """写入一条 span（agent_steps），返回记录 id（失败时 0）。

        replay_delta 写入前统一加密（best-effort：失败降级存明文）。
        started_at/ended_at 为 epoch 秒整数（0 表示未设置，写入时取当前时间）。
        """

        started_at = step.get("started_at") or _now()
        ended_at = step.get("ended_at") or _now()
        replay_delta = _encrypt_replay_delta(step.get("replay_delta", ""))

        def _write(conn):
            cursor = conn.execute(
                """
                INSERT INTO agent_steps
                (run_id, span_id, parent_id, name, status, model, tokens,
                 latency_ms, tool_name, input_summary, error, iteration,
                 sequence, replay_delta, started_at, ended_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    step.get("run_id", ""),
                    step.get("span_id", ""),
                    step.get("parent_id", ""),
                    step.get("name", ""),
                    step.get("status", "ok"),
                    step.get("model", ""),
                    step.get("tokens", 0),
                    step.get("latency_ms", 0),
                    step.get("tool_name", ""),
                    step.get("input_summary", ""),
                    step.get("error", ""),
                    step.get("iteration", 0),
                    step.get("sequence", 0),
                    replay_delta,
                    started_at,
                    ended_at,
                ),
            )
            return cursor.lastrowid

        return self._run_write(_write, error_msg="写入 agent_step 失败", default=0)

    def update_step(self, span_id: str, **fields: Any) -> bool:
        """更新一条 span（agent_steps），供 trace.end_span 调用。

        支持的字段：status, model, tokens, latency_ms, tool_name, input_summary,
        error, replay_delta, started_at, ended_at。
        replay_delta 写入前统一加密（best-effort：失败降级存明文）。
        返回是否成功（无变化或异常均返回 False）。
        """
        allowed = {
            "status",
            "model",
            "tokens",
            "latency_ms",
            "tool_name",
            "input_summary",
            "error",
            "replay_delta",
            "started_at",
            "ended_at",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False

        if "replay_delta" in updates:
            updates["replay_delta"] = _encrypt_replay_delta(updates["replay_delta"])

        cols = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [span_id]

        def _write(conn):
            cursor = conn.execute(
                f"UPDATE agent_steps SET {cols} WHERE span_id=?", values
            )
            return cursor.rowcount > 0

        return self._run_write(_write, error_msg="更新 agent_step 失败", default=False)

    def mark_residual_running_spans(self) -> int:
        """把「所属 run 已终结但自身仍未闭合」的 running span 标记为 error。

        - 判定：``agent_steps.status='running'`` 且所属 ``agent_runs.status``
          已终态（succeeded / failed / cancelled）——正常处理中的 run 的
          running span 是合法中间态，不在清理范围内（不误伤）
        - 动作：``status='error'``、``error`` 记中断说明（残留可读、可过滤）
        - 返回受影响行数；**幂等**（已处理后再次调用返回 0）
        - 供应用启动 / 调度器恢复扫描调用（best-effort：失败由 ``_run_write``
          记日志并返回 0，不影响启动主流程）
        """

        def _write(conn):
            cursor = conn.execute(
                """
                UPDATE agent_steps
                SET status='error',
                    error='span 未闭合（所属 run 已终结，进程可能中断）'
                WHERE status='running' AND run_id IN (
                    SELECT run_id FROM agent_runs
                    WHERE status IN (?, ?, ?)
                )
                """,
                _TERMINAL_STATUSES,
            )
            return cursor.rowcount

        return self._run_write(
            _write, error_msg="标记残留 running span 失败（已忽略）", default=0
        )

    def get_steps(self, run_id: str) -> list:
        """按 run_id 查询 span 列表，按 (iteration, sequence, id) 排序。

        末级 ``id`` 作为 tie-break：同一 (iteration, sequence) 内保持写入顺序，
        与 trace.replay 的 ``(iteration, sequence, id)`` 排序契约一致。

        replay_delta 读取时统一解密（容错无前缀明文：原样返回）。
        """

        def _read(conn):
            cursor = conn.execute(
                "SELECT * FROM agent_steps WHERE run_id=? "
                "ORDER BY iteration ASC, sequence ASC, id ASC",
                (run_id,),
            )
            cols = [d[0] for d in cursor.description]
            rows = []
            for r in cursor.fetchall():
                d = dict(zip(cols, r, strict=True))
                d["replay_delta"] = _decrypt_replay_delta(d.get("replay_delta"))
                rows.append(d)
            return rows

        return self._run_read(_read, error_msg="查询 agent_steps 失败", default=[])
