"""config_secret_crypto 单元测试。"""

from configparser import ConfigParser
from unittest.mock import patch

from app.core import config_secret_crypto as csc


def test_is_sensitive_ini_field():
    assert csc.is_sensitive_ini_field("bangumi", "access_token")
    assert csc.is_sensitive_ini_field("bangumi-user1", "access_token")
    assert not csc.is_sensitive_ini_field("bangumi-data", "access_token")
    assert not csc.is_sensitive_ini_field("bangumi-mapping", "access_token")
    assert csc.is_sensitive_ini_field("auth", "webhook_key")
    assert not csc.is_sensitive_ini_field("auth", "password")
    assert csc.is_sensitive_ini_field("email-1", "smtp_password")
    assert csc.is_sensitive_ini_field("trakt", "client_secret")
    assert not csc.is_sensitive_ini_field("bangumi", "username")
    assert not csc.is_sensitive_ini_field("auth", "secret_key")


def test_encrypt_decrypt_roundtrip():
    with patch.object(csc, "_master_secret", return_value="test-secret-key-for-hkdf"):
        plain = "hello-token-你好"
        enc = csc.encrypt(plain)
        assert enc.startswith(csc.PREFIX)
        assert csc.decrypt(enc) == plain


def test_encrypt_idempotent_when_already_wrapped():
    with patch.object(csc, "_master_secret", return_value="same-key"):
        enc = csc.encrypt("plain")
        assert csc.encrypt(enc) == enc


def test_decrypt_plaintext_passthrough():
    with patch.object(csc, "_master_secret", return_value="k"):
        assert csc.decrypt("no-prefix-plain") == "no-prefix-plain"


def test_decrypt_none_returns_empty():
    assert csc.decrypt(None) == ""


def test_encrypt_empty():
    assert csc.encrypt("") == ""


def test_encrypt_without_master_returns_plaintext():
    """无 secret_key 时不应写入不可解的占位，保持明文。"""
    with patch.object(csc, "_master_secret", return_value=""):
        assert csc.encrypt("still-plain") == "still-plain"


def test_decrypt_wrong_key_returns_ciphertext_string():
    """密文无法用当前 master 解密时回退为原串（兼容损坏或轮换密钥）。"""
    with patch.object(csc, "_master_secret", return_value="key-a"):
        enc = csc.encrypt("secret")
    with patch.object(csc, "_master_secret", return_value="key-b-totally-different"):
        out = csc.decrypt(enc)
    assert out == enc


def test_decrypt_malformed_token_after_prefix():
    with patch.object(csc, "_master_secret", return_value="m" * 20):
        bad = csc.PREFIX + "!!!not-a-fernet-token!!!"
        assert csc.decrypt(bad) == bad


def test_encrypt_if_sensitive_non_sensitive_passthrough():
    with patch.object(csc, "_master_secret", return_value="x" * 20):
        assert csc.encrypt_if_sensitive("bangumi", "username", "bob") == "bob"


def test_encrypt_if_sensitive_access_token_encrypts():
    with patch.object(csc, "_master_secret", return_value="x" * 20):
        out = csc.encrypt_if_sensitive("bangumi", "access_token", "tok")
        assert out.startswith(csc.PREFIX)
        assert csc.decrypt(out) == "tok"


def test_decrypt_if_sensitive_non_sensitive_unchanged():
    assert csc.decrypt_if_sensitive("bangumi", "username", "bob") == "bob"


def test_decrypt_if_sensitive_non_string_unchanged():
    assert csc.decrypt_if_sensitive("bangumi", "access_token", None) is None
    assert csc.decrypt_if_sensitive("email-1", "smtp_port", 465) == 465


def test_decrypt_if_sensitive_decrypts_token():
    with patch.object(csc, "_master_secret", return_value="z" * 20):
        enc = csc.encrypt("t")
        assert csc.decrypt_if_sensitive("bangumi", "access_token", enc) == "t"


def test_decrypt_api_config_payload_multi_accounts():
    with patch.object(csc, "_master_secret", return_value="master"):
        enc = csc.encrypt("tok")
        data = {
            "bangumi": {"username": "u", "access_token": enc},
            "multi_accounts": {
                "acc1": {"username": "u1", "access_token": enc},
            },
        }
        csc.decrypt_api_config_payload(data)
        assert data["bangumi"]["access_token"] == "tok"
        assert data["multi_accounts"]["acc1"]["access_token"] == "tok"


