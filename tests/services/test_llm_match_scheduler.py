"""LLM 匹配调度器测试（恢复 + 去重 + 清理）

通过 mock repo / llm_assist / config_manager 验证调度器行为，不触碰真实 DB 与 LLM。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, cast
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app.models.agent import AgentRunRecord
from app.services import llm_match_scheduler
from app.services.llm_match_scheduler import LlmMatchScheduler

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_active_runs():
    """每测试前后清空模块级 active run 集合，避免跨测试状态污染。"""
    llm_match_scheduler._clear_active_runs()
    yield
    llm_match_scheduler._clear_active_runs()


def _run_model(**overrides) -> AgentRunRecord:
    """构造合法的 AgentRunRecord（仅覆盖指定字段）。"""
    data = {"run_id": "r", "task_type": "match", "sync_record_id": 1}
    data.update(overrides)
    return AgentRunRecord.model_validate(data)


def _make_config(
    enabled: bool = True,
    api_key: str = "k",
    cron: str = "*/2 * * * *",
    concurrency: int = 3,
    retention_days: int = 30,
    recovery_timeout_s: int = 120,
):
    """构造 config_manager mock：llm_match_* 一律走集中 getter，get_llm_config 返回 api_key。"""
    cm = MagicMock()
    cm.get_sync_llm_match_config.return_value = {
        "llm_match_assist": enabled,
        "llm_match_cron": cron,
        "llm_match_retention_days": retention_days,
        "llm_match_max_iterations": "",
        "llm_match_recovery_timeout_s": recovery_timeout_s,
        "llm_match_concurrency": concurrency,
        "llm_match_thinking_level": "medium",
    }
    cm.get_llm_config.return_value = {"api_key": api_key}
    return cm


def _make_repo() -> MagicMock:
    """构造 agent_runs repo mock（各方法默认返回安全值）。"""
    repo = MagicMock()
    repo.cleanup_expired.return_value = 0
    repo.list_stale_processing.return_value = []
    repo.list_pending.return_value = []
    repo.increment_attempts.return_value = 1
    repo.refresh_started_at.return_value = True
    repo.mark_residual_running_spans.return_value = 0
    return repo


def _make_dbm(repo: MagicMock) -> MagicMock:
    dbm = MagicMock()
    dbm.agent_runs = repo
    return dbm


# ---------------------------------------------------------------------------
# 启用条件
# ---------------------------------------------------------------------------


def test_is_enabled_switch_off_returns_false():
    sched = LlmMatchScheduler()
    cm = _make_config(enabled=False, api_key="")
    with patch("app.services.llm_match_scheduler.config_manager", cm):
        assert sched._is_enabled() is False


def test_is_enabled_switch_on_and_llm_present_returns_true():
    sched = LlmMatchScheduler()
    cm = _make_config(enabled=True, api_key="sk-xxx")
    with patch("app.services.llm_match_scheduler.config_manager", cm):
        assert sched._is_enabled() is True


def test_is_enabled_switch_on_but_llm_missing_returns_false_and_logs():
    sched = LlmMatchScheduler()
    cm = _make_config(enabled=True, api_key="")
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        assert sched._is_enabled() is False
    log.info.assert_called_with("LLM 配置缺失，匹配增强已禁用")


def test_get_driver_config_returns_cron():
    sched = LlmMatchScheduler()
    cm = _make_config(cron="*/5 * * * *")
    with patch("app.services.llm_match_scheduler.config_manager", cm):
        cfg = sched._get_driver_config()
    assert cfg["sync_interval"] == "*/5 * * * *"


# ---------------------------------------------------------------------------
# cron 空串/非法值 fail-loud（不注册 job + error 日志含原值）
#
# 集中 getter 已移除默认值兜底（符合「配置类参数禁默认值兜底」沉淀），空串会直达
# 调度器；基类 _schedule_or_refresh_job 的 `or DEFAULT_CRON` 与 _parse_cron 的
# 静默降级会把配置错误掩盖为「以默认频率运行」。故在子类 _get_driver_config 前置
# 校验：空串/非法表达式 → 记 error（含原值）+ 抛 ValueError，绝不回落默认值。
# ---------------------------------------------------------------------------


def test_get_driver_config_empty_cron_fails_loud():
    """空串 cron → 抛 ValueError 且 error 日志可见（不回落 DEFAULT_CRON）。"""
    sched = LlmMatchScheduler()
    cm = _make_config(cron="")
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        with pytest.raises(ValueError):
            sched._get_driver_config()

    errors = [str(c.args[0]) for c in log.error.call_args_list]
    assert any("llm_match_cron" in m for m in errors), (
        f"空串 cron 应记 error 且指明配置项，实际 {errors}"
    )
    assert any("原值=''" in m for m in errors), (
        f"空串 cron 的 error 日志应含原值，实际 {errors}"
    )


def test_get_driver_config_blank_cron_fails_loud():
    """仅空白字符的 cron 视同空值，同样 fail-loud。"""
    sched = LlmMatchScheduler()
    cm = _make_config(cron="   ")
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        with pytest.raises(ValueError):
            sched._get_driver_config()

    errors = [str(c.args[0]) for c in log.error.call_args_list]
    assert any("llm_match_cron" in m for m in errors), (
        f"空白 cron 应记 error 且指明配置项，实际 {errors}"
    )
    assert any("原值='   '" in m for m in errors), (
        f"空白 cron 的 error 日志应含原值，实际 {errors}"
    )


def test_get_driver_config_invalid_cron_fails_loud_and_logs_raw_value():
    """段数非法的 cron（如 'not a cron'）→ 抛 ValueError，error 日志含原值。"""
    sched = LlmMatchScheduler()
    cm = _make_config(cron="not a cron")
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        with pytest.raises(ValueError):
            sched._get_driver_config()

    errors = [str(c.args[0]) for c in log.error.call_args_list]
    assert any("not a cron" in m for m in errors), (
        f"非法 cron 的 error 日志应含原值，实际 {errors}"
    )


def test_get_driver_config_out_of_range_field_fails_loud():
    """5 段但字段越界（minute=99）→ 同样 fail-loud，不静默降级默认。"""
    sched = LlmMatchScheduler()
    cm = _make_config(cron="99 99 99 99 99")
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        with pytest.raises(ValueError):
            sched._get_driver_config()

    errors = [str(c.args[0]) for c in log.error.call_args_list]
    assert any("99 99 99 99 99" in m for m in errors), (
        f"越界 cron 的 error 日志应含原值，实际 {errors}"
    )


def test_schedule_or_refresh_job_invalid_cron_does_not_register():
    """非法 cron → 注册流程抛错且绝不调用 add_job（不注册 job）。"""
    sched = LlmMatchScheduler()
    cm = _make_config(cron="not a cron")
    sched.scheduler = MagicMock()
    sched.scheduler.running = True
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        with pytest.raises(ValueError):
            sched._schedule_or_refresh_job()

    sched.scheduler.add_job.assert_not_called()


def test_schedule_or_refresh_job_empty_cron_does_not_register():
    """空串 cron → 注册流程抛错且绝不调用 add_job（不注册 job）。"""
    sched = LlmMatchScheduler()
    cm = _make_config(cron="")
    sched.scheduler = MagicMock()
    sched.scheduler.running = True
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        with pytest.raises(ValueError):
            sched._schedule_or_refresh_job()

    sched.scheduler.add_job.assert_not_called()


def test_schedule_or_refresh_job_valid_cron_registers_job():
    """正常 cron → 注册行为与现状一致（add_job 用本任务 JOB_ID）。"""
    sched = LlmMatchScheduler()
    cm = _make_config(cron="*/1 * * * *")
    sched.scheduler = MagicMock()
    sched.scheduler.running = True
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        sched._schedule_or_refresh_job()

    sched.scheduler.add_job.assert_called_once()
    _, kwargs = sched.scheduler.add_job.call_args
    assert kwargs["id"] == LlmMatchScheduler.JOB_ID


def test_disabled_run_sync_job_returns_early():
    sched = LlmMatchScheduler()
    cm = _make_config(enabled=False)
    repo = _make_repo()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
    ):
        import asyncio

        asyncio.run(sched._run_sync_job())
    # 禁用时不应做任何 repo 操作
    repo.cleanup_expired.assert_not_called()
    repo.list_pending.assert_not_called()


# ---------------------------------------------------------------------------
# 清理
# ---------------------------------------------------------------------------


def test_cleanup_deletes_expired_terminal_and_logs():
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.cleanup_expired.return_value = 3
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        import asyncio

        asyncio.run(sched._run_sync_job())
    # retention 值由集中 getter 提供，调度器不再自行解析
    repo.cleanup_expired.assert_called_once_with(retention_days=30)
    log.info.assert_any_call("🤖 清理过期 agent_run 3 条")


def test_cleanup_no_log_when_nothing_expired():
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.cleanup_expired.return_value = 0
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        import asyncio

        asyncio.run(sched._run_sync_job())
    repo.cleanup_expired.assert_called_once()
    # 删除数为 0 时不打清理日志
    for call in log.info.call_args_list:
        assert "清理终态过期 agent_run" not in call.args[0]


# ---------------------------------------------------------------------------
# 残留 span 清理（观测：进程中断遗留的 running → error）
# ---------------------------------------------------------------------------


def test_run_round_marks_residual_running_spans_and_logs():
    """调度轮清理未闭合 span；受影响 >0 时记 info。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.mark_residual_running_spans.return_value = 2
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        import asyncio

        asyncio.run(sched._run_sync_job())
    repo.mark_residual_running_spans.assert_called_once()
    log.info.assert_any_call("🤖 清理未闭合 span 2 条（进程中断遗留）")


