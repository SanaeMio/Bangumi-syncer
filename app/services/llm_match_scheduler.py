"""LLM 匹配增强调度器。

继承 BaseScheduler，同构 bangumi_replay_scheduler，按 ``[sync] llm_match_cron``
（默认 ``*/1 * * * *``）定时轮询 agent_runs 处理 match 任务。

每轮流程（recover 与 pending 共享并发限额并发消费）：
1. **清理**：终态且 ended_at 超保留期 → 先删 agent_steps 再删 agent_runs（级联）+ 日志
2. **恢复扫描**：``processing`` 且 started_at 超时的遗留 run → 刷新 started_at
   → sync_record 缺失则 mark_failed；否则构造 bgm 后交由场景层公开入口
   场景运行入口（``get_scenario(task_type).continue_run``）完成 replay 重建与续跑（本调度器不触碰场景内部符号）
3. **正常处理**：逐条原子拾取（由场景运行入口内部 atomic_claim 负责）→
    调场景运行入口 ``get_scenario(task_type).run`` → 异常捕获累加 attempts（≥3 标 failed）

recover 与 pending 统一入列后经 ``asyncio.Semaphore(llm_match_concurrency)`` 并发消费，
单条 run 异常在包装层隔离记录，不影响同批其他 run。sync_record_id 尚未回填
（入队前移的毫秒级窗口）的 run 本轮跳过，等回填后下一轮处理。

去重落在落任务入口（sync_service 的 _handle_match_failure），本调度器只处理已存在任务。
重入防护：本进程正在处理的 run 记入模块级 ``_active_run_ids``，恢复扫描与正常处理
均跳过，避免「进程活着但处理慢」被误判为崩溃遗留而双跑；恢复开始时以统一时间戳
抢占刷新 started_at。跨进程死进程恢复仍由 list_stale_processing 超时判定兜底。

超时闭环（APScheduler / cron 架构不变，仅加两层超时，超时值动态推算、不新增配置项）：

1. **per-run 动态超时（主防线）**：``compute_match_run_timeout`` 按
   ``max_iterations × ([llm] timeout + 工具超时30s) + 缓冲120s`` 推算单 run 上限，
   在 ``_consume`` 内以 ``asyncio.wait_for`` 施压。任一 run 协程 hang（不抛异常、
   不返回）→ **wait_for 超时会 cancel 该协程** → ``_recover_run`` / ``_process_run``
   的 ``finally: _release_run`` 执行（执行权释放）→ run 仍为 ``processing`` →
   下一 tick 恢复扫描断点续跑；同批其他 run 不受影响。
2. **tick 级兜底**：``_run_sync_job`` 以 ``wait_for(_run_round(...), run_timeout + 60)``
   包住整轮，防「fn 之外」的意外 hang 让 tick 永不结束（``max_instances=1`` 下会
   导致后续触发全部被跳过、整个调度停摆）。tick 超时会取消 gather 内全部 run →
   各自释放执行权 → 下一轮恢复（罕见兜底，可接受）。
3. ``CancelledError`` 不得被吞：超时取消会传播到内部协程，各 ``finally`` 正常执行。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from pydantic import ValidationError

from app.core.config import config_manager
from app.core.database import get_database_manager
from app.core.logging import logger
from app.models.agent import AgentRunRecord
from app.services.agent.budget import compute_match_run_timeout
from app.services.agent.run.registry import get_scenario
from app.services.base.notifier_helpers import notify_agent_run_failed
from app.services.base.scheduler import BaseScheduler
from app.services.notification_service import get_notification_service

# tick 级兜底超时缓冲（秒）：gather 并发等待最慢 run 后再加一段收尾余量。
# tick_timeout = run_timeout + TICK_TIMEOUT_BUFFER
TICK_TIMEOUT_BUFFER = 60.0

# ---------------------------------------------------------------------------
# 进程级 active run 防护
#
# 定时任务仅凭 started_at 超时无法区分「进程已死（应恢复）」与「进程活着但处理慢
# （不应恢复）」，多实例/重入下会双跑同一 run。本进程内已取得执行权的 run 记入
# ``_active_run_ids``，恢复扫描与正常处理均据此跳过。
# ---------------------------------------------------------------------------

_active_run_ids: set[str] = set()


def _try_acquire_run(run_id: str) -> bool:
    """尝试取得 run 在本进程的执行权；已持有返回 False。

    原子性依据：CPython 事件循环单线程调度协程，本函数为**不含 await 的同步
    check-and-set**，执行期间不会发生协程切换，因此 "in + add" 两步对其它协程
    而言是原子的。无需 asyncio.Lock（后者还带来跨 event loop 绑定风险）。
    """
    if run_id in _active_run_ids:
        return False
    _active_run_ids.add(run_id)
    return True


def _release_run(run_id: str) -> None:
    """释放 run 的本进程执行权（幂等）。"""
    _active_run_ids.discard(run_id)


def _clear_active_runs() -> None:
    """清空 active 集合（测试隔离用，避免模块级状态跨测试污染）。"""
    _active_run_ids.clear()


def _parse_run_rows(raw_rows: list, *, source: str) -> list[AgentRunRecord]:
    """把 repo 返回的 dict 行转换为 ``AgentRunRecord``。

    单行构造失败（缺必填列 / 类型不符）时记 error 并跳过该行，不中断整轮调度；
    返回可安全进行属性访问的模型列表。
    """
    records: list[AgentRunRecord] = []
    for raw in raw_rows:
        try:
            records.append(AgentRunRecord.model_validate(raw))
        except ValidationError as e:
            logger.error(
                f"🤖 {source} 行解析为 AgentRunRecord 失败，已跳过: {raw!r}: {e}"
            )
    return records


class LlmMatchScheduler(BaseScheduler):
    """LLM 匹配增强调度器（APScheduler 周期任务）"""

    JOB_ID = "llm_match_pending"
    DEFAULT_CRON = "*/1 * * * *"  # 每 60s
    DRIVER_NAME = "LlmMatch"

    # 每轮最多处理 N 条 pending，防堆积
    BATCH_SIZE = 5

    # ------------------------------------------------------------------
    # 抽象方法实现
    # ------------------------------------------------------------------

    def _is_enabled(self) -> bool:
        """启用条件：[sync] llm_match_assist=true 且 LLM api_key 非空。

        - 开关关 → False（不启动）
        - 开关开但 LLM 配置缺失 → False + 日志"LLM 配置缺失，匹配增强已禁用"
        """
        enabled = config_manager.get_sync_llm_match_config()["llm_match_assist"]
        if not enabled:
            return False
        llm_cfg = config_manager.get_llm_config()
        if not (llm_cfg.get("api_key") or "").strip():
            logger.info("LLM 配置缺失，匹配增强已禁用")
            return False
        return True

    def _get_driver_config(self) -> dict:
        """返回含 sync_interval（cron）的配置。

        cron 空串/非法时 **fail-loud**（见 ``_resolve_cron_or_fail``）：集中 getter
        仅对 ini 缺键提供 fallback 默认，显式空值/非法值会原样透出，由此处 fail-loud
        校验拦截；若此处不拦截，基类 ``_schedule_or_refresh_job`` 的
        ``or DEFAULT_CRON`` / ``_parse_cron`` 会静默回落默认值，掩盖用户的配置错误。
        """
        return {"sync_interval": self._resolve_cron_or_fail()}

    def _resolve_cron_or_fail(self) -> str:
        """读取并校验 cron；空串/非法表达式 → 记 error（含原值）+ 抛 ValueError。

        校验失败即不注册/刷新 job（异常在基类 add_job 之前抛出），由上层
        ``start`` 的 except 记录「调度器启动失败」；不回落 DEFAULT_CRON。
        """
        cron = config_manager.get_sync_llm_match_config()["llm_match_cron"]
        expr = str(cron).strip()
        if not expr:
            msg = f"🤖 llm_match_cron 为空，拒绝注册定时任务（原值={cron!r}）"
            logger.error(msg)
            raise ValueError(msg)
        if not self._is_valid_cron(expr):
            msg = f"🤖 llm_match_cron 非法，拒绝注册定时任务（原值={cron!r}）"
            logger.error(msg)
            raise ValueError(msg)
        return expr

    @staticmethod
    def _is_valid_cron(expr: str) -> bool:
        """校验 5 段式 cron 表达式是否可被 APScheduler 接受（不回落默认值）。"""
        parts = expr.split()
        if len(parts) != 5:
            return False
        try:
            CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
            )
        except ValueError:
            return False
        return True

    async def _run_sync_job(self) -> None:
        """单轮调度入口：启用检查 + tick 级兜底超时。

        - per-run 动态超时（主防线）在 ``_run_round`` 的 ``_consume`` 内实现；
        - 本方法以 ``wait_for`` 为整轮加 tick 兜底，防「fn 之外」的意外 hang
          让 tick 永不结束（``max_instances=1`` 下会连锁跳过后续触发、调度停摆）；
          超时取消会传播到 ``gather`` 内各 run（各自 finally 释放执行权），
          下一轮恢复扫描续跑。
        """
        if not self._is_enabled():
            return

        # llm_match_* 配置统一走集中 getter，本轮只读取一次后复用；每次调度周期
        # 重新读取，保证 Web 保存配置后的热更新语义不变。
        match_cfg = config_manager.get_sync_llm_match_config()
        llm_timeout = self._resolve_llm_timeout()
        # 单 run 动态上限（不新增配置项）：按 thinking_level × LLM/工具超时推算
        run_timeout = compute_match_run_timeout(match_cfg, llm_timeout)
        tick_timeout = run_timeout + TICK_TIMEOUT_BUFFER
        try:
            await asyncio.wait_for(
                self._run_round(match_cfg, run_timeout), timeout=tick_timeout
            )
        except asyncio.TimeoutError:
            # CancelledError 不在此捕获：取消已传播至内部协程，各自 finally 正常执行
            logger.error(
                f"🤖 调度轮整体超时（>{tick_timeout:.0f}s），已取消，下一轮重试"
            )

    @staticmethod
    def _resolve_llm_timeout() -> float:
        """读取 ``[llm] timeout``（秒）；读取异常 / 非法值 / 非正数 → 回退 60。

        与 ``config_manager.get_llm_config`` 的默认 timeout=60 保持一致；此处不引入
        新配置项，仅做健壮性兜底，保证超时推算不因脏配置失败。
        """
        try:
            raw = config_manager.get_llm_config().get("timeout", 60)
            value = float(raw)
        except Exception as e:
            logger.warning(f"🤖 读取 LLM timeout 失败，回退 60s: {type(e).__name__}")
            return 60.0
        if value <= 0:
            logger.warning(f"🤖 LLM timeout={raw!r} 非正数，回退 60s")
            return 60.0
        return value

    async def _run_round(self, match_cfg: dict, run_timeout: float) -> None:
        """单轮调度主体：清理 → 残留 span → 恢复扫描 → pending 共享信号量并发消费。

        ``run_timeout`` 为单 run 动态超时（``compute_match_run_timeout`` 推算）。
        ``_consume`` 内 ``asyncio.wait_for`` 超时会 cancel ``fn`` 协程，进而触发
        ``_recover_run`` / ``_process_run`` 的 ``finally: _release_run`` 释放执行权；
        run 仍为 ``processing`` → 下一 tick 恢复扫描断点续跑，同批其他 run 不受影响。
        """
        dbm = get_database_manager()
        repo = dbm.agent_runs

        # 1. 滑动窗口轮转清理（终态超窗 + 活性过期死行，单条 DELETE）
        retention_days = match_cfg["llm_match_retention_days"]
        try:
            deleted = repo.cleanup_expired(retention_days=retention_days)
        except Exception as e:
            logger.error(f"🤖 清理过期 agent_run 失败: {e}")
            deleted = 0
        if deleted and deleted > 0:
            logger.info(f"🤖 清理过期 agent_run {deleted} 条")

        # 2. 残留 span 清理（观测）：所属 run 已终结但自身未闭合的 running →
        #    error（进程中断遗留），best-effort，失败仅日志、不影响本轮调度
        try:
            marked = repo.mark_residual_running_spans()
            if marked:
                logger.info(f"🤖 清理未闭合 span {marked} 条（进程中断遗留）")
        except Exception as e:
            logger.warning(f"🤖 清理未闭合 span 失败（已忽略）: {e}")

        # 3. 恢复扫描（本进程已在处理（活着但慢）的 run 不得被重复捞起）
        recovery_timeout = match_cfg["llm_match_recovery_timeout_s"]
        try:
            stale_raw = repo.list_stale_processing(timeout_seconds=recovery_timeout)
        except Exception as e:
            logger.error(f"🤖 恢复扫描失败: {e}")
            stale_raw = []
        stale = [
            run
            for run in _parse_run_rows(stale_raw, source="恢复扫描")
            if run.run_id not in _active_run_ids
        ]

        # 4. 正常处理（limit 防堆积；切片双保险，避免上层 mock 返回超量）
        try:
            pending_raw = repo.list_pending(limit=self.BATCH_SIZE)[: self.BATCH_SIZE]
        except Exception as e:
            logger.error(f"🤖 列出 pending agent_run 失败: {e}")
            pending_raw = []
        pending = _parse_run_rows(pending_raw, source="pending")

        # 5. 并发消费：recover 先入列，与 pending 共享并发限额（信号量结构化并发）
        # getter 已做非法值回退，此处仅兜底非正数下限。
        limit = max(1, match_cfg["llm_match_concurrency"])
        sem = asyncio.Semaphore(limit)
        failures = 0

        async def _consume(fn, run: AgentRunRecord) -> None:
            """单条消费包装：受共享信号量约束，异常/超时隔离不影响同批其他 run。

            ``wait_for`` 超时会 cancel ``fn`` 协程（非抛异常）；``CancelledError``
            在 Python 3.8+ 属 ``BaseException``，不会被下方 ``except Exception``
            吞掉，故能正常传播至 ``fn`` 的 ``finally`` 释放执行权。
            """
            nonlocal failures
            async with sem:
                try:
                    await asyncio.wait_for(fn(run), timeout=run_timeout)
                except asyncio.TimeoutError:
                    failures += 1
                    logger.error(
                        f"🤖 run {run.run_id} 执行超时（>{run_timeout:.0f}s），"
                        f"已取消，留待恢复扫描续跑"
                    )
                except Exception as e:
                    failures += 1
                    logger.error(f"🤖 处理 run {run.run_id} 异常: {e}")

        tasks = [(self._recover_run, run) for run in stale]
        tasks += [(self._process_run, run) for run in pending]
        await asyncio.gather(*(_consume(fn, run) for fn, run in tasks))
        if failures:
            logger.error(f"🤖 本轮并发消费有 {failures} 条 run 处理异常")

    # ------------------------------------------------------------------
    # 恢复扫描
    # ------------------------------------------------------------------

    async def _recover_run(self, run: AgentRunRecord) -> None:
        """恢复单条崩溃遗留的 processing run。

        0. sync_record_id 尚未回填 → 跳过本轮，不占用执行权
        1. 取得本进程执行权（防并发恢复双跑；未取得直接跳过）
        2. 以**统一时间戳**对 started_at 做 CAS 刷新（expected=扫描到的值），
           防跨进程重复恢复：未抢到（值已被他人刷新/状态已变）则释放执行权返回
        3. sync_record 缺失 → mark_failed(error)
        4. 否则构造 bgm → 调用场景运行入口 ``get_scenario(task_type).continue_run``
           （replay / 补执行 / 续跑 / 落库与失败分流均在场景层内部完成）

        本方法只负责调度与兜底日志，不触碰场景层私有符号。
        """
        run_id = run.run_id
        # sync_record_id 由 persist 后回填，存在毫秒级窗口；未回填时跳过本轮
        # （不 mark_failed，等回填后下一轮再处理），且必须在 acquire 之前判断，
        # 避免占用（并随后释放）执行权造成同轮 pending 被误跳过。
        if not run.sync_record_id:
            logger.info(f"🤖 run {run_id} 的 sync_record_id 尚未回填，跳过本轮")
            return
        if not _try_acquire_run(run_id):
            logger.info(f"🤖 恢复 run {run_id} 跳过：本进程已在处理")
            return
        try:
            repo = get_database_manager().agent_runs

            # 恢复开始即以 CAS 抢占并刷新 started_at：expected 取扫描到的
            # started_at，仅当无其他执行者刷新过（值未变）才成功，避免两个
            # 调度进程在同一轮扫描后都续跑同一 run。
            ts = int(time.time())
            if not repo.refresh_started_at(
                run_id, ts, expected_started_at=run.started_at
            ):
                logger.info(f"🤖 恢复 run {run_id} 跳过：已被其他执行者抢占或状态已变")
                return

            sync_record = self._get_sync_record(run.sync_record_id)
            if sync_record is None:
                logger.warning(f"🤖 恢复 run {run_id} 关联 sync_record 缺失，标记失败")
                repo.mark_failed(
                    run_id, stop_reason="error", last_error="sync_record missing"
                )
                notify_agent_run_failed(
                    run_id,
                    task_type=run.task_type,
                    stop_reason="error",
                    last_error="sync_record missing",
                )
                return

            ctx = get_scenario(run.task_type).new_ctx(sync_record)
            try:
                # 续跑的场景内部逻辑（replay / 补执行 / loop / 落库 / 失败分流）
                # 全部收敛在 continue_run 内；此处不再触碰其私有符号。
                await get_scenario(run.task_type).continue_run(
                    run_id,
                    ctx=ctx,
                    notification_service=get_notification_service(),
                )
            except Exception as e:
                # 兜底：continue_run 内部已按可重试性完成计数/置终态（不向调用方抛），
                # 走到这里说明出现未预期的外层异常，仅记录日志、不重复计数。
                # 此处保留异常文本用于诊断（经评估该异常来自 LLM 处理链，不含凭据）。
                logger.error(f"🤖 恢复续跑 {run_id} 未预期异常: {e}")
        finally:
            _release_run(run_id)

    # ------------------------------------------------------------------
    # 正常处理
    # ------------------------------------------------------------------

    async def _process_run(self, run: AgentRunRecord) -> None:
        """处理单条 pending run：查 sync_record → 场景运行入口 run → 异常重试。

        入口取得本进程执行权，防止恢复扫描误捞正在处理的 run（并发化后尤为关键）；
        未取得执行权（本进程已有协程在处理）直接跳过，且不释放他人持有的执行权。
        """
        run_id = run.run_id
        # sync_record_id 由 persist 后回填，存在毫秒级窗口；未回填时跳过本轮
        # （不 mark_failed，等回填后下一轮再处理），避免无意义地占用执行权。
        if not run.sync_record_id:
            logger.info(f"🤖 run {run_id} 的 sync_record_id 尚未回填，跳过本轮")
            return
        if not _try_acquire_run(run_id):
            logger.info(f"🤖 处理 run {run_id} 跳过：本进程已在处理")
            return
        try:
            repo = get_database_manager().agent_runs

            sync_record = self._get_sync_record(run.sync_record_id)
            if sync_record is None:
                logger.warning(f"🤖 run {run_id} 关联 sync_record 缺失，标记失败")
                repo.mark_failed(
                    run_id, stop_reason="error", last_error="sync_record missing"
                )
                notify_agent_run_failed(
                    run_id,
                    task_type=run.task_type,
                    stop_reason="error",
                    last_error="sync_record missing",
                )
                return

            ctx = get_scenario(run.task_type).new_ctx(sync_record)
            try:
                # thinking_level 统一从集中配置读取并透传给场景运行入口
                # （config_override 由场景运行入口内部从同一配置读取）。
                match_cfg = config_manager.get_sync_llm_match_config()
                thinking_level = match_cfg["llm_match_thinking_level"]
                # atomic_claim / 状态流转 / 落库均在场景运行入口内部完成
                await get_scenario(run.task_type).run(
                    run_id,
                    ctx=ctx,
                    thinking_level=thinking_level,
                    notification_service=get_notification_service(),
                )
            except Exception as e:
                logger.error(f"🤖 处理 run {run_id} 异常: {e}")
                # 计数与达上限置终态在 repo 内单点事务完成，携带 last_error 供排查
                repo.increment_attempts(run_id, last_error=str(e))
        finally:
            _release_run(run_id)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _get_sync_record(self, sync_record_id: Any) -> dict | None:
        """按 id 查询同步记录；缺失或异常返回 None。"""
        if not sync_record_id:
            return None
        try:
            return get_database_manager().get_sync_record_by_id(int(sync_record_id))
        except Exception as e:
            logger.warning(f"🤖 查询 sync_record {sync_record_id} 失败: {e}")
            return None


# 全局单例
llm_match_scheduler = LlmMatchScheduler()
