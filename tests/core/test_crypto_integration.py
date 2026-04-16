"""
敏感配置加解密端到端集成测试：真实 ConfigParser + 临时 ini、Security 迁移、webhook 刷新与校验。
"""

from configparser import ConfigParser
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core import config as config_pkg, security as security_pkg


def _read_ini_raw(path: Path, section: str, option: str) -> str:
    p = ConfigParser()
    p.read(path, encoding="utf-8-sig")
    return p.get(section, option)


def _auth_block(
    secret_key: str = "integration_test_secret_key_must_be_long_enough_string",
    webhook_key: str = "plain-webhook-before-migrate",
    webhook_auth: str = "false",
) -> str:
    return f"""[auth]
enabled = false
username = admin
password = {"a" * 64}
session_timeout = 3600
secret_key = {secret_key}
https_only = false
max_login_attempts = 5
lockout_duration = 900
webhook_key = {webhook_key}
webhook_auth_enabled = {webhook_auth}
"""


@pytest.fixture
def tmp_ini(tmp_path: Path) -> Path:
    ini = tmp_path / "integration_config.ini"
    ini.write_text(
        _auth_block()
        + """
[bangumi]
username = bgm_user
access_token = plain-bgm-token-for-integration
private = false

[trakt]
client_id = cid
client_secret = plain-trakt-secret
redirect_uri = http://localhost:8000/api/trakt/auth/callback
default_sync_interval = 0 */6 * * *
default_enabled = true
""",
        encoding="utf-8",
    )
    return ini


def test_config_manager_set_get_roundtrip_and_file_is_encrypted(
    tmp_ini: Path, monkeypatch: pytest.MonkeyPatch
):
    """ConfigManager 写入敏感项后磁盘为 BGS1，读取为明文。"""
    monkeypatch.setenv("CONFIG_FILE", str(tmp_ini))
    mock_si = MagicMock()
    with patch("app.core.startup_info.startup_info", mock_si):
        cm = config_pkg.ConfigManager()

    assert cm.get("bangumi", "access_token") == "plain-bgm-token-for-integration"

    cm.set_config("bangumi", "access_token", "rotated-token-新")

    raw = _read_ini_raw(tmp_ini, "bangumi", "access_token")
    assert raw.startswith("BGS1:")
    assert cm.get("bangumi", "access_token") == "rotated-token-新"


def test_security_init_migrates_plaintext_sensitive_to_disk(
    tmp_ini: Path, monkeypatch: pytest.MonkeyPatch
):
    """模拟老版本明文 ini：SecurityManager 初始化后迁移为密文并落盘。"""
    monkeypatch.setenv("CONFIG_FILE", str(tmp_ini))
    mock_si = MagicMock()
    with patch("app.core.startup_info.startup_info", mock_si):
        cm = config_pkg.ConfigManager()

    monkeypatch.setattr(config_pkg, "config_manager", cm)
    monkeypatch.setattr(security_pkg, "config_manager", cm)

    sm = security_pkg.SecurityManager()
    assert isinstance(sm, security_pkg.SecurityManager)

    raw_bgm = _read_ini_raw(tmp_ini, "bangumi", "access_token")
    raw_wh = _read_ini_raw(tmp_ini, "auth", "webhook_key")
    raw_trakt = _read_ini_raw(tmp_ini, "trakt", "client_secret")

    assert raw_bgm.startswith("BGS1:")
    assert raw_wh.startswith("BGS1:")
    assert raw_trakt.startswith("BGS1:")

    assert cm.get("bangumi", "access_token") == "plain-bgm-token-for-integration"
    assert cm.get("auth", "webhook_key") == "plain-webhook-before-migrate"
    assert cm.get("trakt", "client_secret") == "plain-trakt-secret"


def test_refresh_webhook_key_verify_webhook_key_roundtrip(
    tmp_ini: Path, monkeypatch: pytest.MonkeyPatch
):
    """开启 webhook 校验后：刷新返回明文，磁盘密文，verify 通过。"""
    content = _auth_block(webhook_auth="true")
    tmp_ini.write_text(
        content
        + """
[bangumi]
username = u
access_token = tok
private = false
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("CONFIG_FILE", str(tmp_ini))
    mock_si = MagicMock()
    with patch("app.core.startup_info.startup_info", mock_si):
        cm = config_pkg.ConfigManager()

    monkeypatch.setattr(config_pkg, "config_manager", cm)
    monkeypatch.setattr(security_pkg, "config_manager", cm)

    sm = security_pkg.SecurityManager()

    raw_before = _read_ini_raw(tmp_ini, "auth", "webhook_key")
    assert raw_before.startswith("BGS1:")
    old_plain = cm.get("auth", "webhook_key")
    assert sm.verify_webhook_key(old_plain)

    new_plain = sm.refresh_webhook_key()
    assert len(new_plain) > 20

    raw_after = _read_ini_raw(tmp_ini, "auth", "webhook_key")
    assert raw_after.startswith("BGS1:")
    assert raw_after != raw_before

    assert sm.verify_webhook_key(new_plain)
    assert not sm.verify_webhook_key(old_plain)


def test_verify_webhook_key_skips_when_auth_disabled(
    tmp_ini: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CONFIG_FILE", str(tmp_ini))
    mock_si = MagicMock()
    with patch("app.core.startup_info.startup_info", mock_si):
        cm = config_pkg.ConfigManager()

    monkeypatch.setattr(config_pkg, "config_manager", cm)
    monkeypatch.setattr(security_pkg, "config_manager", cm)

    sm = security_pkg.SecurityManager()
    assert sm.verify_webhook_key("any-wrong-key") is True