def test_run_round_residual_span_failure_is_ignored():
    """清理异常仅记 warning，本轮调度继续（恢复扫描照常执行）。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.mark_residual_running_spans.side_effect = RuntimeError("db boom")
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        import asyncio

        asyncio.run(sched._run_sync_job())
    repo.list_stale_processing.assert_called_once()
    assert any("清理未闭合 span 失败" in c.args[0] for c in log.warning.call_args_list)


# ---------------------------------------------------------------------------
# 恢复扫描
# ---------------------------------------------------------------------------


def test_recovery_missing_sync_record_marks_failed():
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.list_stale_processing.return_value = [
        {"run_id": "r1", "task_type": "match", "sync_record_id": 42}
    ]
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_get_sync_record", return_value=None),
        patch(
            "app.services.agent.run.registry.ScenarioRuntime.continue_run",
            new=AsyncMock(),
        ) as cont,
    ):
        import asyncio

        asyncio.run(sched._run_sync_job())

    repo.refresh_started_at.assert_called_once()
    ts_call_args = repo.refresh_started_at.call_args[0]
    assert ts_call_args[0] == "r1"
    assert isinstance(ts_call_args[1], int)
    # recovery 超时值由集中 getter 提供，调度器不再自行解析
    repo.list_stale_processing.assert_called_once_with(timeout_seconds=120)
    repo.mark_failed.assert_called_once_with(
        "r1", stop_reason="error", last_error="sync_record missing"
    )
    cont.assert_not_awaited()


def test_recovery_present_sync_record_continues():
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.list_stale_processing.return_value = [
        {"run_id": "r1", "task_type": "match", "sync_record_id": 42}
    ]
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_get_sync_record", return_value={"id": 42, "title": "x"}),
        patch("app.services.matching.llm_assist.build_bgm", return_value=MagicMock()),
        patch(
            "app.services.agent.run.registry.ScenarioRuntime.continue_run",
            new=AsyncMock(),
        ) as cont,
    ):
        import asyncio

        asyncio.run(sched._run_sync_job())

    repo.refresh_started_at.assert_called_once()
    ts_call_args = repo.refresh_started_at.call_args[0]
    assert ts_call_args[0] == "r1"
    assert isinstance(ts_call_args[1], int)
    cont.assert_awaited_once()


def test_recover_run_calls_continue_run_single_entry():
    """_recover_run 只调场景层公开单一入口 continue_run，并透传 run_id/sync_record/bgm/通知服务。"""
    sched = LlmMatchScheduler()
    repo = _make_repo()
    fake_svc = MagicMock()
    fake_bgm = MagicMock()
    sync_record = {"id": 42, "title": "x"}
    cont = AsyncMock()
    with (
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_get_sync_record", return_value=sync_record),
        patch("app.services.matching.llm_assist.build_bgm", return_value=fake_bgm),
        patch(
            "app.services.llm_match_scheduler.get_notification_service",
            return_value=fake_svc,
        ),
        patch(
            "app.services.agent.run.registry.ScenarioRuntime.continue_run",
            new=cont,
        ),
    ):
        asyncio.run(sched._recover_run(_run_model(run_id="A", sync_record_id=42)))

    cont.assert_awaited_once_with("A", ctx=ANY, notification_service=fake_svc)
    # ctx 由场景侧 new_ctx 自建（通用层不感知其结构）：只断言它携带本次 sync_record
    ctx = cont.await_args.kwargs["ctx"]
    assert ctx.sync_record is sync_record


# ---------------------------------------------------------------------------
# 正常处理
# ---------------------------------------------------------------------------


def test_process_pending_calls_llm_assist_run_per_item():
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    runs = [
        {"run_id": "a", "task_type": "match", "sync_record_id": 1},
        {"run_id": "b", "task_type": "match", "sync_record_id": 2},
        {"run_id": "c", "task_type": "match", "sync_record_id": 3},
    ]
    repo.list_pending.return_value = runs
    run_mock = AsyncMock(return_value="succeeded")
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_get_sync_record", return_value={"id": 1, "title": "t"}),
        patch("app.services.matching.llm_assist.build_bgm", return_value=MagicMock()),
        patch("app.services.agent.run.registry.ScenarioRuntime.run", run_mock),
    ):
        import asyncio

        asyncio.run(sched._run_sync_job())

    assert run_mock.await_count == 3


def test_process_pending_respects_batch_limit_five():
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    # 即便 repo 返回 7 条，调度器也只处理 5 条
    # （sync_record_id 用正数：0/空 现表示"尚未回填"，会按规则跳过）
    repo.list_pending.return_value = [
        {"run_id": f"r{i}", "task_type": "match", "sync_record_id": i + 1}
        for i in range(7)
    ]
    run_mock = AsyncMock(return_value="succeeded")
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_get_sync_record", return_value={"id": 1, "title": "t"}),
        patch("app.services.matching.llm_assist.build_bgm", return_value=MagicMock()),
        patch("app.services.agent.run.registry.ScenarioRuntime.run", run_mock),
    ):
        import asyncio

        asyncio.run(sched._run_sync_job())

    repo.list_pending.assert_called_once_with(limit=5)
    assert run_mock.await_count == 5


def test_run_exception_increments_attempts():
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.list_pending.return_value = [
        {"run_id": "a", "task_type": "match", "sync_record_id": 1}
    ]
    run_mock = AsyncMock(side_effect=Exception("boom"))
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_get_sync_record", return_value={"id": 1, "title": "t"}),
        patch("app.services.matching.llm_assist.build_bgm", return_value=MagicMock()),
        patch("app.services.agent.run.registry.ScenarioRuntime.run", run_mock),
    ):
        import asyncio

        asyncio.run(sched._run_sync_job())

    repo.increment_attempts.assert_called_once_with("a", last_error="boom")


def test_run_exception_attempts_reach_three_single_point_no_double_mark_failed():
    """达上限由 repo.increment_attempts 单点置终态，调度器不再二次 mark_failed。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.list_pending.return_value = [
        {"run_id": "a", "task_type": "match", "sync_record_id": 1}
    ]
    repo.increment_attempts.return_value = 3
    run_mock = AsyncMock(side_effect=Exception("boom"))
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_get_sync_record", return_value={"id": 1, "title": "t"}),
        patch("app.services.matching.llm_assist.build_bgm", return_value=MagicMock()),
        patch("app.services.agent.run.registry.ScenarioRuntime.run", run_mock),
    ):
        import asyncio

        asyncio.run(sched._run_sync_job())

    repo.increment_attempts.assert_called_once_with("a", last_error="boom")
    # 终态写入收敛到 increment_attempts 单点事务，外层不得二次 mark_failed
    repo.mark_failed.assert_not_called()


