"""NotificationTemplateManager 的类型专属字段渲染测试

覆盖 Issue #245 暴露的问题：使用通用默认模板时，追番总结等类型的
专属内容（总结正文）会被丢弃。
"""

import pytest

from app.utils.notifier.template_manager import NotificationTemplateManager

# ─────────────────────────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────────────────────────


def _summary_data(**overrides):
    data = {
        "notification_type": "watching_summary_每日总结",
        "type_display_name": "追番总结",
        "type_icon": "📊",
        "timestamp": "2026-01-01 00:00:00",
        "user_name": "tester",
        "title": "unknown",
        "season": 0,
        "episode": 0,
        "source": "summary",
        "job_name": "每日总结",
        "summary_text": "本周看了 3 部番，共 12 集。",
        "date_range": "2026-01-01 ~ 2026-01-07",
        "record_count": 12,
        "model": "deepseek-chat",
        "tokens_used": 3456,
    }
    data.update(overrides)
    return data


def _manager(tmp_path):
    """使用仓库内置模板目录的管理器"""
    return NotificationTemplateManager(default_dir=None, custom_dir=tmp_path)


# ─────────────────────────────────────────────────────────────────────────
# type_fields
# ─────────────────────────────────────────────────────────────────────────


class TestTypeFields:
    def test_watching_summary_includes_summary(self):
        fields = NotificationTemplateManager.type_fields(_summary_data())
        assert fields["summary"] == "本周看了 3 部番，共 12 集。"
        assert fields["job_name"] == "每日总结"
        assert fields["record_count"] == 12

    def test_missing_values_are_dropped(self):
        """缺失或空的字段不应出现，避免模板输出空键"""
        data = _summary_data()
        data.pop("model")
        data["date_range"] = ""
        fields = NotificationTemplateManager.type_fields(data)
        assert "model" not in fields
        assert "date_range" not in fields

    def test_none_values_are_dropped(self):
        """None 与空串同等对待：整个键不存在，而非输出 null"""
        data = _summary_data()
        data["model"] = None
        fields = NotificationTemplateManager.type_fields(data)
        assert "model" not in fields

    def test_zero_is_kept_not_treated_as_empty(self):
        """0 是有效值（如 record_count=0），不得被当成空而丢弃"""
        data = _summary_data()
        data["record_count"] = 0
        fields = NotificationTemplateManager.type_fields(data)
        assert fields["record_count"] == 0

    def test_false_is_kept(self):
        """False 也是有效值，不能被当作空"""
        data = _summary_data()
        data["is_timeout"] = False
        data["notification_type"] = "scheduler_job_failed"
        fields = NotificationTemplateManager.type_fields(data)
        assert fields["is_timeout"] is False

    def test_contract_is_absence_not_default(self):
        """契约：缺值是「键不存在」，不是「键存在且为 0/空串」。

        与旧 Notifier 的实现相反（旧实现会填 0/""），这是有意为之：
        避免 summary="" 覆盖，也让消费方能区分「没有这个字段」和「值为 0」。
        """
        data = _summary_data()
        for k in ("model", "date_range", "record_count", "tokens_used"):
            data.pop(k, None)
        fields = NotificationTemplateManager.type_fields(data)
        for k in ("model", "date_range", "record_count", "tokens_used"):
            assert k not in fields, f"{k} 应为「不存在」，而不是默认值"

    def test_unknown_type_returns_empty(self):
        assert (
            NotificationTemplateManager.type_fields(
                {"notification_type": "no_such_type"}
            )
            == {}
        )

    def test_missing_notification_type_returns_empty(self):
        assert NotificationTemplateManager.type_fields({}) == {}

    def test_type_without_fields_returns_empty(self):
        """request_received 未声明专属字段"""
        assert (
            NotificationTemplateManager.type_fields(
                {"notification_type": "request_received", "title": "x"}
            )
            == {}
        )


# ─────────────────────────────────────────────────────────────────────────
# render_webhook_payload
# ─────────────────────────────────────────────────────────────────────────