def test_decrypt_api_config_payload_auth_webhook_and_trakt():
    with patch.object(csc, "_master_secret", return_value="master"):
        w = csc.encrypt("whk")
        s = csc.encrypt("cs")
        data = {
            "auth": {"webhook_key": w, "username": "admin"},
            "trakt": {"client_id": "id", "client_secret": s},
        }
        csc.decrypt_api_config_payload(data)
        assert data["auth"]["webhook_key"] == "whk"
        assert data["trakt"]["client_secret"] == "cs"
        assert data["auth"]["username"] == "admin"


def test_decrypt_api_config_payload_plaintext_passthrough():
    data = {"bangumi": {"access_token": "legacy-plain"}}
    csc.decrypt_api_config_payload(data)
    assert data["bangumi"]["access_token"] == "legacy-plain"


def test_decrypt_api_config_payload_skips_non_dict_top_level():
    data = {"sync": "not-a-dict", "bangumi": {"access_token": "plain"}}
    csc.decrypt_api_config_payload(data)
    assert data["sync"] == "not-a-dict"


def test_migrate_plaintext_sensitive_fields():
    cfg = ConfigParser()
    cfg.add_section("bangumi")
    cfg.set("bangumi", "username", "u")
    cfg.set("bangumi", "access_token", "plain-tok")
    cfg.add_section("trakt")
    cfg.set("trakt", "client_id", "id")
    cfg.set("trakt", "client_secret", "sec")

    with patch.object(csc, "_master_secret", return_value="m" * 20):
        assert csc.migrate_plaintext_sensitive_fields(cfg) is True
        assert cfg.get("bangumi", "access_token").startswith(csc.PREFIX)
        assert cfg.get("trakt", "client_secret").startswith(csc.PREFIX)
        assert csc.decrypt(cfg.get("bangumi", "access_token")) == "plain-tok"


def test_migrate_empty_master_returns_false():
    cfg = ConfigParser()
    cfg.add_section("bangumi")
    cfg.set("bangumi", "access_token", "plain")
    with patch.object(csc, "_master_secret", return_value=""):
        assert csc.migrate_plaintext_sensitive_fields(cfg) is False
    assert cfg.get("bangumi", "access_token") == "plain"


def test_migrate_skips_empty_and_already_encrypted():
    cfg = ConfigParser()
    cfg.add_section("bangumi")
    cfg.set("bangumi", "access_token", "")
    cfg.add_section("email-1")
    cfg.set("email-1", "smtp_password", "")
    with patch.object(csc, "_master_secret", return_value="m" * 20):
        assert csc.migrate_plaintext_sensitive_fields(cfg) is False

    with patch.object(csc, "_master_secret", return_value="m" * 20):
        enc = csc.encrypt("one")
        cfg.set("bangumi", "access_token", enc)
        assert csc.migrate_plaintext_sensitive_fields(cfg) is False


def test_migrate_migrates_bangumi_dash_webhook_and_email_password():
    cfg = ConfigParser()
    cfg.add_section("bangumi-user99")
    cfg.set("bangumi-user99", "username", "u99")
    cfg.set("bangumi-user99", "access_token", "multi-plain")
    cfg.add_section("auth")
    cfg.set("auth", "webhook_key", "wh-plain")
    cfg.add_section("email-2")
    cfg.set("email-2", "smtp_password", "pw-plain")

    with patch.object(csc, "_master_secret", return_value="m" * 22):
        assert csc.migrate_plaintext_sensitive_fields(cfg) is True
        assert csc.decrypt(cfg.get("bangumi-user99", "access_token")) == "multi-plain"
        assert csc.decrypt(cfg.get("auth", "webhook_key")) == "wh-plain"
        assert csc.decrypt(cfg.get("email-2", "smtp_password")) == "pw-plain"


def test_migrate_second_run_returns_false():
    cfg = ConfigParser()
    cfg.add_section("bangumi")
    cfg.set("bangumi", "access_token", "p")
    with patch.object(csc, "_master_secret", return_value="m" * 20):
        assert csc.migrate_plaintext_sensitive_fields(cfg) is True
        assert csc.migrate_plaintext_sensitive_fields(cfg) is False