def test_exception_in_one_run_does_not_stop_others():
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    runs = [
        {"run_id": "a", "task_type": "match", "sync_record_id": 1},
        {"run_id": "b", "task_type": "match", "sync_record_id": 2},
    ]
    repo.list_pending.return_value = runs
    # 第一条抛错，第二条成功；两条都应被处理
    run_mock = AsyncMock(side_effect=[Exception("boom"), "succeeded"])
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_get_sync_record", return_value={"id": 1, "title": "t"}),
        patch("app.services.matching.llm_assist.build_bgm", return_value=MagicMock()),
        patch("app.services.agent.run.registry.ScenarioRuntime.run", run_mock),
    ):
        import asyncio

        asyncio.run(sched._run_sync_job())

    # 两条都尝试处理（第一条异常被捕获后仍继续）
    assert run_mock.await_count == 2
    repo.increment_attempts.assert_called_once_with("a", last_error="boom")


# ---------------------------------------------------------------------------
# 正常处理路径透传 thinking_level（统一从集中配置读取）
# ---------------------------------------------------------------------------


def test_process_run_passes_thinking_level_from_config():
    sched = LlmMatchScheduler()
    repo = _make_repo()
    run = _run_model(run_id="a", sync_record_id=1)

    cm = MagicMock()
    cm.get_sync_llm_match_config.return_value = {
        "llm_match_thinking_level": "high",
        "llm_match_max_iterations": "",
    }
    run_mock = AsyncMock(return_value="succeeded")
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_get_sync_record", return_value={"id": 1, "title": "t"}),
        patch("app.services.matching.llm_assist.build_bgm", return_value=MagicMock()),
        patch("app.services.agent.run.registry.ScenarioRuntime.run", run_mock),
    ):
        asyncio.run(sched._process_run(run))

    run_mock.assert_awaited_once()
    _, kwargs = run_mock.call_args
    assert kwargs.get("thinking_level") == "high"


