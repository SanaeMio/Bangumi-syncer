"""
ConfigManager summary 配置 CRUD 方法测试（基于名称）。
"""


def _cm_from_ini(tmp_path, ini_text: str):
    """构建一个指向临时 config.ini 的 ConfigManager，不运行 __init__。"""
    from app.core.config import ConfigManager

    p = tmp_path / "config.ini"
    p.write_text(ini_text, encoding="utf-8")
    cm = ConfigManager.__new__(ConfigManager)
    cm.platform = "Test"
    cm.cwd = tmp_path
    cm.config_paths = {
        "env": None,
        "mounted": tmp_path / "__no_mounted__.ini",
        "dev": tmp_path / "__no_dev__.ini",
        "default": p,
    }
    cm.active_config_path = p
    cm._config_cache = None
    cm._last_modified = 0
    cm._load_config()
    return cm


_SAMPLE_CONFIG = {
    "name": "每日追番总结",
    "enabled": True,
    "cron": "0 21 * * *",
    "lookback_days": 1,
    "user_name": "",
    "system_prompt": "你是一个友好的追番助手。",
    "max_records": 200,
}

_TWO_SUMMARY_INI = """[bangumi]
username = u
[summary-每日总结]
enabled = true
name = 每日总结
cron = 0 21 * * *
lookback_days = 1
user_name = alice
system_prompt = 你好
max_records = 100
[summary-每周总结]
enabled = false
name = 每周总结
cron = 0 9 * * 1
lookback_days = 7
user_name = bob
system_prompt = 周报助手
max_records = 500
[webhook-1]
id = 1
enabled = true
url = http://example.com
"""


class TestGetSummaryConfigs:
    def test_empty_when_no_sections(self, tmp_path):
        cm = _cm_from_ini(tmp_path, "[bangumi]\nusername = u\n")
        configs = cm.get_summary_configs()
        assert configs == []

    def test_returns_all_summary_sections_sorted_by_name(self, tmp_path):
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        configs = cm.get_summary_configs()
        assert len(configs) == 2
        assert configs[1]["name"] == "每日总结"
        assert configs[0]["name"] == "每周总结"

    def test_name_derived_from_section(self, tmp_path):
        """当配置中不存在 name 字段时，从节名称推导。"""
        ini = """[summary-测试]\nenabled = true\ncron = 0 0 * * *\n"""
        cm = _cm_from_ini(tmp_path, ini)
        configs = cm.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["name"] == "测试"

    def test_skips_non_summary_sections(self, tmp_path):
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        configs = cm.get_summary_configs()
        names = [c["name"] for c in configs]
        assert names == ["每周总结", "每日总结"]  # 按 Unicode 排序

    def test_field_values(self, tmp_path):
        ini = """[summary-测试总结]
enabled = true
name = 测试总结
cron = */5 * * * *
lookback_days = 3
user_name = testuser
system_prompt = 你是测试助手
max_records = 50
"""
        cm = _cm_from_ini(tmp_path, ini)
        configs = cm.get_summary_configs()
        assert len(configs) == 1
        c = configs[0]
        assert c["name"] == "测试总结"
        assert c["enabled"] is True
        assert c["cron"] == "*/5 * * * *"
        assert c["lookback_days"] == 3
        assert c["user_name"] == "testuser"
        assert c["system_prompt"] == "你是测试助手"
        assert c["max_records"] == 50


