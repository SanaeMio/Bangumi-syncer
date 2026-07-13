"""BaseSyncResult / BaseWatchRecord 数据模型测试"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

from app.services.base.models import BaseSyncResult, BaseWatchRecord


class TestBaseSyncResult:
    def test_default_values(self):
        r = BaseSyncResult(success=True, message="ok")
        assert r.success is True
        assert r.message == "ok"
        assert r.synced_count == 0
        assert r.skipped_count == 0
        assert r.error_count == 0

    def test_with_all_fields(self):
        r = BaseSyncResult(
            success=False,
            message="partial",
            synced_count=5,
            skipped_count=3,
            error_count=1,
        )
        assert r.success is False
        assert r.synced_count == 5
        assert r.skipped_count == 3
        assert r.error_count == 1

    def test_is_mutable(self):
        r = BaseSyncResult(success=True, message="ok")
        r.synced_count = 10
        assert r.synced_count == 10

    def test_subclass_can_extend(self):
        from dataclasses import dataclass

        @dataclass
        class ExtendedResult(BaseSyncResult):
            discovered_devices: int = 0

        r = ExtendedResult(success=True, message="ok", discovered_devices=3)
        assert r.discovered_devices == 3
        assert r.synced_count == 0  # 继承的字段


class TestBaseWatchRecord:
    def test_basic_fields(self):
        r = BaseWatchRecord(
            title="测试番",
            season=1,
            episode=5,
            release_date="2024-01-01",
            user_name="user1",
        )
        assert r.title == "测试番"
        assert r.season == 1
        assert r.episode == 5
        assert r.release_date == "2024-01-01"
        assert r.user_name == "user1"

    def test_is_frozen(self):
        r = BaseWatchRecord(
            title="x", season=1, episode=1, release_date="", user_name=""
        )
        try:
            r.title = "modified"
            raise AssertionError("应抛出 FrozenInstanceError")
        except FrozenInstanceError:
            pass

    def test_subclass_can_extend(self):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class ExtendedRecord(BaseWatchRecord):
            device_ip: str = ""

        r = ExtendedRecord(
            title="x",
            season=1,
            episode=1,
            release_date="",
            user_name="",
            device_ip="192.168.1.1",
        )
        assert r.device_ip == "192.168.1.1"