def test_process_run_passes_notification_service_to_scenario_runtime_run():
    """生产路径：_process_run 调 ScenarioRuntime.run 时必须传入非 None 的 notification_service。"""
    sched = LlmMatchScheduler()
    repo = _make_repo()
    run = _run_model(run_id="a", sync_record_id=1)

    cm = MagicMock()
    cm.get_sync_llm_match_config.return_value = {
        "llm_match_thinking_level": "medium",
        "llm_match_max_iterations": "",
    }
    run_mock = AsyncMock(return_value="succeeded")
    fake_svc = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_get_sync_record", return_value={"id": 1, "title": "t"}),
        patch("app.services.matching.llm_assist.build_bgm", return_value=MagicMock()),
        patch("app.services.agent.run.registry.ScenarioRuntime.run", run_mock),
        patch(
            "app.services.llm_match_scheduler.get_notification_service",
            return_value=fake_svc,
        ),
    ):
        asyncio.run(sched._process_run(run))

    run_mock.assert_awaited_once()
    _, kwargs = run_mock.call_args
    assert kwargs.get("notification_service") is fake_svc


# ---------------------------------------------------------------------------
# 模块级 active run 重入防护（跳过运行中 run / 统一时间戳抢占 / 互斥 / 释放）
# ---------------------------------------------------------------------------


def test_recovery_scan_skips_active_run():
    """场景1：run A 在本进程 active 集合 → 恢复扫描不调用 _recover_run。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.list_stale_processing.return_value = [
        {"run_id": "A", "task_type": "match", "sync_record_id": 42}
    ]
    llm_match_scheduler._active_run_ids.add("A")
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_recover_run", new=AsyncMock()) as recover,
    ):
        asyncio.run(sched._run_sync_job())

    recover.assert_not_awaited()
    repo.refresh_started_at.assert_not_called()


def test_recover_run_passes_caller_timestamp_to_refresh():
    """场景2：恢复开始以调用方统一时间戳刷新，而非仓储内部取 now。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.time.time", return_value=1700000000),
        patch.object(sched, "_get_sync_record", return_value={"id": 42, "title": "x"}),
        patch("app.services.matching.llm_assist.build_bgm", return_value=MagicMock()),
        patch(
            "app.services.agent.run.registry.ScenarioRuntime.continue_run",
            new=AsyncMock(),
        ),
    ):
        asyncio.run(sched._recover_run(_run_model(run_id="A", sync_record_id=42)))

    # CAS：expected_started_at 取扫描到的 run.started_at（默认 0）
    repo.refresh_started_at.assert_called_once_with(
        "A", 1700000000, expected_started_at=0
    )


def test_concurrent_recover_same_run_only_one_acquires():
    """场景3：同一 run 被两个并发恢复触发 → 仅一个取得执行权，续跑只发生一次。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()

    cont_calls = {"n": 0}
    events: dict = {}

    async def _gated_continue(run_id, ctx, *, notification_service=None):
        cont_calls["n"] += 1
        events["started"].set()
        await events["release"].wait()

    async def _go():
        # Event 必须在运行中的 loop 内创建（Python 3.9 会绑定创建时的 loop）
        events["started"] = asyncio.Event()
        events["release"] = asyncio.Event()
        run = _run_model(run_id="A", sync_record_id=42)
        tasks = asyncio.gather(
            sched._recover_run(run),
            sched._recover_run(run),
        )
        await events["started"].wait()
        events["release"].set()
        await tasks

    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.time.time", return_value=1700000001),
        patch.object(sched, "_get_sync_record", return_value={"id": 42, "title": "x"}),
        patch("app.services.matching.llm_assist.build_bgm", return_value=MagicMock()),
        patch(
            "app.services.agent.run.registry.ScenarioRuntime.continue_run",
            side_effect=_gated_continue,
        ),
    ):
        asyncio.run(_go())

    assert cont_calls["n"] == 1, "并发恢复同一 run 只应有一次续跑"
    repo.refresh_started_at.assert_called_once_with(
        "A", 1700000001, expected_started_at=0
    )
    assert "A" not in llm_match_scheduler._active_run_ids


def test_recover_run_cas_loser_skips_continuation_and_releases_active():
    """CAS 刷新失败（已被其他执行者抢占/状态已变）→ 跳过续跑并释放执行权。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.refresh_started_at.return_value = False
    cont = AsyncMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.time.time", return_value=1700000009),
        patch.object(sched, "_get_sync_record", return_value={"id": 42, "title": "x"}),
        patch("app.services.matching.llm_assist.build_bgm", return_value=MagicMock()),
        patch(
            "app.services.agent.run.registry.ScenarioRuntime.continue_run",
            new=cont,
        ),
    ):
        asyncio.run(
            sched._recover_run(
                _run_model(run_id="A", sync_record_id=42, started_at=111)
            )
        )

    # CAS 必须带扫描行携带的 expected_started_at，供仓储做原子比对
    repo.refresh_started_at.assert_called_once_with(
        "A", 1700000009, expected_started_at=111
    )
    cont.assert_not_awaited()
    assert "A" not in llm_match_scheduler._active_run_ids, "CAS 失败后必须释放执行权"


