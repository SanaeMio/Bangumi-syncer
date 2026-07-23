"""
Tests for ConfigManager summary config CRUD methods.
"""


def _cm_from_ini(tmp_path, ini_text: str):
    """Build a ConfigManager pointing to a temp config.ini, without running __init__."""
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


# Sample config dict for creating/updating summary configs
_SAMPLE_CONFIG = {
    "enabled": True,
    "name": "每日追番总结",
    "cron": "0 21 * * *",
    "lookback_days": 1,
    "user_name": "",
    "system_prompt": "你是一个友好的追番助手。",
    "max_records": 200,
}

# Valid ini with two summary sections
_TWO_SUMMARY_INI = """[bangumi]
username = u
[summary-1]
id = 1
enabled = true
name = 每日总结
cron = 0 21 * * *
lookback_days = 1
user_name = alice
system_prompt = 你好
max_records = 100
[summary-2]
id = 2
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
    """Tests for ConfigManager.get_summary_configs()."""

    def test_empty_when_no_sections(self, tmp_path):
        """Returns empty list when no [summary-N] sections exist."""
        cm = _cm_from_ini(tmp_path, "[bangumi]\nusername = u\n")
        configs = cm.get_summary_configs()
        assert configs == []

    def test_returns_all_summary_sections_sorted_by_id(self, tmp_path):
        """Returns all [summary-N] sections sorted by id."""
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        configs = cm.get_summary_configs()
        assert len(configs) == 2
        assert configs[0]["id"] == 1
        assert configs[0]["name"] == "每日总结"
        assert configs[1]["id"] == 2
        assert configs[1]["name"] == "每周总结"

    def test_skips_non_summary_sections(self, tmp_path):
        """Does not include webhook, email, or other non-summary sections."""
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        configs = cm.get_summary_configs()
        ids = [c["id"] for c in configs]
        # webhook-1 has id=1 but should NOT appear here
        assert len(ids) == 2
        assert 1 in ids
        assert 2 in ids

    def test_field_values(self, tmp_path):
        """All 8 fields are correctly read."""
        ini = """[summary-1]
id = 1
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
        assert c["id"] == 1
        assert c["enabled"] is True
        assert c["name"] == "测试总结"
        assert c["cron"] == "*/5 * * * *"
        assert c["lookback_days"] == 3
        assert c["user_name"] == "testuser"
        assert c["system_prompt"] == "你是测试助手"
        assert c["max_records"] == 50


class TestSaveSummaryConfig:
    """Tests for ConfigManager.save_summary_config()."""

    def test_create_new_auto_id(self, tmp_path):
        """Creating a config without an id auto-assigns the next sequential id."""
        cm = _cm_from_ini(tmp_path, "[bangumi]\nusername = u\n")
        cm.save_summary_config(dict(_SAMPLE_CONFIG))
        configs = cm.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["id"] == 1
        assert configs[0]["name"] == "每日追番总结"
        assert configs[0]["enabled"] is True

    def test_create_new_with_existing_sections_auto_id(self, tmp_path):
        """Auto-assigned id should be count+1 when other summary sections exist."""
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        cm.save_summary_config(dict(_SAMPLE_CONFIG))
        configs = cm.get_summary_configs()
        assert len(configs) == 3
        ids = [c["id"] for c in configs]
        assert ids == [1, 2, 3]

    def test_update_existing_by_id(self, tmp_path):
        """Providing an existing id updates that section in-place."""
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        updated = dict(_SAMPLE_CONFIG)
        updated["id"] = 1
        updated["name"] = "改名后的总结"
        updated["enabled"] = False
        cm.save_summary_config(updated)

        configs = cm.get_summary_configs()
        assert len(configs) == 2
        c1 = configs[0]
        assert c1["id"] == 1
        assert c1["name"] == "改名后的总结"
        assert c1["enabled"] is False
        # Config 2 should be unchanged
        c2 = configs[1]
        assert c2["id"] == 2
        assert c2["name"] == "每周总结"

    def test_save_persists_to_disk(self, tmp_path):
        """Saved config should be readable after a fresh reload."""
        cm = _cm_from_ini(tmp_path, "[bangumi]\nusername = u\n")
        cm.save_summary_config(dict(_SAMPLE_CONFIG))

        cm2 = _cm_from_ini(tmp_path, cm.active_config_path.read_text(encoding="utf-8"))
        configs = cm2.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["name"] == "每日追番总结"


class TestDeleteSummaryConfig:
    """Tests for ConfigManager.delete_summary_config()."""

    def test_delete_existing(self, tmp_path):
        """Deleting an existing section removes it and re-indexes remaining."""
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        cm.delete_summary_config(1)
        configs = cm.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["id"] == 1  # re-indexed to sequential id=1
        assert configs[0]["name"] == "每周总结"  # was original id=2

    def test_delete_nonexistent_no_error(self, tmp_path):
        """Deleting a non-existent id does not raise. Use no-op as default behavior."""
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        cm.delete_summary_config(99)
        configs = cm.get_summary_configs()
        assert len(configs) == 2  # unchanged

    def test_delete_reindex_sequential(self, tmp_path):
        """After deletion, remaining sections have sequential IDs (1, 2, ...)."""
        # Create 3 sections
        ini = """[summary-1]
id = 1
enabled = true
name = 总结1
cron = 0 0 * * *
lookback_days = 1
user_name =
system_prompt = p1
max_records = 10
[summary-2]
id = 2
enabled = true
name = 总结2
cron = 0 0 * * *
lookback_days = 1
user_name =
system_prompt = p2
max_records = 10
[summary-3]
id = 3
enabled = true
name = 总结3
cron = 0 0 * * *
lookback_days = 1
user_name =
system_prompt = p3
max_records = 10
"""
        cm = _cm_from_ini(tmp_path, ini)

        # Delete the middle one (id=2)
        cm.delete_summary_config(2)
        configs = cm.get_summary_configs()
        assert len(configs) == 2
        assert configs[0]["id"] == 1
        assert configs[0]["name"] == "总结1"
        assert configs[1]["id"] == 2
        assert configs[1]["name"] == "总结3"

        # Verify section names are sequential in the raw parser
        parser = cm.get_config_parser()
        sections = [s for s in parser.sections() if s.startswith("summary-")]
        assert sorted(sections) == ["summary-1", "summary-2"]

    def test_delete_last_remaining(self, tmp_path):
        """Deleting the only summary section leaves an empty list."""
        ini = """[summary-1]
id = 1
enabled = true
name = 唯一总结
cron = 0 0 * * *
lookback_days = 1
user_name =
system_prompt = p
max_records = 10
"""
        cm = _cm_from_ini(tmp_path, ini)
        cm.delete_summary_config(1)
        configs = cm.get_summary_configs()
        assert configs == []

    def test_delete_persists_reindex(self, tmp_path):
        """Re-indexing after delete is persisted to disk."""
        cm = _cm_from_ini(tmp_path, _TWO_SUMMARY_INI)
        cm.delete_summary_config(1)

        cm2 = _cm_from_ini(tmp_path, cm.active_config_path.read_text(encoding="utf-8"))
        configs = cm2.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["id"] == 1
        assert configs[0]["name"] == "每周总结"
