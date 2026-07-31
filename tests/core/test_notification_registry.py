"""NotificationTypeRegistry 单元测试"""

from __future__ import annotations

from app.core.notification_registry import (
    WATCHING_SUMMARY_PREFIX,
    all_types,
    get_type_meta,
    is_item_level_type,
    item_level_types,
    normalize_type,
    resolve_in_app_type,
    type_color,
    type_display_name,
    type_icon,
    ui_visible_types,
)


class TestGetTypeMeta:
    def test_known_type(self):
        meta = get_type_meta("mark_failed")
        assert meta is not None
        assert meta.display_name == "同步失败"
        assert meta.icon == "❌"
        assert meta.color == "#dc3545"

    def test_watching_summary_dynamic(self):
        meta = get_type_meta("watching_summary_dad")
        assert meta is not None
        assert meta.display_name == "追番总结"
        assert meta.icon == "📊"

    def test_unknown_type(self):
        assert get_type_meta("nonexistent") is None


class TestResolveInAppType:
    def test_mark_failed_maps_to_sync_failed(self):
        assert resolve_in_app_type("mark_failed") == "sync_failed"

    def test_anime_not_found_maps_to_sync_failed(self):
        assert resolve_in_app_type("anime_not_found") == "sync_failed"

    def test_episode_not_found_maps_to_sync_failed(self):
        assert resolve_in_app_type("episode_not_found") == "sync_failed"

    def test_mark_success_no_in_app(self):
        assert resolve_in_app_type("mark_success") is None

    def test_watching_summary_no_in_app(self):
        assert resolve_in_app_type("watching_summary_dad") is None


class TestNormalizeType:
    def test_watching_summary_normalized(self):
        assert normalize_type("watching_summary_dad") == WATCHING_SUMMARY_PREFIX
        assert normalize_type("watching_summary_foo") == WATCHING_SUMMARY_PREFIX

    def test_plain_type_unchanged(self):
        assert normalize_type("mark_failed") == "mark_failed"


class TestItemLevel:
    def test_item_level_types(self):
        types = item_level_types()
        assert "mark_failed" in types
        assert "mark_success" in types
        assert "request_received" in types
        assert "config_error" not in types

    def test_is_item_level_true(self):
        assert is_item_level_type("mark_failed") is True

    def test_is_item_level_false(self):
        assert is_item_level_type("config_error") is False

    def test_is_item_level_unknown(self):
        assert is_item_level_type("nonexistent") is False


class TestUiVisibleTypes:
    def test_excludes_internal_types(self):
        visible = ui_visible_types()
        visible_ids = {t.id for t in visible}
        assert "sync_failed" not in visible_ids
        assert "summary_llm_failed" not in visible_ids
        assert "summary_job_failed" not in visible_ids

    def test_includes_user_facing_types(self):
        visible = ui_visible_types()
        visible_ids = {t.id for t in visible}
        assert "mark_failed" in visible_ids
        assert "mark_success" in visible_ids
        assert "request_received" in visible_ids


class TestTypeHelpers:
    def test_display_name(self):
        assert type_display_name("mark_failed") == "同步失败"
        assert type_display_name("watching_summary_dad") == "追番总结"
        assert type_display_name("nonexistent") == "nonexistent"

    def test_icon(self):
        assert type_icon("mark_failed") == "❌"
        assert type_icon("nonexistent") == "📢"

    def test_color(self):
        assert type_color("mark_failed") == "#dc3545"
        assert type_color("nonexistent") == "#6c757d"


class TestAllTypes:
    def test_includes_watching_summary(self):
        types = all_types()
        ids = {t.id for t in types}
        assert WATCHING_SUMMARY_PREFIX in ids
        assert "mark_failed" in ids


class TestAiringToday:
    """airing_today 通知类型登记"""

    def test_registered(self):
        meta = get_type_meta("airing_today")
        assert meta is not None
        assert meta.display_name == "今日放送提醒"
        assert meta.category == "scheduler"
        assert meta.is_item_level is False
        assert meta.visible_in_ui is True

    def test_no_in_app_mapping(self):
        """airing_today 不映射站内信（仅外部渠道）"""
        assert resolve_in_app_type("airing_today") is None

    def test_not_item_level(self):
        assert is_item_level_type("airing_today") is False

    def test_visible_in_ui(self):
        visible_ids = {t.id for t in ui_visible_types()}
        assert "airing_today" in visible_ids