def test_recover_run_releases_after_unexpected_exception():
    """场景4：continue_run 抛未预期异常 → 兜底记 error、释放执行权、不重复计数。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.logger", log),
        patch("app.services.llm_match_scheduler.time.time", return_value=1700000002),
        patch.object(sched, "_get_sync_record", return_value={"id": 42, "title": "x"}),
        patch("app.services.matching.llm_assist.build_bgm", return_value=MagicMock()),
        patch(
            "app.services.agent.run.registry.ScenarioRuntime.continue_run",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        # 未预期异常被兜底消化，不向调用方抛出
        asyncio.run(sched._recover_run(_run_model(run_id="A", sync_record_id=42)))

    errors = [str(c.args[0]) for c in log.error.call_args_list]
    assert any("boom" in m for m in errors), f"兜底应记 error，实际 {errors}"
    # 计数在 continue_run 内部完成，调度器兜底不得重复计数
    repo.increment_attempts.assert_not_called()
    assert "A" not in llm_match_scheduler._active_run_ids, "异常后必须释放执行权"
    # 释放后可再次取得执行权（下一轮可恢复）
    assert llm_match_scheduler._try_acquire_run("A") is True


def test_process_run_skips_active_run():
    """_process_run 对正在本进程处理的 run 也应跳过，防恢复误捞双跑。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    llm_match_scheduler._active_run_ids.add("A")
    run_mock = AsyncMock(return_value="succeeded")
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_get_sync_record", return_value={"id": 42, "title": "x"}),
        patch("app.services.agent.run.registry.ScenarioRuntime.run", run_mock),
    ):
        asyncio.run(sched._process_run(_run_model(run_id="A", sync_record_id=42)))

    run_mock.assert_not_awaited()
    assert "A" in llm_match_scheduler._active_run_ids, "跳过时不应误删他人持有的执行权"


# ---------------------------------------------------------------------------
# recover/pending 共享信号量并发消费
# ---------------------------------------------------------------------------


def test_stale_and_pending_share_concurrency_limit():
    """1 stale + 3 pending，limit=2 → 并发峰值恰好 2，且 4 条都被处理。"""
    sched = LlmMatchScheduler()
    cm = _make_config(concurrency=2)
    repo = _make_repo()
    repo.list_stale_processing.return_value = [
        {"run_id": "s1", "task_type": "match", "sync_record_id": 10}
    ]
    repo.list_pending.return_value = [
        {"run_id": f"p{i}", "task_type": "match", "sync_record_id": i + 1}
        for i in range(3)
    ]
    state = {"current": 0, "peak": 0}
    processed: list[str] = []

    async def _fn(run):
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        await asyncio.sleep(0)
        state["current"] -= 1
        processed.append(run.run_id)

    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_recover_run", side_effect=_fn),
        patch.object(sched, "_process_run", side_effect=_fn),
    ):
        asyncio.run(sched._run_sync_job())

    assert state["peak"] == 2, f"共享限额并发峰值应恰好为 2，实际 {state['peak']}"
    assert sorted(processed) == ["p0", "p1", "p2", "s1"], (
        f"4 条 run 都应被处理，实际 {sorted(processed)}"
    )


def test_recover_and_pending_consume_concurrently():
    """recover 任务尚未结束时 pending 任务已开始执行（非严格先后）。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.list_stale_processing.return_value = [
        {"run_id": "stale1", "task_type": "match", "sync_record_id": 1}
    ]
    repo.list_pending.return_value = [
        {"run_id": "p1", "task_type": "match", "sync_record_id": 2}
    ]

    observed = {"recover_saw_pending_start": False}

    async def _go():
        recover_entered = asyncio.Event()
        pending_entered = asyncio.Event()

        async def _recover(run):
            recover_entered.set()
            try:
                await asyncio.wait_for(pending_entered.wait(), timeout=2.0)
                observed["recover_saw_pending_start"] = True
            except asyncio.TimeoutError:
                observed["recover_saw_pending_start"] = False

        async def _process(run):
            await recover_entered.wait()
            pending_entered.set()

        with (
            patch("app.services.llm_match_scheduler.config_manager", cm),
            patch(
                "app.services.llm_match_scheduler.get_database_manager",
                return_value=_make_dbm(repo),
            ),
            patch.object(sched, "_recover_run", side_effect=_recover),
            patch.object(sched, "_process_run", side_effect=_process),
        ):
            await sched._run_sync_job()

    asyncio.run(_go())

    assert observed["recover_saw_pending_start"] is True, (
        "recover 与 pending 应并发消费：pending 开始时 recover 尚未结束"
    )


def test_consume_exception_isolated_and_logged_error():
    """某 run 处理抛异常 → 记 error 且不影响同批其他 run 完成。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.list_pending.return_value = [
        {"run_id": "a", "task_type": "match", "sync_record_id": 1},
        {"run_id": "b", "task_type": "match", "sync_record_id": 2},
    ]
    finished: list[str] = []

    async def _fn(run):
        if run.run_id == "a":
            raise RuntimeError("boom")
        finished.append(run.run_id)

    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.logger", log),
        patch.object(sched, "_process_run", side_effect=_fn),
    ):
        asyncio.run(sched._run_sync_job())

    assert finished == ["b"], "同批其他 run 不应被异常中断"
    error_msgs = [str(c.args[0]) for c in log.error.call_args_list]
    assert any("boom" in m for m in error_msgs), (
        f"异常应被 error 级记录，实际 {error_msgs}"
    )
    assert any("1 条" in m for m in error_msgs), (
        f"应对本轮失败数计数并记录，实际 {error_msgs}"
    )


# ---------------------------------------------------------------------------
# 并发配置解析（非法/缺失回退 3，非正数下限 1）
# ---------------------------------------------------------------------------


def _measure_peak_concurrency(concurrency_value: int, n_pending: int = 3) -> int:
    sched = LlmMatchScheduler()
    cm = _make_config(concurrency=concurrency_value)
    repo = _make_repo()
    repo.list_pending.return_value = [
        {"run_id": f"p{i}", "task_type": "match", "sync_record_id": i + 1}
        for i in range(n_pending)
    ]
    state = {"current": 0, "peak": 0}

    async def _fn(run):
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        await asyncio.sleep(0)
        state["current"] -= 1

    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_process_run", side_effect=_fn),
    ):
        asyncio.run(sched._run_sync_job())
    return state["peak"]


