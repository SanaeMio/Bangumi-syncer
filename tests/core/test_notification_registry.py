"""NotificationTypeRegistry 单元测试"""

from __future__ import annotations

from unittest.mock import MagicMock

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
from app.services.notification_service import NotificationService
from app.utils.notifier.channels_impl import InAppChannel


class _FakeDB:
    """捕获 insert_notification 调用的假数据库"""

    def __init__(self):
        self.calls = []

    def insert_notification(self, notif_type, title, body, ref_id=None):
        self.calls.append((notif_type, title, body, ref_id))


def _make_service() -> tuple[NotificationService, _FakeDB]:
    svc = NotificationService()
    svc._in_app_channel = InAppChannel(
        channel_id="in_app", config={"in_app_notification": True}
    )
    fake_db = _FakeDB()
    svc._get_db_manager = MagicMock(return_value=fake_db)  # type: ignore[assignment]
    return svc, fake_db


def _pending_candidate_data(**overrides) -> dict:
    data = {
        "timestamp": "2026-07-16 12:00:00",
        "user_name": "tester",
        "title": "测试番剧",
        "ori_title": "test anime",
        "season": 2,
        "episode": 5,
        "source": "plex",
        "media_type": "episode",
    }
    data.update(overrides)
    return data


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

    def test_in_app_mapping(self):
        """airing_today 映射到站内信，标题模板带放送日期与集数"""
        assert resolve_in_app_type("airing_today") == "airing_today"
        meta = get_type_meta("airing_today")
        assert meta is not None
        assert meta.in_app_title_template == "今日放送 {total} 集（{airdate}）"

    def test_not_item_level(self):
        assert is_item_level_type("airing_today") is False

    def test_visible_in_ui(self):
        visible_ids = {t.id for t in ui_visible_types()}
        assert "airing_today" in visible_ids


class TestPendingCandidateInAppMapping:
    """pending_candidate 登记站内信映射"""

    def test_pending_candidate_has_match_pending_in_app_type(self):
        meta = get_type_meta("pending_candidate")
        assert meta is not None
        assert meta.in_app_type == "match_pending"
        assert meta.in_app_title_template == "匹配待确认：{title} {ep_label}"

    def test_resolve_pending_candidate_to_match_pending(self):
        assert resolve_in_app_type("pending_candidate") == "match_pending"

    def test_match_pending_is_registered_internal_type(self):
        meta = get_type_meta("match_pending")
        assert meta is not None
        assert meta.visible_in_ui is False
        assert meta.category == "match_quality"

    def test_match_pending_not_in_ui_visible_list(self):
        visible_ids = {t.id for t in ui_visible_types()}
        assert "match_pending" not in visible_ids


class TestPendingCandidateInAppBody:
    """站内信正文：AI 标识前缀 + 转义"""

    def test_is_llm_suggestion_true_adds_prefix_and_reason(self):
        svc, fake_db = _make_service()
        data = _pending_candidate_data(
            is_llm_suggestion=True,
            llm_reason="首选候选置信度较低，建议人工确认",
        )
        svc._write_in_app_notification(
            "pending_candidate", data, None, None, None, None
        )
        assert fake_db.calls, "应写入一条站内信"
        notif_type, title, body, ref_id = fake_db.calls[0]
        assert notif_type == "match_pending"
        assert body.startswith("[AI 建议] ")
        assert "首选候选置信度较低，建议人工确认" in body

    def test_is_llm_suggestion_false_no_prefix(self):
        svc, fake_db = _make_service()
        data = _pending_candidate_data(
            is_llm_suggestion=False,
            llm_reason="不应出现前缀",
        )
        svc._write_in_app_notification(
            "pending_candidate", data, None, None, None, None
        )
        assert fake_db.calls
        notif_type, title, body, ref_id = fake_db.calls[0]
        assert not body.startswith("[AI 建议] ")
        # 非 LLM 建议时不应将 llm_reason 透传进正文
        assert "不应出现前缀" not in body

    def test_reason_html_escaped_in_body(self):
        svc, fake_db = _make_service()
        evil = "<script>alert(1)</script> {{user.email}}"
        data = _pending_candidate_data(is_llm_suggestion=True, llm_reason=evil)
        svc._write_in_app_notification(
            "pending_candidate", data, None, None, None, None
        )
        notif_type, title, body, ref_id = fake_db.calls[0]
        # 危险标签被转义，不应以原始 <script> 出现
        assert "<script>" not in body
        assert "&lt;script&gt;" in body
        # 模板语法 {{...}} 作为纯文本原样保留（不被执行）
        assert "{{user.email}}" in body

    def test_title_html_escaped_in_title(self):
        svc, fake_db = _make_service()
        evil_title = "<img src=x onerror=alert(1)> {{user.email}}"
        data = _pending_candidate_data(title=evil_title, is_llm_suggestion=False)
        svc._write_in_app_notification(
            "pending_candidate", data, None, None, None, None
        )
        notif_type, title, body, ref_id = fake_db.calls[0]
        assert "<img" not in title
        assert "&lt;img" in title
        assert "{{user.email}}" in title
        # 标题模板前缀命中
        assert title.startswith("匹配待确认：")


class TestPayloadFields:
    """类型专属 payload 字段声明（供 webhook 默认模板展开）"""

    def test_watching_summary_carries_summary_text(self):
        """追番总结的正文是核心内容，必须随类型声明，否则通用模板发不出内容"""
        meta = get_type_meta("watching_summary_每日总结")
        assert meta is not None
        fields = meta.default_payload_fields()
        assert fields["summary"] == "summary_text"
        assert fields["job_name"] == "job_name"

    def test_mark_failed_carries_error_context(self):
        meta = get_type_meta("mark_failed")
        assert meta is not None
        fields = meta.default_payload_fields()
        assert fields["error_type"] == "error_type"
        assert fields["additional_info"] == "additional_info"

    def test_default_payload_fields_returns_dict(self):
        meta = get_type_meta("request_received")
        assert meta is not None
        assert meta.default_payload_fields() == {}

    def test_payload_fields_are_name_key_pairs(self):
        """所有声明必须是 (字段名, data 键名) 二元组，避免渲染时取错来源"""
        for meta in all_types():
            for pair in meta.payload_fields:
                assert len(pair) == 2, f"{meta.id} 的 payload_fields 项格式非法: {pair}"
                field_name, data_key = pair
                assert field_name and isinstance(field_name, str)
                assert data_key and isinstance(data_key, str)

    def test_unknown_type_has_no_fields(self):
        assert get_type_meta("no_such_type") is None
