"""
配置文件敏感项对称加密：由 auth.secret_key 经 HKDF 派生 Fernet 密钥，ini 中存 BGS1: 前缀密文。
"""

from __future__ import annotations

import base64
from configparser import ConfigParser
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIX = "BGS1:"
_HKDF_SALT = b"bangumi-syncer-config-v1"
_HKDF_INFO = b"config-secret-fernet-v1"

_EXCLUDED_BANGUMI_LIKE = frozenset({"bangumi-data", "bangumi-mapping"})


def is_sensitive_ini_field(section: str, option: str) -> bool:
    if option == "access_token":
        if section == "bangumi":
            return True
        if section.startswith("bangumi-") and section not in _EXCLUDED_BANGUMI_LIKE:
            return True
        return False
    if section == "auth" and option == "webhook_key":
        return True
    if section.startswith("email-") and option == "smtp_password":
        return True
    if section == "trakt" and option == "client_secret":
        return True
    return False


def _derive_fernet_key(master: str) -> bytes:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    return base64.urlsafe_b64encode(hkdf.derive(master.encode("utf-8")))


def _fernet_for_master(master: str) -> Fernet | None:
    if not master:
        return None
    return Fernet(_derive_fernet_key(master))


def _master_secret() -> str:
    from .config import config_manager

    return str(config_manager.get("auth", "secret_key", fallback="") or "")


def encrypt(plaintext: str) -> str:
    if plaintext is None or plaintext == "":
        return ""
    if plaintext.startswith(PREFIX):
        return plaintext
    f = _fernet_for_master(_master_secret())
    if f is None:
        return plaintext
    token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt(stored: Any) -> str:
    if stored is None:
        return ""
    s = str(stored)
    if not s.startswith(PREFIX):
        return s
    token = s[len(PREFIX) :]
    f = _fernet_for_master(_master_secret())
    if f is None:
        if s.startswith(PREFIX):
            from .logging import logger

            logger.warning(
                "敏感配置项无法解密：[auth] secret_key 为空，请设置 secret_key 后重新保存 token"
            )
        return s
    try:
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        from .logging import logger

        logger.warning(
            "敏感配置密文解密失败（secret_key 是否与加密时一致？）。"
            "若 Bangumi 等 API 报 token 无效，请检查 secret_key 或在 Web 中重新填写 token"
        )
        return s


def encrypt_if_sensitive(section: str, option: str, value: str) -> str:
    if not is_sensitive_ini_field(section, option):
        return value
    return encrypt(value)


def decrypt_if_sensitive(section: str, option: str, value: Any) -> Any:
    if not is_sensitive_ini_field(section, option):
        return value
    if not isinstance(value, str):
        return value
    return decrypt(value)


def migrate_plaintext_sensitive_fields(config: ConfigParser) -> bool:
    """将仍为明文的敏感项加密。返回是否发生过修改。"""
    if not _master_secret():
        return False
    changed = False
    for section in config.sections():
        for option in config.options(section):
            if not is_sensitive_ini_field(section, option):
                continue
            raw = config.get(section, option, fallback="")
            if not raw or not str(raw).strip():
                continue
            if str(raw).startswith(PREFIX):
                continue
            config.set(section, option, encrypt(str(raw)))
            changed = True
    return changed


def decrypt_api_config_payload(data: dict[str, Any]) -> None:
    """就地解密 GET /api/config 等返回体中的敏感字段。"""
    for top_key, top_val in list(data.items()):
        if top_key == "multi_accounts" and isinstance(top_val, dict):
            for _name, acct in top_val.items():
                if isinstance(acct, dict) and "access_token" in acct:
                    v = acct.get("access_token")
                    if isinstance(v, str):
                        acct["access_token"] = decrypt(v)
            continue
        if not isinstance(top_val, dict):
            continue
        section = top_key.replace("_", "-")
        for key, val in list(top_val.items()):
            if isinstance(val, str) and is_sensitive_ini_field(section, key):
                top_val[key] = decrypt(val)
