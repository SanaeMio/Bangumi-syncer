"""
ConfigManager tests - Simplified version
"""

import os
from unittest.mock import MagicMock, patch

import pytest


class TestConfigManagerSimple:
    """Test ConfigManager class with simplified tests"""

    def test_get_config_paths(self, temp_dir):
        """Test getting config paths returns dict with expected keys"""
        from app.core.config import ConfigManager

        with patch.object(ConfigManager, "__init__", lambda x: None):
            cm = ConfigManager.__new__(ConfigManager)
            cm.cwd = temp_dir
            paths = cm._get_config_paths()

            assert "env" in paths
            assert "mounted" in paths
            assert "dev" in paths
            assert "default" in paths

    def test_find_active_config_with_env(self, temp_dir):
        """Test finding active config from environment"""
        env_config = temp_dir / "env_config.ini"
        env_config.write_text("[bangumi]\nusername = env_user\n")

        with patch.dict(os.environ, {"CONFIG_FILE": str(env_config)}):
            from app.core.config import ConfigManager

            with patch.object(ConfigManager, "__init__", lambda x: None):
                cm = ConfigManager.__new__(ConfigManager)
                cm.config_paths = {
                    "env": str(env_config),
                    "mounted": temp_dir / "mounted.ini",
                    "dev": temp_dir / "dev.ini",
                    "default": temp_dir / "default.ini",
                }

                result = cm._find_active_config()
                assert result == env_config

    def test_apply_env_overrides(self, temp_dir):
        """Test applying environment variable overrides"""
        from configparser import ConfigParser

        from app.core.config import ConfigManager

        with patch.object(ConfigManager, "__init__", lambda x: None):
            cm = ConfigManager.__new__(ConfigManager)

            config = ConfigParser()
            config.read_string("[bangumi]\nusername = original\n")

            with patch.dict(os.environ, {"BANGUMI_USERNAME": "env_user"}):
                cm._apply_env_overrides(config)

            assert config.get("bangumi", "username") == "env_user"

    def test_type_conversion(self, temp_dir):
        """Test type conversion in get_config"""
        from configparser import ConfigParser

        config = ConfigParser()
        config.read_string(
            """
[settings]
bool_true = true
bool_false = false
int_value = 42
string_value = hello
"""
        )

        # Test conversion logic
        def convert_value(value):
            if value.lower() in ("true", "false"):
                return value.lower() == "true"
            elif value.isdigit():
                return int(value)
            return value

        assert convert_value("true") is True
        assert convert_value("false") is False
        assert convert_value("42") == 42
        assert convert_value("hello") == "hello"


def _config_manager_from_ini(tmp_path, ini_text: str):
    """构造不跑 __init__（无 banner / 迁移）的 ConfigManager，指向临时 config.ini。"""
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