@pytest.mark.parametrize(
    "value,expected_peak",
    [
        (3, 3),  # 集中 getter 默认 3 → 峰值 3
        (0, 1),  # 0 → 下限 1
        (-4, 1),  # 负数 → 下限 1
        (2, 2),  # 合法值生效
        (9, 3),  # 大于任务数 → 峰值受任务数限制
    ],
)
def test_concurrency_config_parsing(value, expected_peak):
    """调度器消费集中 getter 的 llm_match_concurrency，非正数下限 1。

    非法/缺失值回退 3 属 getter 职责，见 tests/core/test_config.py。
    """
    assert _measure_peak_concurrency(value, n_pending=3) == expected_peak


# ---------------------------------------------------------------------------
# 三处调度失败日志应为 error 级
# ---------------------------------------------------------------------------


def _run_job_with_logger(repo: MagicMock) -> MagicMock:
    sched = LlmMatchScheduler()
    cm = _make_config()
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        asyncio.run(sched._run_sync_job())
    return log


def test_cleanup_failure_logged_at_error_level():
    repo = _make_repo()
    repo.cleanup_expired.side_effect = RuntimeError("cleanup down")
    log = _run_job_with_logger(repo)

    errors = [str(c.args[0]) for c in log.error.call_args_list]
    assert any("清理过期" in m and "cleanup down" in m for m in errors), (
        f"清理失败应记 error，实际 {errors}"
    )
    debugs = [str(c.args[0]) for c in log.debug.call_args_list]
    assert not any("清理过期" in m for m in debugs), "清理失败不应停留在 debug 级"


def test_stale_scan_failure_logged_at_error_level():
    repo = _make_repo()
    repo.list_stale_processing.side_effect = RuntimeError("stale down")
    log = _run_job_with_logger(repo)

    errors = [str(c.args[0]) for c in log.error.call_args_list]
    assert any("恢复扫描" in m and "stale down" in m for m in errors), (
        f"恢复扫描失败应记 error，实际 {errors}"
    )
    debugs = [str(c.args[0]) for c in log.debug.call_args_list]
    assert not any("恢复扫描" in m for m in debugs), "恢复扫描失败不应停留在 debug 级"


def test_list_pending_failure_logged_at_error_level():
    repo = _make_repo()
    repo.list_pending.side_effect = RuntimeError("pending down")
    log = _run_job_with_logger(repo)

    errors = [str(c.args[0]) for c in log.error.call_args_list]
    assert any("pending" in m and "pending down" in m for m in errors), (
        f"list_pending 失败应记 error，实际 {errors}"
    )
    debugs = [str(c.args[0]) for c in log.debug.call_args_list]
    assert not any("pending" in m for m in debugs), (
        "list_pending 失败不应停留在 debug 级"
    )


# ---------------------------------------------------------------------------
# sync_record_id 尚未回填（前移窗口）→ 跳过本轮
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("empty_id", [None, 0])
def test_process_run_skips_run_without_sync_record_id(empty_id):
    """_process_run：sync_record_id 空 → 跳过，不 acquire、不 mark_failed、不调 run。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    acquire_spy = MagicMock(return_value=True)
    run_mock = AsyncMock(return_value="succeeded")
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.logger", log),
        patch("app.services.llm_match_scheduler._try_acquire_run", acquire_spy),
        patch("app.services.agent.run.registry.ScenarioRuntime.run", run_mock),
    ):
        asyncio.run(sched._process_run(_run_model(run_id="a", sync_record_id=empty_id)))

    acquire_spy.assert_not_called()
    run_mock.assert_not_awaited()
    repo.mark_failed.assert_not_called()
    infos = [str(c.args[0]) for c in log.info.call_args_list]
    assert any("sync_record_id" in m for m in infos), (
        f"跳过时应记 info 说明原因，实际 {infos}"
    )


@pytest.mark.parametrize("empty_id", [None, 0])
def test_recover_run_skips_run_without_sync_record_id(empty_id):
    """_recover_run：sync_record_id 空 → 在 acquire 前跳过，不刷新、不 mark_failed。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    acquire_spy = MagicMock(return_value=True)
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.logger", log),
        patch("app.services.llm_match_scheduler._try_acquire_run", acquire_spy),
        patch(
            "app.services.agent.run.registry.ScenarioRuntime.continue_run",
            new=AsyncMock(),
        ),
        patch.object(sched, "_get_sync_record", return_value={"id": 1}),
    ):
        asyncio.run(sched._recover_run(_run_model(run_id="A", sync_record_id=empty_id)))

    acquire_spy.assert_not_called()
    repo.refresh_started_at.assert_not_called()
    repo.mark_failed.assert_not_called()
    assert "A" not in llm_match_scheduler._active_run_ids
    infos = [str(c.args[0]) for c in log.info.call_args_list]
    assert any("sync_record_id" in m for m in infos), (
        f"跳过时应记 info 说明原因，实际 {infos}"
    )


# ---------------------------------------------------------------------------
# run 参数 BaseModel 化 + 行解析防御 + import 规范化
# ---------------------------------------------------------------------------


def test_agent_run_record_aligns_schema_allows_extra_and_null_sync_record():
    """AgentRunRecord 对齐 agent_runs 列，允许未来新增列，sync_record_id 可空。"""
    rec = AgentRunRecord.model_validate(
        {
            "id": 1,
            "run_id": "r1",
            "task_type": "match",
            "sync_record_id": None,
            "business_key": "k",
            "status": "pending",
            "stop_reason": "",
            "attempts": 0,
            "total_attempts": 0,
            "last_attempt_at": 0,
            "last_error": None,
            "total_tokens": 0,
            "started_at": 0,
            "ended_at": 0,
            "created_at": 0,
            "future_column": "x",  # 未来新增列不应导致校验失败
        }
    )
    assert rec.run_id == "r1"
    assert rec.task_type == "match"
    assert rec.sync_record_id is None
    assert cast("Any", rec).future_column == "x", "extra='allow' 应保留未来新增列"


