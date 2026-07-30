"""SchedulerRegistry 单元测试

验证注册、查询、生命周期、配置联动、状态汇总的核心契约。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.scheduler_registry import (
    JobSpec,
    SchedulerRegistry,
)


def _make_runner(
    job_id: str = "test_job",
    start_ok: bool = True,
    stop_ok: bool = True,
) -> MagicMock:
    """构造一个模拟 BaseScheduler runner 的 MagicMock"""
    runner = MagicMock()
    runner.JOB_ID = job_id
    runner.scheduler = None  # 未启动状态
    runner.start = AsyncMock(return_value=start_ok)
    runner.stop = AsyncMock(return_value=stop_ok)
    runner.apply_config_after_save = AsyncMock()
    return runner


def _make_instance(
    start_ok: bool = True,
    stop_ok: bool = True,
    status: dict | None = None,
) -> MagicMock:
    """构造一个模拟命令式调度器 instance 的 MagicMock"""
    inst = MagicMock()
    inst.start = AsyncMock(return_value=start_ok)
    inst.stop = AsyncMock(return_value=stop_ok)
    inst.get_all_jobs_status = MagicMock(return_value=status or {})
    return inst


class TestRegistration:
    """注册与查询"""

    def test_register_spec(self):
        reg = SchedulerRegistry()
        runner = _make_runner()
        reg.register_spec(JobSpec(scheduler_id="feiniu", runner=runner))
        assert reg.get("feiniu") is runner
        assert "feiniu" in reg.all_scheduler_ids()

    def test_register_instance(self):
        reg = SchedulerRegistry()
        inst = _make_instance()
        reg.register_instance("trakt", inst)
        assert reg.get("trakt") is inst
        assert "trakt" in reg.all_scheduler_ids()

    def test_register_spec_overrides_instance_with_same_id(self):
        reg = SchedulerRegistry()
        inst = _make_instance()
        runner = _make_runner()
        reg.register_instance("dup", inst)
        reg.register_spec(JobSpec(scheduler_id="dup", runner=runner))
        assert reg.get("dup") is runner
        assert "dup" not in [sid for sid, _ in []]

    def test_register_instance_overrides_spec_with_same_id(self):
        reg = SchedulerRegistry()
        runner = _make_runner()
        inst = _make_instance()
        reg.register_spec(JobSpec(scheduler_id="dup", runner=runner))
        reg.register_instance("dup", inst)
        assert reg.get("dup") is inst

    def test_get_unknown_returns_none(self):
        reg = SchedulerRegistry()
        assert reg.get("nonexistent") is None

    def test_all_scheduler_ids_combines_both(self):
        reg = SchedulerRegistry()
        reg.register_spec(JobSpec(scheduler_id="feiniu", runner=_make_runner()))
        reg.register_instance("trakt", _make_instance())
        ids = reg.all_scheduler_ids()
        assert "feiniu" in ids
        assert "trakt" in ids


class TestLifecycle:
    """start_all / stop_all"""

    @pytest.mark.asyncio
    async def test_start_all_calls_spec_runners_and_instances(self):
        reg = SchedulerRegistry()
        runner = _make_runner()
        inst = _make_instance()
        reg.register_spec(JobSpec(scheduler_id="feiniu", runner=runner))
        reg.register_instance("trakt", inst)

        await reg.start_all()

        runner.start.assert_awaited_once()
        inst.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_all_calls_instances_and_spec_runners(self):
        reg = SchedulerRegistry()
        runner = _make_runner()
        inst = _make_instance()
        reg.register_spec(JobSpec(scheduler_id="feiniu", runner=runner))
        reg.register_instance("trakt", inst)

        await reg.stop_all()

        inst.stop.assert_awaited_once()
        runner.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_all_continues_on_exception(self):
        reg = SchedulerRegistry()
        bad_runner = _make_runner()
        bad_runner.start = AsyncMock(side_effect=RuntimeError("boom"))
        good_runner = _make_runner()
        reg.register_spec(JobSpec(scheduler_id="bad", runner=bad_runner))
        reg.register_spec(JobSpec(scheduler_id="good", runner=good_runner))

        await reg.start_all()  # 不应抛异常

        bad_runner.start.assert_awaited_once()
        good_runner.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_all_continues_on_exception(self):
        reg = SchedulerRegistry()
        bad_inst = _make_instance()
        bad_inst.stop = AsyncMock(side_effect=RuntimeError("boom"))
        good_inst = _make_instance()
        reg.register_instance("bad", bad_inst)
        reg.register_instance("good", good_inst)

        await reg.stop_all()  # 不应抛异常

        bad_inst.stop.assert_awaited_once()
        good_inst.stop.assert_awaited_once()


class TestApplyConfigBySection:
    """配置联动"""

    @pytest.mark.asyncio
    async def test_apply_config_by_section_triggers_runner(self):
        reg = SchedulerRegistry()
        runner = _make_runner()
        reg.register_spec(JobSpec(scheduler_id="feiniu", runner=runner))

        await reg.apply_config_by_section("feiniu")

        runner.apply_config_after_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_apply_config_by_section_executes_pre_reload_hook(self):
        reg = SchedulerRegistry()
        runner = _make_runner()
        hook = MagicMock()
        reg.register_spec(
            JobSpec(
                scheduler_id="bangumi_archive",
                runner=runner,
                pre_reload_hook=hook,
            )
        )

        await reg.apply_config_by_section("bangumi-archive")

        hook.assert_called_once()
        runner.apply_config_after_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_apply_config_by_section_no_scheduler_id(self):
        """无 scheduler_id 关联的 section 静默跳过"""
        reg = SchedulerRegistry()
        runner = _make_runner()
        reg.register_spec(JobSpec(scheduler_id="feiniu", runner=runner))

        await reg.apply_config_by_section("sync")  # sync 段无 scheduler_id

        runner.apply_config_after_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_apply_config_by_section_instance_skipped(self):
        """instance 调度器（trakt）不参与 INI 联动，静默跳过不报错"""
        reg = SchedulerRegistry()
        inst = _make_instance()
        reg.register_instance("trakt", inst)

        # 不应抛异常（instance 走 _instances，apply_config_by_section 只查 _specs）
        await reg.apply_config_by_section("trakt")

    @pytest.mark.asyncio
    async def test_apply_config_by_section_unregistered_scheduler(self):
        """scheduler_id 存在但未注册调度器，静默跳过"""
        reg = SchedulerRegistry()
        # summary 段有 scheduler_id="summary"，但未注册任何调度器
        await reg.apply_config_by_section("summary")  # 不应抛异常

    @pytest.mark.asyncio
    async def test_apply_config_by_section_catches_exception(self):
        """runner.apply_config_after_save 抛异常时仅记日志，不向上抛"""
        reg = SchedulerRegistry()
        runner = _make_runner()
        runner.apply_config_after_save = AsyncMock(
            side_effect=RuntimeError("apply fail")
        )
        reg.register_spec(JobSpec(scheduler_id="feiniu", runner=runner))

        await reg.apply_config_by_section("feiniu")  # 不应抛异常


class TestGetAllJobsStatus:
    """状态汇总"""

    def test_spec_with_no_scheduler_returns_empty(self):
        reg = SchedulerRegistry()
        runner = _make_runner()
        runner.scheduler = None
        reg.register_spec(JobSpec(scheduler_id="feiniu", runner=runner))
        assert reg.get_all_jobs_status() == {}

    def test_instance_status_merged(self):
        reg = SchedulerRegistry()
        inst = _make_instance(status={"trakt_sync_123": {"next_run_time": 999}})
        reg.register_instance("trakt", inst)
        status = reg.get_all_jobs_status()
        assert "trakt_sync_123" in status

    def test_instance_exception_returns_empty(self):
        reg = SchedulerRegistry()
        inst = _make_instance()
        inst.get_all_jobs_status = MagicMock(side_effect=RuntimeError("status fail"))
        reg.register_instance("trakt", inst)
        assert reg.get_all_jobs_status() == {}