class TestRenderWebhookPayload:
    def test_default_template_gains_summary_field(self, tmp_path):
        """默认模板 + 追番总结 → payload 必须携带 summary"""
        payload = _manager(tmp_path).render_webhook_payload(_summary_data())
        assert payload["summary"] == "本周看了 3 部番，共 12 集。"
        assert payload["job_name"] == "每日总结"

    def test_default_template_drops_placeholder_sentinel(self, tmp_path):
        """模板中的 extra 哨兵不应以字面量泄漏到最终 payload"""
        payload = _manager(tmp_path).render_webhook_payload(_summary_data())
        assert "__type_fields__" not in payload.values()

    def test_sentinel_dropped_for_types_without_fields(self, tmp_path):
        """无专属字段的类型同样要消费掉哨兵。

        回归：哨兵删除原先写在 `if not fields: return` 之后，导致 31 个类型里
        有 7 个（mark_failed / airing_today / anime_not_found / request_received /
        config_error / episode_not_found / source_fetch_* / sync_failed）把字面量
        "__type_fields__" 原样发给接收端。
        """
        data = {
            "notification_type": "mark_failed",
            "type_display_name": "同步失败",
            "type_icon": "❌",
            "title": "某番",
            "error_message": "boom",
        }
        payload = _manager(tmp_path).render_webhook_payload(data)
        assert "__type_fields__" not in payload.values()
        assert "extra" not in payload

    @pytest.mark.parametrize(
        "notification_type",
        [
            "mark_failed",
            "airing_today",
            "anime_not_found",
            "episode_not_found",
            "request_received",
            "source_fetch_failed",
            "source_fetch_empty",
            "sync_failed",
            "config_error",
        ],
    )
    def test_no_sentinel_leak_for_fieldless_types(self, tmp_path, notification_type):
        payload = _manager(tmp_path).render_webhook_payload(
            {"notification_type": notification_type, "title": "x"}
        )
        assert "__type_fields__" not in str(payload)
        assert "extra" not in payload

    def test_fieldless_type_does_not_gain_bogus_fields(self, tmp_path):
        """无专属字段的类型不应凭空多出字段"""
        payload = _manager(tmp_path).render_webhook_payload(
            {"notification_type": "mark_failed", "title": "x", "error_message": "e"}
        )
        assert payload["error"] == "e"
        assert "summary" not in payload
        assert "job_name" not in payload

    def test_default_template_keeps_common_fields(self, tmp_path):
        payload = _manager(tmp_path).render_webhook_payload(_summary_data())
        assert payload["type"] == "watching_summary_每日总结"
        assert payload["user"] == "tester"

    def test_mark_failed_gains_error_type(self, tmp_path):
        payload = _manager(tmp_path).render_webhook_payload(
            {
                "notification_type": "mark_failed",
                "type_display_name": "同步失败",
                "type_icon": "❌",
                "timestamp": "t",
                "user_name": "u",
                "title": "某番",
                "season": 1,
                "episode": 2,
                "source": "plex",
                "error_message": "boom",
                "error_type": "sync_error",
            }
        )
        assert payload["error"] == "boom"
        assert payload["error_type"] == "sync_error"
        # 不应混入追番总结的字段
        assert "summary" not in payload

    def test_unknown_type_does_not_raise(self, tmp_path):
        payload = _manager(tmp_path).render_webhook_payload(
            {"notification_type": "totally_unknown", "title": "x"}
        )
        assert isinstance(payload, dict)


# ─────────────────────────────────────────────────────────────────────────
# merge_type_fields
# ─────────────────────────────────────────────────────────────────────────