def test_run_sync_job_converts_rows_to_agent_run_record():
    """repo 返回的 dict 行被转换为 AgentRunRecord 后交给处理函数。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.list_stale_processing.return_value = [
        {"run_id": "s1", "task_type": "match", "sync_record_id": 10}
    ]
    repo.list_pending.return_value = [
        {"run_id": "p1", "task_type": "match", "sync_record_id": 11}
    ]
    seen: list = []

    async def _capture(run):
        seen.append(run)

    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_recover_run", side_effect=_capture),
        patch.object(sched, "_process_run", side_effect=_capture),
    ):
        asyncio.run(sched._run_sync_job())

    assert len(seen) == 2
    assert all(isinstance(r, AgentRunRecord) for r in seen), seen
    assert {r.run_id for r in seen} == {"s1", "p1"}


def test_run_sync_job_skips_unparseable_row_and_logs_error():
    """非法行构造失败 → error 日志 + 跳过，不影响同批其他 run。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    # 第二行缺 run_id/task_type → 校验失败
    repo.list_pending.return_value = [
        {"run_id": "good", "task_type": "match", "sync_record_id": 1},
        {"sync_record_id": 2},
    ]
    processed: list[str] = []

    async def _capture(run):
        processed.append(run.run_id)

    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.logger", log),
        patch.object(sched, "_process_run", side_effect=_capture),
    ):
        asyncio.run(sched._run_sync_job())

    assert processed == ["good"], "非法行应被跳过，合法行仍处理"
    errors = [str(c.args[0]) for c in log.error.call_args_list]
    assert any("解析" in m for m in errors), f"非法行应记 error，实际 {errors}"


def test_build_bgm_exception_log_redacts_secret_keeps_user_and_type():
    """构造 BangumiApi 异常 → warning 不含原始异常文本（防 access_token 泄漏），
    但保留可诊断的用户维度与异常类型。

    **归属变更**：bgm 构造已从调度器下沉到匹配场景（``llm_assist.build_bgm``），
    通用调度器不再接触凭据；脱敏责任随之迁到场景侧。
    """
    from app.services.matching import llm_assist

    log = MagicMock()
    secret = "SECRET-ACCESS-TOKEN-abc123"
    cm = MagicMock()
    cm.get_dev_http_snapshot.return_value = {
        "script_proxy": "",
        "ssl_verify": True,
        "bgm_api_proxy": "",
        "bgm_next_proxy": "",
        "ech_mode": False,
    }
    with (
        patch(
            "app.services.matching.llm_assist.get_primary_bangumi_config",
            return_value={"username": "u1", "access_token": secret},
        ),
        patch("app.services.matching.llm_assist.config_manager", cm),
        patch(
            "app.services.matching.llm_assist.BangumiApi",
            side_effect=ValueError(f"invalid access_token={secret}"),
        ),
        patch("app.services.matching.llm_assist.logger", log),
    ):
        result = llm_assist.build_bgm("alice")

    assert result is None
    log.warning.assert_called_once()
    msg = log.warning.call_args[0][0]
    assert secret not in msg, "日志不得包含原始异常文本（可能含 access_token）"
    assert "ValueError" in msg, "应保留异常类型便于诊断"
    assert "alice" in msg, "应保留用户维度便于定位"


# ---------------------------------------------------------------------------
# 超时闭环：per-run 动态超时（主防线）+ tick 级兜底
#
# 缺陷背景：run 协程 hang（不抛异常、不返回）→ finally: _release_run 永不执行
# → 执行权泄漏 + tick 永不结束（max_instances=1）→ 整个 match 调度永久停摆。
# 修复：wait_for 超时会 cancel 协程 → finally 释放执行权 → 下一 tick 恢复续跑。
# ---------------------------------------------------------------------------


def test_run_sync_job_passes_dynamic_run_timeout_to_round():
    """动态推算 run_timeout（medium + llm timeout 60 → 570）并下传给调度轮。"""
    sched = LlmMatchScheduler()
    cm = _make_config()  # thinking medium；get_llm_config 不含 timeout → 默认 60
    spy = MagicMock(return_value=570.0)
    round_mock = AsyncMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch("app.services.llm_match_scheduler.compute_match_run_timeout", spy),
        patch.object(sched, "_run_round", round_mock),
    ):
        asyncio.run(sched._run_sync_job())

    spy.assert_called_once()
    called_cfg, called_llm_timeout = spy.call_args.args
    assert called_cfg["llm_match_thinking_level"] == "medium"
    assert called_llm_timeout == 60.0
    round_mock.assert_awaited_once()
    assert round_mock.call_args.args[1] == 570.0, "动态 run_timeout 应传入调度轮"


def test_run_sync_job_llm_timeout_invalid_falls_back_to_60():
    """[llm] timeout 非法值 → 回退 60 再参与推算。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    cm.get_llm_config.return_value = {"api_key": "k", "timeout": "not-a-number"}
    spy = MagicMock(return_value=570.0)
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch("app.services.llm_match_scheduler.compute_match_run_timeout", spy),
        patch.object(sched, "_run_round", AsyncMock()),
    ):
        asyncio.run(sched._run_sync_job())

    assert spy.call_args.args[1] == 60.0


def test_run_sync_job_llm_timeout_getter_exception_falls_back_to_60():
    """get_llm_config 抛异常 → 回退 60（不因超时推算失败中断调度）。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    # 第 1 次供 _is_enabled 启用检查；第 2 次（_resolve_llm_timeout）抛异常
    cm.get_llm_config.side_effect = [{"api_key": "k"}, RuntimeError("boom")]
    spy = MagicMock(return_value=570.0)
    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch("app.services.llm_match_scheduler.compute_match_run_timeout", spy),
        patch.object(sched, "_run_round", AsyncMock()),
        patch("app.services.llm_match_scheduler.logger", log),
    ):
        asyncio.run(sched._run_sync_job())

    assert spy.call_args.args[1] == 60.0
    assert log.warning.called, "回退应记 warning 便于诊断"


