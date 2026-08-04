"""
Bangumi 账号统一访问层（全列表化重构的核心）。

- 账号（含 OAuth 令牌）唯一真相源 = SQLite（``bangumi_accounts`` 表）。
- 列表长度 = 1 即单用户，无需 ``sync.mode`` 单/多判断。
- 启动时 ``migrate_ini_accounts_to_db`` 把旧 INI 账号段一次性迁移入库（幂等）。
- 本模块是对 ``DatabaseManager`` 账号仓库的薄封装，供 config/api/service 各层调用。
"""

from typing import Optional

from .config import config_manager
from .database import database_manager

_BANGUMI_SECTION = "bangumi"


def _to_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _collect_ini_accounts() -> list[dict]:
    """收集旧 INI 中的账号（兼容单用户段 [bangumi] 与多用户段 [bangumi-*]）。"""
    accounts: list[dict] = []

    # 多用户段
    for section, cfg in config_manager.get_bangumi_configs().items():
        accounts.append(_cfg_to_account(section, cfg))

    # 单用户段 [bangumi]（仅当真实存在且有 username 时）
    if config_manager.get_config_parser().has_section(_BANGUMI_SECTION):
        username = config_manager.get(_BANGUMI_SECTION, "username", fallback="") or ""
        if username.strip() and _BANGUMI_SECTION not in {
            a["section_name"] for a in accounts
        }:
            raw = {
                "username": username,
                "media_server_username": config_manager.get(
                    _BANGUMI_SECTION, "media_server_username", fallback=[]
                ),
                "auth_method": config_manager.get(
                    _BANGUMI_SECTION, "auth_method", fallback="manual"
                ),
                "access_token": config_manager.get(
                    _BANGUMI_SECTION, "access_token", fallback=""
                ),
                "refresh_token": config_manager.get(
                    _BANGUMI_SECTION, "refresh_token", fallback=""
                ),
                "token_type": config_manager.get(
                    _BANGUMI_SECTION, "token_type", fallback="Bearer"
                ),
                "expires_at": config_manager.get(
                    _BANGUMI_SECTION, "expires_at", fallback=""
                ),
                "bangumi_user_id": config_manager.get(
                    _BANGUMI_SECTION, "bangumi_user_id", fallback=""
                ),
                "nickname": config_manager.get(
                    _BANGUMI_SECTION, "nickname", fallback=""
                ),
                "avatar": config_manager.get(_BANGUMI_SECTION, "avatar", fallback=""),
            }
            accounts.append(_cfg_to_account(_BANGUMI_SECTION, raw))

    return accounts


def _cfg_to_account(section_name: str, cfg: dict) -> dict:
    return {
        "section_name": section_name,
        "username": (cfg.get("username") or "").strip(),
        "media_server_usernames": cfg.get("media_server_username") or [],
        "auth_method": cfg.get("auth_method") or "manual",
        "access_token": cfg.get("access_token") or "",
        "refresh_token": cfg.get("refresh_token") or "",
        "token_type": cfg.get("token_type") or "Bearer",
        "expires_at": _to_int(cfg.get("expires_at")),
        "bangumi_user_id": cfg.get("bangumi_user_id") or "",
        "nickname": cfg.get("nickname") or "",
        "avatar": cfg.get("avatar") or "",
        "is_active": False,
    }


def migrate_ini_accounts_to_db() -> int:
    """一次性把旧 INI 账号段迁移到 DB（幂等）。返回迁移的账号数。

    仅在 DB 中不存在对应 section 时写入，避免覆盖已通过 DB 管理的账号。
    """
    migrated = 0
    for acc in _collect_ini_accounts():
        if database_manager.get_bangumi_account(acc["section_name"]):
            continue
        database_manager.save_bangumi_account(acc)
        migrated += 1

    # 若已有账号但无激活项，默认激活首个（旧单用户段或首个映射段）
    if database_manager.count_bangumi_accounts() > 0:
        active = database_manager.get_active_bangumi_account()
        if active is None:
            first = database_manager.list_bangumi_accounts()[0]
            database_manager.set_active_bangumi_account(first["section_name"])
    return migrated


# ── 统一访问层（DB 为唯一真相源）───────────────────────────────
def list_bangumi_accounts() -> list[dict]:
    return database_manager.list_bangumi_accounts()


def get_bangumi_account(section_name: str) -> Optional[dict]:
    return database_manager.get_bangumi_account(section_name)


def get_active_bangumi_account() -> Optional[dict]:
    return database_manager.get_active_bangumi_account()


def save_bangumi_account(account: dict) -> bool:
    return database_manager.save_bangumi_account(account)


def delete_bangumi_account(section_name: str) -> bool:
    return database_manager.delete_bangumi_account(section_name)


def set_active_bangumi_account(section_name: str) -> bool:
    return database_manager.set_active_bangumi_account(section_name)


def update_bangumi_account_token(section_name: str, token: dict) -> bool:
    return database_manager.update_bangumi_account_token(section_name, token)


def count_bangumi_accounts() -> int:
    return database_manager.count_bangumi_accounts()