class TestSaveSummaryConfig:
    def test_create_new(self, tmp_path):
        cm = _cm_from_ini(tmp_path, "[bangumi]\nusername = u\n")
        cm.save_summary_config(dict(_SAMPLE_CONFIG))
        configs = cm.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["name"] == "每日追番总结"

    def test_create_new_with_existing(self, tmp_path):
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        cm.save_summary_config(dict(_SAMPLE_CONFIG))
        configs = cm.get_summary_configs()
        assert len(configs) == 3

    def test_update_by_name(self, tmp_path):
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        updated = dict(_SAMPLE_CONFIG)
        updated["name"] = "改名后的总结"
        cm.save_summary_config(updated, old_name="每日总结")

        configs = cm.get_summary_configs()
        names = [c["name"] for c in configs]
        assert "改名后的总结" in names
        assert "每日总结" not in names

    def test_partial_update_only_enabled(self, tmp_path):
        """只传 enabled 时不应写入错误的配置节（回归 summary- 空名称 bug）。"""
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        cm.save_summary_config({"enabled": False}, old_name="每日总结")

        configs = cm.get_summary_configs()
        c = next(c for c in configs if c["name"] == "每日总结")
        assert c["enabled"] is False
        # 其他字段应保持原值
        assert c["cron"] == "0 21 * * *"
        assert c["user_name"] == "alice"

        # 确认没有创建空的 summary- 节
        parser = cm.get_config_parser()
        assert not parser.has_section("summary-")

    def test_save_persists_to_disk(self, tmp_path):
        cm = _cm_from_ini(tmp_path, "[bangumi]\nusername = u\n")
        cm.save_summary_config(dict(_SAMPLE_CONFIG))
        cm2 = _cm_from_ini(tmp_path, cm.active_config_path.read_text(encoding="utf-8"))
        configs = cm2.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["name"] == "每日追番总结"


class TestDeleteSummaryConfig:
    def test_delete_existing(self, tmp_path):
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        cm.delete_summary_config("每日总结")
        configs = cm.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["name"] == "每周总结"

    def test_delete_nonexistent_no_error(self, tmp_path):
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        cm.delete_summary_config("不存在")
        configs = cm.get_summary_configs()
        assert len(configs) == 2

    def test_delete_persists(self, tmp_path):
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        cm.delete_summary_config("每日总结")
        cm2 = _cm_from_ini(tmp_path, cm.active_config_path.read_text(encoding="utf-8"))
        configs = cm2.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["name"] == "每周总结"


_RENAME_INI = """[bangumi]
username = u
[summary-旧任务]
enabled = true
name = 旧任务
cron = 0 21 * * *
[webhook-1]
id = 1
enabled = true
url = http://example.com
types = mark_success, watching_summary_旧任务, mark_failed
[webhook-2]
id = 2
enabled = true
url = http://other.com
types = all
[email-1]
id = 1
enabled = true
smtp_server = smtp.example.com
types = watching_summary_旧任务, mark_failed
[email-2]
id = 2
enabled = false
smtp_server = smtp2.example.com
types = mark_success
"""


class TestRenameNotificationType:
    """rename_notification_type 测试。"""

    def test_replaces_in_webhook_types(self, tmp_path):
        cm = _cm_from_ini(tmp_path, _RENAME_INI)
        updated = cm.rename_notification_type(
            "watching_summary_旧任务", "watching_summary_新任务"
        )
        assert updated >= 2  # webhook-1 + email-1
        config = cm.get_config_parser()
        wh_types = config.get("notify-webhook-1", "types")
        assert "watching_summary_旧任务" not in wh_types
        assert "watching_summary_新任务" in wh_types
        assert "mark_success" in wh_types
        assert "mark_failed" in wh_types

    def test_replaces_in_email_types(self, tmp_path):
        cm = _cm_from_ini(tmp_path, _RENAME_INI)
        cm.rename_notification_type(
            "watching_summary_旧任务", "watching_summary_新任务"
        )
        config = cm.get_config_parser()
        em_types = config.get("notify-email-1", "types")
        assert "watching_summary_旧任务" not in em_types
        assert "watching_summary_新任务" in em_types

    def test_skips_all_types(self, tmp_path):
        cm = _cm_from_ini(tmp_path, _RENAME_INI)
        cm.rename_notification_type(
            "watching_summary_旧任务", "watching_summary_新任务"
        )
        config = cm.get_config_parser()
        assert config.get("notify-webhook-2", "types") == "all"

    def test_no_match_returns_zero(self, tmp_path):
        cm = _cm_from_ini(tmp_path, _RENAME_INI)
        updated = cm.rename_notification_type(
            "watching_summary_不存在", "watching_summary_xxx"
        )
        assert updated == 0

    def test_persists_to_disk(self, tmp_path):
        cm = _cm_from_ini(tmp_path, _RENAME_INI)
        cm.rename_notification_type(
            "watching_summary_旧任务", "watching_summary_新任务"
        )
        cm2 = _cm_from_ini(tmp_path, cm.active_config_path.read_text(encoding="utf-8"))
        config = cm2.get_config_parser()
        assert "watching_summary_新任务" in config.get("notify-webhook-1", "types")