def test_per_run_timeout_cancels_hang_releases_active_and_isolates():
    """单 run hang → 超时取消、释放执行权、同批其他 run 不受影响。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.list_pending.return_value = [
        {"run_id": "hang", "task_type": "match", "sync_record_id": 1},
        {"run_id": "ok", "task_type": "match", "sync_record_id": 2},
    ]
    finished: list[str] = []

    async def _run_side_effect(run_id, **kwargs):
        if run_id == "hang":
            await asyncio.Event().wait()  # 永不 set → hang
        finished.append(run_id)

    log = MagicMock()
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch("app.services.llm_match_scheduler.logger", log),
        patch(
            "app.services.llm_match_scheduler.compute_match_run_timeout",
            return_value=0.05,
        ),
        patch.object(sched, "_get_sync_record", return_value={"id": 1, "title": "t"}),
        patch("app.services.matching.llm_assist.build_bgm", return_value=MagicMock()),
        patch(
            "app.services.agent.run.registry.ScenarioRuntime.run",
            side_effect=_run_side_effect,
        ),
    ):
        asyncio.run(sched._run_sync_job())

    assert "hang" not in finished, "hang run 不应完成"
    assert "ok" in finished, "同批其他 run 应正常完成（隔离）"
    assert "hang" not in llm_match_scheduler._active_run_ids, (
        "超时取消后 finally 必须释放执行权，否则恢复扫描永久跳过"
    )
    errors = [str(c.args[0]) for c in log.error.call_args_list]
    assert any("超时" in m and "hang" in m for m in errors), (
        f"超时应记 error（含 run_id），实际 {errors}"
    )


def test_per_run_timeout_leaves_run_processing_then_recovery_continues():
    """超时 run 不得被误标失败/计数；下一轮恢复扫描能续跑（闭环）。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.list_pending.return_value = [
        {"run_id": "hang", "task_type": "match", "sync_record_id": 1}
    ]

    async def _hang(run_id, **kwargs):
        await asyncio.Event().wait()

    def _common_patchers():
        """每轮新建 patcher（_patch 对象不可跨 with 复用）。"""
        return [
            patch("app.services.llm_match_scheduler.config_manager", cm),
            patch(
                "app.services.llm_match_scheduler.get_database_manager",
                return_value=_make_dbm(repo),
            ),
            patch(
                "app.services.llm_match_scheduler.compute_match_run_timeout",
                return_value=0.05,
            ),
            patch.object(
                sched, "_get_sync_record", return_value={"id": 1, "title": "t"}
            ),
            patch(
                "app.services.matching.llm_assist.build_bgm", return_value=MagicMock()
            ),
        ]

    def _enter(*extra):
        stack = contextlib.ExitStack()
        for p in _common_patchers():
            stack.enter_context(p)
        for p in extra:
            stack.enter_context(p)
        return stack

    # 第一轮：pending hang → 超时取消
    with _enter(
        patch("app.services.agent.run.registry.ScenarioRuntime.run", side_effect=_hang)
    ):
        asyncio.run(sched._run_sync_job())

    repo.mark_failed.assert_not_called()
    repo.increment_attempts.assert_not_called()
    assert "hang" not in llm_match_scheduler._active_run_ids

    # 第二轮：run 仍为 processing → 恢复扫描捞起 → continue_run 续跑
    repo.list_pending.return_value = []
    repo.list_stale_processing.return_value = [
        {"run_id": "hang", "task_type": "match", "sync_record_id": 1}
    ]
    cont = AsyncMock()
    with _enter(
        patch("app.services.agent.run.registry.ScenarioRuntime.continue_run", new=cont)
    ):
        asyncio.run(sched._run_sync_job())

    cont.assert_awaited_once()


def test_tick_timeout_cancels_hanging_round_and_returns():
    """tick 兜底：调度轮整体 hang → wait_for 取消、测试不悬挂、finally 执行。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    log = MagicMock()
    state = {"cancelled": False}

    async def _hang_round(match_cfg, run_timeout):
        try:
            await asyncio.Event().wait()
        finally:
            state["cancelled"] = True  # 证明取消传播到内部（未被吞）

    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.compute_match_run_timeout",
            return_value=0.05,
        ),
        patch("app.services.llm_match_scheduler.TICK_TIMEOUT_BUFFER", 0.1),
        patch("app.services.llm_match_scheduler.logger", log),
        patch.object(sched, "_run_round", new=_hang_round),
    ):
        asyncio.run(sched._run_sync_job())

    assert state["cancelled"] is True, (
        "tick 取消应传播到 _run_round（CancelledError 不得被吞）"
    )
    errors = [str(c.args[0]) for c in log.error.call_args_list]
    assert any("整体超时" in m for m in errors), f"tick 超时应记 error，实际 {errors}"


def test_recovery_missing_sync_record_notifies_agent_run_failed():
    """恢复扫描：sync_record 缺失 → mark_failed 并发送 agent_run_failed。"""
    sched = LlmMatchScheduler()
    cm = _make_config()
    repo = _make_repo()
    repo.list_stale_processing.return_value = [
        {"run_id": "r1", "task_type": "match", "sync_record_id": 42}
    ]
    with (
        patch("app.services.llm_match_scheduler.config_manager", cm),
        patch(
            "app.services.llm_match_scheduler.get_database_manager",
            return_value=_make_dbm(repo),
        ),
        patch.object(sched, "_get_sync_record", return_value=None),
        patch("app.services.base.notifier_helpers.notification_service") as notify,
    ):
        import asyncio

        asyncio.run(sched._run_sync_job())

    repo.mark_failed.assert_called_once()
    notify.notify.assert_called_once()
    assert notify.notify.call_args.args[0] == "agent_run_failed"
    assert notify.notify.call_args.kwargs.get("run_id") == "r1"
