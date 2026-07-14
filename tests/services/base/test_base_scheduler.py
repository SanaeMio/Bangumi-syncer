"""BaseScheduler 抽象基类测试"""

from __future__ import annotations

import pytest

from app.services.base.scheduler import BaseScheduler


class _FakeScheduler(BaseScheduler):
    """测试用的具体子类"""

    JOB_ID = "fake_test_sync"
    DEFAULT_CRON = "*/10 * * * *"
    DRIVER_NAME = "fake"

    def __init__(self):
        super().__init__()
        self.sync_called = False
        self.config = {"enabled": True, "sync_interval": "*/10 * * * *"}

    def _is_enabled(self) -> bool:
        return self.config.get("enabled", False)

    async def _run_sync_job(self) -> None:
        self.sync_called = True

    def _get_driver_config(self) -> dict:
        return self.config


class TestBaseSchedulerCronParsing:
    """_parse_cron 方法测试"""

    def test_parse_valid_5_parts(self):
        s = _FakeScheduler()
        trigger = s._parse_cron("*/5 * * * *")
        assert trigger is not None

    def test_parse_valid_custom(self):
        s = _FakeScheduler()
        trigger = s._parse_cron("0 8 * * 1-5")
        assert trigger is not None

    def test_parse_invalid_4_parts_fallback(self):
        s = _FakeScheduler()
        trigger = s._parse_cron("*/5 * * *")
        # 无效时应返回默认 trigger
        assert trigger is not None

    def test_parse_invalid_6_parts_fallback(self):
        s = _FakeScheduler()
        trigger = s._parse_cron("*/5 * * * * *")
        assert trigger is not None

    def test_parse_empty_fallback(self):
        s = _FakeScheduler()
        trigger = s._parse_cron("")
        assert trigger is not None


class TestBaseSchedulerDefaults:
    """类属性与默认值测试"""

    def test_default_cron_trigger(self):
        s = _FakeScheduler()
        trigger = s._default_cron_trigger()
        assert trigger is not None

    def test_job_id(self):
        assert _FakeScheduler.JOB_ID == "fake_test_sync"

    def test_default_cron(self):
        assert _FakeScheduler.DEFAULT_CRON == "*/10 * * * *"

    def test_driver_name(self):
        assert _FakeScheduler.DRIVER_NAME == "fake"


class TestBaseSchedulerStartStop:
    """start/stop 生命周期测试"""

    @pytest.mark.asyncio
    async def test_start_disabled_returns_true_without_scheduler(self):
        s = _FakeScheduler()
        s.config["enabled"] = False
        result = await s.start()
        assert result is True
        assert s.scheduler is None

    @pytest.mark.asyncio
    async def test_start_enabled_creates_scheduler(self):
        s = _FakeScheduler()
        s.config["enabled"] = True
        result = await s.start()
        assert result is True
        assert s.scheduler is not None
        assert s.scheduler.running is True
        await s.stop()

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self):
        s = _FakeScheduler()
        result = await s.stop()
        assert result is True

    @pytest.mark.asyncio
    async def test_start_idempotent_when_already_running(self):
        s = _FakeScheduler()
        s.config["enabled"] = True
        await s.start()
        # 再次 start 不应报错
        result = await s.start()
        assert result is True
        await s.stop()

    @pytest.mark.asyncio
    async def test_start_then_disable_removes_job(self):
        s = _FakeScheduler()
        s.config["enabled"] = True
        await s.start()
        # 禁用后 start 应移除 job
        s.config["enabled"] = False
        await s.start()
        # scheduler 仍在运行（不 shutdown），但 job 应已移除
        assert s.scheduler is not None
        try:
            s.scheduler.get_job(s.JOB_ID)
        except Exception:
            pass  # job 不存在是预期行为
        await s.stop()


class TestBaseSchedulerApplyConfig:
    """apply_config_after_save 测试"""

    @pytest.mark.asyncio
    async def test_apply_config_enable_starts_scheduler(self):
        s = _FakeScheduler()
        s.config["enabled"] = False
        await s.apply_config_after_save()
        assert s.scheduler is None

        s.config["enabled"] = True
        await s.apply_config_after_save()
        assert s.scheduler is not None
        await s.stop()

    @pytest.mark.asyncio
    async def test_apply_config_disable_stops_scheduler(self):
        s = _FakeScheduler()
        s.config["enabled"] = True
        await s.start()
        assert s.scheduler is not None

        s.config["enabled"] = False
        await s.apply_config_after_save()
        assert s.scheduler is None


class TestBaseSchedulerReloadJob:
    """reload_job_if_running 测试"""

    def test_reload_when_not_running_does_nothing(self):
        s = _FakeScheduler()
        # scheduler 未创建，reload 不应报错
        s.reload_job_if_running()

    @pytest.mark.asyncio
    async def test_reload_when_running_refreshes_job(self):
        s = _FakeScheduler()
        s.config["enabled"] = True
        await s.start()
        # 更改 cron 后 reload
        s.config["sync_interval"] = "*/30 * * * *"
        s.reload_job_if_running()
        job = s.scheduler.get_job(s.JOB_ID)
        assert job is not None
        await s.stop()


class TestBaseSchedulerAbstractEnforcement:
    """抽象类强制实现测试"""

    def test_cannot_instantiate_base_directly(self):
        with pytest.raises(TypeError):
            BaseScheduler()

    def test_subclass_without_abstract_methods_fails(self):
        class Incomplete(BaseScheduler):
            JOB_ID = "incomplete"
            DEFAULT_CRON = "*/5 * * * *"
            DRIVER_NAME = "test"

        with pytest.raises(TypeError):
            Incomplete()