class TestConfigManagerBranches:
    def test_find_active_config_prefers_mounted_over_default(self, tmp_path):
        from app.core.config import ConfigManager

        default_ini = tmp_path / "config.ini"
        default_ini.write_text("[sync]\nmode = single\n", encoding="utf-8")
        mounted_ini = tmp_path / "mounted.ini"
        mounted_ini.write_text("[sync]\nmode = multi\n", encoding="utf-8")
        cm = ConfigManager.__new__(ConfigManager)
        cm.cwd = tmp_path
        cm.config_paths = {
            "env": None,
            "mounted": mounted_ini,
            "dev": tmp_path / "nodev.ini",
            "default": default_ini,
        }
        assert cm._find_active_config() == mounted_ini

    def test_find_active_config_prefers_dev_when_no_mounted(self, tmp_path):
        from app.core.config import ConfigManager

        default_ini = tmp_path / "config.ini"
        default_ini.write_text("[sync]\nmode = single\n", encoding="utf-8")
        dev_ini = tmp_path / "config.dev.ini"
        dev_ini.write_text("[sync]\nmode = dev\n", encoding="utf-8")
        cm = ConfigManager.__new__(ConfigManager)
        cm.cwd = tmp_path
        cm.config_paths = {
            "env": None,
            "mounted": tmp_path / "nomount.ini",
            "dev": dev_ini,
            "default": default_ini,
        }
        assert cm._find_active_config() == dev_ini

    def test_get_config_parser_reload_when_file_mtime_changes(self, tmp_path):
        cm = _config_manager_from_ini(tmp_path, "[a]\nx = 1\n")
        first = cm.get_config_parser()
        assert first.get("a", "x") == "1"
        # 触盘更新 mtime
        p = tmp_path / "config.ini"
        p.write_text("[a]\nx = 2\n", encoding="utf-8")
        second = cm.get_config_parser()
        assert second.get("a", "x") == "2"

    def test_get_section_missing_returns_fallback(self, tmp_path):
        cm = _config_manager_from_ini(tmp_path, "[sync]\nmode = single\n")
        assert cm.get_section("nope", {"k": "v"}) == {"k": "v"}
        assert cm.get_section("nope") == {}

    def test_get_config_missing_section_and_option(self, tmp_path):
        cm = _config_manager_from_ini(tmp_path, "[sync]\nmode = single\n")
        assert cm.get_config("missing", "k", fallback="d") == "d"
        assert cm.get("sync", "nope", fallback=9) == 9

    def test_get_set_reload_aliases(self, tmp_path):
        cm = _config_manager_from_ini(tmp_path, "[sync]\nmode = single\n")
        cm.set("sync", "extra", "hello")
        assert cm.get("sync", "extra") == "hello"
        cm.reload()
        assert cm.get("sync", "extra") == "hello"

    def test_get_config_decrypt_returns_non_str_uses_raw(self, tmp_path):
        cm = _config_manager_from_ini(tmp_path, "[x]\ny = enc\n")
        with patch(
            "app.core.config_secret_crypto.decrypt_if_sensitive", return_value=42
        ):
            assert cm.get_config("x", "y") == "enc"

    def test_get_section_decrypt_if_sensitive(self, tmp_path):
        cm = _config_manager_from_ini(tmp_path, "[x]\na = v\n")
        with patch(
            "app.core.config_secret_crypto.decrypt_if_sensitive", return_value="dec"
        ):
            d = cm.get_section("x")
            assert d.get("a") == "dec"

    def test_get_bangumi_configs_and_user_mappings(self, tmp_path):
        ini = """
[bangumi-data]
enabled = false
[bangumi-main]
username = u1
access_token = t1
media_server_username = plex_a
[bangumi-alt]
username = u2
access_token = t2
"""
        cm = _config_manager_from_ini(tmp_path, ini)
        cfgs = cm.get_bangumi_configs()
        assert "bangumi-main" in cfgs and "bangumi-alt" in cfgs
        assert "bangumi-data" not in cfgs
        m = cm.get_user_mappings()
        assert m["plex_a"] == "bangumi-main"

    def test_get_trakt_config_defaults_and_bool_strings(self, tmp_path):
        cm = _config_manager_from_ini(
            tmp_path,
            "[trakt]\nclient_id = cid\ndefault_enabled = yes\n",
        )
        t = cm.get_trakt_config()
        assert t["client_id"] == "cid"
        assert t["default_enabled"] is True
        assert "redirect_uri" in t

    def test_get_trakt_config_empty_when_no_section(self, tmp_path):
        cm = _config_manager_from_ini(tmp_path, "[sync]\nx=1\n")
        assert cm.get_trakt_config() == {}

    def test_get_scheduler_config_defaults_and_invalid_int(self, tmp_path):
        cm = _config_manager_from_ini(
            tmp_path,
            "[scheduler]\nstartup_delay = notint\njob_timeout = 120\n",
        )
        s = cm.get_scheduler_config()
        assert s["startup_delay"] == 30
        assert s["job_timeout"] == 120

    def test_get_scheduler_config_empty(self, tmp_path):
        cm = _config_manager_from_ini(tmp_path, "[sync]\nx=1\n")
        assert cm.get_scheduler_config() == {}

    def test_get_feiniu_config_coercions(self, tmp_path):
        cm = _config_manager_from_ini(
            tmp_path,
            "[feiniu]\nenabled = true\nmin_percent = bad\nlimit = x\n",
        )
        f = cm.get_feiniu_config()
        assert f["enabled"] is True
        assert f["min_percent"] == 85
        assert f["limit"] == 100

    def test_get_all_config_multi_accounts(self, tmp_path):
        ini = """
[sync]
mode = single
[bangumi-main]
username = u
access_token = t
display_name = 主账号
[web]
base_path = /
"""
        cm = _config_manager_from_ini(tmp_path, ini)
        allc = cm.get_all_config()
        assert "multi_accounts" in allc
        assert "主账号" in allc["multi_accounts"]
        assert "web" in allc

    def test_reload_multi_account_configs(self, tmp_path):
        cm = _config_manager_from_ini(
            tmp_path,
            "[bangumi-main]\nusername=u\naccess_token=t\n",
        )
        with patch("app.core.logging.logger") as mock_log:
            cm.reload_multi_account_configs()
        mock_log.info.assert_called()

    def test_needs_migration_webhook_only(self, tmp_path):
        cm = _config_manager_from_ini(
            tmp_path,
            "[notification]\nwebhook_url = http://x\n",
        )
        assert cm._needs_migration() is True

    def test_needs_migration_false_when_new_structure(self, tmp_path):
        cm = _config_manager_from_ini(
            tmp_path,
            "[notification]\nwebhook_url = http://x\n[webhook-1]\nid=1\n",
        )
        assert cm._needs_migration() is False

    def test_migrate_webhook_full_and_incomplete(self, tmp_path):
        from app.core.config import ConfigManager

        full_ini = """[notification]
webhook_enabled = True
webhook_url = http://hook
webhook_method = POST
webhook_headers = {"h":"1"}
webhook_template = {"t":2}
"""
        cm = _config_manager_from_ini(tmp_path, full_ini)
        with patch.object(cm, "_save_config", wraps=cm._save_config):
            cm._migrate_webhook_config()
        cfg = cm.get_config_parser()
        assert cfg.has_section("webhook-1")
        assert cfg.get("webhook-1", "url") == "http://hook"

        p2 = tmp_path / "config2.ini"
        p2.write_text(
            "[notification]\nwebhook_url = http://only\n",
            encoding="utf-8",
        )
        cm2 = ConfigManager.__new__(ConfigManager)
        cm2.cwd = tmp_path
        cm2.active_config_path = p2
        cm2.config_paths = {"default": p2, "env": None, "mounted": p2, "dev": p2}
        cm2._config_cache = None
        cm2._last_modified = 0
        cm2._load_config()
        cm2._migrate_webhook_config()
        assert not cm2.get_config_parser().has_option("notification", "webhook_url")

    def test_migrate_email_full(self, tmp_path):
        ini = """[notification]
email_enabled = True
smtp_server = smtp.example.com
smtp_port = 465
smtp_username = u
smtp_password = p
smtp_use_tls = True
email_from = from@x.com
email_to = to@x.com
email_subject = subj
email_template_file = tpl.html
"""
        cm = _config_manager_from_ini(tmp_path, ini)
        cm._migrate_email_config()
        cfg = cm.get_config_parser()
        assert cfg.has_section("email-1")
        assert cfg.get("email-1", "smtp_server") == "smtp.example.com"

    def test_migrate_email_incomplete_removes_notification(self, tmp_path):
        ini = """[notification]
email_enabled = True
smtp_server = smtp.example.com
"""
        cm = _config_manager_from_ini(tmp_path, ini)
        cm._migrate_email_config()
        cfg = cm.get_config_parser()
        assert not cfg.has_section("notification")

    def test_needs_migration_email_only(self, tmp_path):
        cm = _config_manager_from_ini(
            tmp_path,
            "[notification]\nemail_enabled = True\n",
        )
        assert cm._needs_migration() is True

    def test_find_active_config_env_path_missing_falls_through(self, tmp_path):
        from app.core.config import ConfigManager

        default_ini = tmp_path / "config.ini"
        default_ini.write_text("[sync]\nmode = single\n", encoding="utf-8")
        cm = ConfigManager.__new__(ConfigManager)
        cm.cwd = tmp_path
        cm.config_paths = {
            "env": str(tmp_path / "missing.ini"),
            "mounted": tmp_path / "nomount.ini",
            "dev": tmp_path / "nodev.ini",
            "default": default_ini,
        }
        assert cm._find_active_config() == default_ini

    def test_check_config_updated_false_when_file_missing(self, tmp_path):
        cm = _config_manager_from_ini(tmp_path, "[a]\nx=1\n")
        cm.active_config_path = tmp_path / "gone.ini"
        assert cm._check_config_updated() is False

    def test_save_config_roundtrip(self, tmp_path):
        cm = _config_manager_from_ini(tmp_path, "[sync]\nmode = single\n")
        cm.set_config("sync", "k_extra", "v99")
        cm.save_config()
        cm2 = _config_manager_from_ini(tmp_path, cm.active_config_path.read_text(encoding="utf-8"))
        assert cm2.get("sync", "k_extra") == "v99"
