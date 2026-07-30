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


class TestGetStatusList:
    """get_status_list() — 前端状态卡数据源"""

    def test_empty_registry_returns_empty_list(self):
        reg = SchedulerRegistry()
        assert reg.get_status_list() == []

    def test_spec_with_no_scheduler_returns_empty_jobs(self):
        """spec 调度器未启动（scheduler=None）时 jobs 为空列表"""
        reg = SchedulerRegistry()
        runner = _make_runner()
        runner.scheduler = None
        reg.register_spec(JobSpec(scheduler_id="feiniu", runner=runner))
        result = reg.get_status_list()
        assert len(result) == 1
        assert result[0]["scheduler_id"] == "feiniu"
        assert result[0]["display_name"] == "飞牛影视"
        assert result[0]["jobs"] == []

    def test_spec_with_job_includes_job_info(self):
        """spec 调度器有 job 时返回 trigger / next_run_time"""
        reg = SchedulerRegistry()
        runner = _make_runner(job_id="feiniu_sync")
        # 模拟 APScheduler job
        mock_job = MagicMock()
        mock_job.id = "feiniu_sync"
        mock_job.name = "飞牛同步"
        mock_job.next_run_time = MagicMock()
        mock_job.next_run_time.timestamp.return_value = 1700000000.0
        mock_job.trigger = "*/15 * * * *"
        mock_sched = MagicMock()
        mock_sched.get_job.return_value = mock_job
        runner.scheduler = mock_sched
        reg.register_spec(JobSpec(scheduler_id="feiniu", runner=runner))

        result = reg.get_status_list()
        assert len(result) == 1
        assert result[0]["display_name"] == "飞牛影视"
        assert len(result[0]["jobs"]) == 1
        job = result[0]["jobs"][0]
        assert job["job_id"] == "feiniu_sync"
        assert job["name"] == "飞牛同步"
        assert job["next_run_time"] == 1700000000.0
        assert job["trigger"] == "*/15 * * * *"

    def test_spec_job_none_next_run_time(self):
        """job.next_run_time 为 None 时 next_run_time 字段为 None"""
        reg = SchedulerRegistry()
        runner = _make_runner(job_id="replay_job")
        mock_job = MagicMock()
        mock_job.id = "replay_job"
        mock_job.name = "补发"
        mock_job.next_run_time = None
        mock_job.trigger = "*/10 * * * *"
        mock_sched = MagicMock()
        mock_sched.get_job.return_value = mock_job
        runner.scheduler = mock_sched
        reg.register_spec(JobSpec(scheduler_id="bangumi_replay", runner=runner))

        result = reg.get_status_list()
        assert result[0]["jobs"][0]["next_run_time"] is None

    def test_instance_jobs_merged(self):
        """instance 调度器的 jobs 被合并到 jobs 列表"""
        reg = SchedulerRegistry()
        inst = _make_instance(
            status={
                "trakt_user_1": {
                    "name": "Trakt 同步",
                    "next_run_time": 100.0,
                    "trigger": "0 * * * *",
                },
                "trakt_user_2": {
                    "name": "Trakt 同步",
                    "next_run_time": 200.0,
                    "trigger": "0 * * * *",
                },
            }
        )
        reg.register_instance("trakt", inst)

        result = reg.get_status_list()
        assert len(result) == 1
        assert result[0]["scheduler_id"] == "trakt"
        assert result[0]["display_name"] == "Trakt 同步"
        assert len(result[0]["jobs"]) == 2

    def test_instance_exception_returns_empty_jobs(self):
        """instance 调度器 get_all_jobs_status 抛异常时 jobs 为空"""
        reg = SchedulerRegistry()
        inst = _make_instance()
        inst.get_all_jobs_status = MagicMock(side_effect=RuntimeError("boom"))
        reg.register_instance("trakt", inst)

        result = reg.get_status_list()
        assert len(result) == 1
        assert result[0]["jobs"] == []

    def test_display_name_from_section_meta(self):
        """display_name 从 SectionMeta 取（bangumi_archive → Bangumi Archive）"""
        reg = SchedulerRegistry()
        runner = _make_runner()
        runner.scheduler = None
        reg.register_spec(JobSpec(scheduler_id="bangumi_archive", runner=runner))
        result = reg.get_status_list()
        assert result[0]["display_name"] == "Bangumi Archive"

    def test_display_name_fallback_to_scheduler_id(self):
        """无 SectionMeta 关联的 scheduler_id 回退为 id 本身"""
        reg = SchedulerRegistry()
        inst = _make_instance(status={})
        reg.register_instance("unknown_sid", inst)
        result = reg.get_status_list()
        assert result[0]["display_name"] == "unknown_sid"

    def test_mixed_spec_and_instance(self):
        """spec + instance 混合注册"""
        reg = SchedulerRegistry()
        runner = _make_runner()
        runner.scheduler = None
        reg.register_spec(JobSpec(scheduler_id="feiniu", runner=runner))
        inst = _make_instance(status={"trakt_job": {"next_run_time": 1.0}})
        reg.register_instance("trakt", inst)

        result = reg.get_status_list()
        assert len(result) == 2
        sids = {r["scheduler_id"] for r in result}
        assert sids == {"feiniu", "trakt"}

    def test_instance_job_dict_keys_passed_through(self):
        """instance 返回的 job dict 额外字段（如 pending）被透传"""
        reg = SchedulerRegistry()
        inst = _make_instance(
            status={"job1": {"next_run_time": 1.0, "pending": True, "custom": "x"}}
        )
        reg.register_instance("trakt", inst)
        result = reg.get_status_list()
        job = result[0]["jobs"][0]
        assert job["pending"] is True
        assert job["custom"] == "x"
        assert job["job_id"] == "job1"
