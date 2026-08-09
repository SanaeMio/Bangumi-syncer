"""轻量可注入单例（app.core.container.Injectable）及服务 get/set/reset 钩子测试。

约定：每个用例在 finally 中复位单例，避免污染同进程内的其它用例。
"""

import pytest

from app.core.config import (
    config_manager,
    get_config_manager,
    reset_config_manager,
    set_config_manager,
)
from app.core.container import Injectable
from app.core.database import (
    DatabaseManager,
    get_database_manager,
    reset_database_manager,
    set_database_manager,
)
from app.services.mapping_service import (
    MappingService,
    get_mapping_service,
    mapping_service,
    reset_mapping_service,
    set_mapping_service,
)
from app.services.notification_service import (
    NotificationService,
    get_notification_service,
    notification_service,
    reset_notification_service,
    set_notification_service,
)
from app.services.sync_service import (
    SyncService,
    get_sync_service,
    reset_sync_service,
    set_sync_service,
)

# ── Injectable 基础行为 ─────────────────────────────────────────────────


def test_injectable_lazy_creation():
    calls = []

    def factory():
        calls.append(1)
        return object()

    holder = Injectable(factory)
    assert calls == []
    first = holder.get()
    assert calls == [1]
    assert holder.get() is first
    assert calls == [1]  # 第二次 get 不再创建


def test_injectable_set_and_reset():
    holder = Injectable(lambda: object())
    original = holder.get()
    fake = object()
    holder.set(fake)
    assert holder.get() is fake
    holder.reset()
    assert holder.get() is not fake
    assert holder.get() is not original


# ── mapping_service ─────────────────────────────────────────────────────


def test_mapping_service_getters_and_inject():
    try:
        assert get_mapping_service() is mapping_service
        fake = object()
        set_mapping_service(fake)
        assert get_mapping_service() is fake
        reset_mapping_service()
        assert get_mapping_service() is not fake
        assert isinstance(get_mapping_service(), MappingService)
    finally:
        reset_mapping_service()


def test_mapping_service_reset_returns_fresh_instance():
    try:
        first = get_mapping_service()
        reset_mapping_service()
        second = get_mapping_service()
        assert second is not first
        assert isinstance(second, MappingService)
    finally:
        reset_mapping_service()


# ── notification_service ────────────────────────────────────────────────


def test_notification_service_getters_and_inject():
    try:
        assert get_notification_service() is notification_service
        fake = object()
        set_notification_service(fake)
        assert get_notification_service() is fake
        reset_notification_service()
        assert get_notification_service() is not fake
        assert isinstance(get_notification_service(), NotificationService)
    finally:
        reset_notification_service()


# ── sync_service ────────────────────────────────────────────────────────


def test_sync_service_getters_and_inject():
    try:
        assert get_sync_service().__class__ is SyncService
        fake = object()
        set_sync_service(fake)
        assert get_sync_service() is fake
        reset_sync_service()
        assert get_sync_service() is not fake
        assert get_sync_service().__class__ is SyncService
    finally:
        reset_sync_service()


# ── database_manager ────────────────────────────────────────────────────


def test_database_manager_getters_and_inject():
    try:
        assert get_database_manager().__class__ is DatabaseManager
        fake = object()
        set_database_manager(fake)
        assert get_database_manager() is fake
        reset_database_manager()
        assert get_database_manager() is not fake
        assert get_database_manager().__class__ is DatabaseManager
    finally:
        reset_database_manager()


# ── config_manager ──────────────────────────────────────────────────────


def test_config_manager_getters_and_inject():
    try:
        assert get_config_manager() is config_manager
        fake = object()
        set_config_manager(fake)
        assert get_config_manager() is fake
        reset_config_manager()
        assert get_config_manager() is config_manager
    finally:
        reset_config_manager()


# ── 未匹配属性仍抛 AttributeError ───────────────────────────────────────


def test_module_getattr_unknown_attribute():
    import importlib

    mod = importlib.import_module("app.services.mapping_service")

    with pytest.raises(AttributeError):
        _ = mod.not_existing_attribute