class TestMergeTypeFields:
    def test_merges_into_plain_payload(self, tmp_path):
        """用户自定义的裸模板也要能拿到类型专属字段"""
        merged = _manager(tmp_path).merge_type_fields(
            {"content": "📢 {title}"}, _summary_data()
        )
        assert merged["summary"] == "本周看了 3 部番，共 12 集。"
        assert merged["content"] == "📢 {title}"

    def test_expands_sentinel(self, tmp_path):
        merged = _manager(tmp_path).merge_type_fields(
            {"extra": "__type_fields__"}, _summary_data()
        )
        assert "extra" not in merged
        assert merged["summary"] == "本周看了 3 部番，共 12 集。"

    def test_does_not_override_existing_key(self, tmp_path):
        """用户模板里已显式给出的键优先，不被自动字段覆盖"""
        merged = _manager(tmp_path).merge_type_fields(
            {"summary": "用户自己写的"}, _summary_data()
        )
        assert merged["summary"] == "用户自己写的"

    def test_non_dict_payload_passthrough(self, tmp_path):
        assert _manager(tmp_path).merge_type_fields("plain", _summary_data()) == "plain"
        assert _manager(tmp_path).merge_type_fields([1, 2], _summary_data()) == [1, 2]

    def test_no_fields_leaves_payload_untouched(self, tmp_path):
        payload = {"a": 1}
        merged = _manager(tmp_path).merge_type_fields(
            payload, {"notification_type": "request_received"}
        )
        assert merged == {"a": 1}


# ─────────────────────────────────────────────────────────────────────────
# 用户自定义模板的注入契约（H2）
# ─────────────────────────────────────────────────────────────────────────


class TestUserTemplateInjectionContract:
    """用户自定义模板是「完全替换」：只有显式写哨兵才注入类型特有字段。

    回归：原先无条件补齐，导致只想发 ``{"msg": "{title}"}`` 的用户被静默
    塞入 error_type/additional_info 等未声明的键，严格 schema 的接收端会 400。
    """

    def test_no_sentinel_means_no_injection(self, tmp_path):
        merged = _manager(tmp_path).merge_type_fields(
            {"msg": "T"}, _summary_data(), inject_default=False
        )
        assert merged == {"msg": "T"}
        assert "summary" not in merged
        assert "job_name" not in merged

    def test_explicit_sentinel_triggers_injection(self, tmp_path):
        merged = _manager(tmp_path).merge_type_fields(
            {"msg": "T", "extra": "__type_fields__"},
            _summary_data(),
            inject_default=False,
        )
        assert merged["msg"] == "T"
        assert merged["summary"] == "本周看了 3 部番，共 12 集。"
        assert "extra" not in merged

    def test_sentinel_consumed_even_without_injection(self, tmp_path):
        """声明了哨兵但该类型无专属字段：哨兵仍要被吃掉，不能外发字面量"""
        merged = _manager(tmp_path).merge_type_fields(
            {"msg": "T", "extra": "__type_fields__"},
            {"notification_type": "mark_failed"},
            inject_default=False,
        )
        assert merged == {"msg": "T"}

    def test_default_mode_still_injects(self, tmp_path):
        """内置默认模板这条路径保持无条件补齐（追番总结的 summary 不能丢）"""
        merged = _manager(tmp_path).merge_type_fields({"msg": "T"}, _summary_data())
        assert merged["summary"] == "本周看了 3 部番，共 12 集。"

    def test_nested_sentinel_triggers_injection(self, tmp_path):
        merged = _manager(tmp_path).merge_type_fields(
            {"m": {"inner": "__type_fields__"}}, _summary_data(), inject_default=False
        )
        assert merged["job_name"] == "每日总结"
        assert "__type_fields__" not in str(merged)

    def test_deep_and_list_sentinels_never_leak(self, tmp_path):
        for payload in (
            {"a": {"b": {"c": "__type_fields__"}}},
            {"items": [{"extra": "__type_fields__"}]},
            {"e1": "__type_fields__", "e2": "__type_fields__"},
        ):
            merged = _manager(tmp_path).merge_type_fields(
                payload, _summary_data(), inject_default=False
            )
            assert "__type_fields__" not in str(merged)

    def test_user_keys_win_over_injection(self, tmp_path):
        merged = _manager(tmp_path).merge_type_fields(
            {"extra": "__type_fields__", "summary": "用户自己的"},
            _summary_data(),
            inject_default=False,
        )
        assert merged["summary"] == "用户自己的"
