"""
Bangumi 账号统一访问层（全列表化重构的核心）。

- 账号（含 OAuth 令牌）唯一真相源 = SQLite（``bangumi_accounts`` 表）。
- 列表长度 = 1 即单用户，无需 ``sync.mode`` 单/多判断。
- 启动时 ``migrate_ini_accounts_to_db`` 把旧 INI 账号段一次性迁移入库（幂等）。
- 本模块是对 ``DatabaseManager`` 账号仓库的薄封装，供 config/api/service 各层调用。
- 同时提供与原 ``config_manager.get_active_bangumi_config`` / ``get_bangumi_configs``
  / ``get_user_mappings`` 同语义的 DB 版本，便于上层逐点切换。
"""

from typing import Any, Optional

from .config import config_manager, parse_media_server_username_value
from .database import database_manager

_BANGUMI_SECTION = "bangumi"


def _to_str(value) -> str:
    """规范化配置值为字符串（兼容 get() 将纯数字转为 int）。"""
    if value is None:
        return ""
    return str(value).strip()


def _to_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value) -> bool:
    """规范化 private 字段为 bool（兼容 INI 字符串与 DB 0/1）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def _collect_ini_accounts() -> list[dict]:
    """收集旧 INI 中的账号（兼容单用户段 [bangumi] 与多用户段 [bangumi-*]）。"""
    accounts: list[dict] = []

    # 多用户段
    for section, cfg in config_manager.get_bangumi_configs().items():
        accounts.append(_cfg_to_account(section, cfg))

    # 单用户段 [bangumi]（仅当真实存在且有 username + access_token 时，
    # 与多用户段 get_bangumi_configs 的过滤条件对齐，避免迁入"半配置"幽灵账号）
    if config_manager.get_config_parser().has_section(_BANGUMI_SECTION):
        username = _to_str(
            config_manager.get(_BANGUMI_SECTION, "username", fallback="")
        )
        access_token = _to_str(
            config_manager.get(_BANGUMI_SECTION, "access_token", fallback="")
        )
        if (
            username
            and access_token
            and _BANGUMI_SECTION not in {a["section_name"] for a in accounts}
        ):
            raw = {
                "username": username,
                "media_server_username": config_manager.get(
                    _BANGUMI_SECTION, "media_server_username", fallback=[]
                ),
                "auth_method": config_manager.get(
                    _BANGUMI_SECTION, "auth_method", fallback="manual"
                ),
                "access_token": access_token,
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
                "private": config_manager.get(
                    _BANGUMI_SECTION, "private", fallback=False
                ),
            }
            accounts.append(_cfg_to_account(_BANGUMI_SECTION, raw))

    return accounts


def _normalize_media_server_usernames(raw) -> list[str]:
    """规范化 media_server_username（兼容 get() 将纯数字转为 int）。"""
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return parse_media_server_username_value(raw)


def _cfg_to_account(section_name: str, cfg: dict) -> dict:
    return {
        "section_name": section_name,
        "username": _to_str(cfg.get("username")),
        "media_server_usernames": _normalize_media_server_usernames(
            cfg.get("media_server_username")
        ),
        "auth_method": _to_str(cfg.get("auth_method")) or "manual",
        "access_token": _to_str(cfg.get("access_token")),
        "refresh_token": _to_str(cfg.get("refresh_token")),
        "token_type": _to_str(cfg.get("token_type")) or "Bearer",
        "expires_at": _to_int(cfg.get("expires_at")),
        "bangumi_user_id": _to_str(cfg.get("bangumi_user_id")),
        "nickname": _to_str(cfg.get("nickname")),
        "avatar": _to_str(cfg.get("avatar")),
        "private": _to_bool(cfg.get("private")),
        "is_active": False,
    }


def migrate_ini_accounts_to_db() -> int:
    """一次性把旧 INI 账号段迁移到 DB（幂等）。返回迁移的账号数。

    仅在 DB 中不存在对应 section 时写入，避免覆盖已通过 DB 管理的账号。
    迁移完成后清理 INI 中已入库的账号段，兑现"DB 为唯一真相源"。
    """
    ini_accounts = _collect_ini_accounts()
    migrated = 0
    for acc in ini_accounts:
        if database_manager.get_bangumi_account(acc["section_name"]):
            continue
        database_manager.save_bangumi_account(acc)
        migrated += 1

    # 若已有账号但无激活项，默认激活首个（旧单用户段或首个映射段）。
    # 注意：get_active_bangumi_account 在无 is_active=1 时会回退返回首个账号，
    # 因此不能用 `active is None` 判断，必须直接检查 is_active 标记，
    # 否则 set_active 永不执行，DB 中所有账号 is_active=0。
    if database_manager.count_bangumi_accounts() > 0:
        accounts = database_manager.list_bangumi_accounts()
        if not any(a.get("is_active") for a in accounts):
            database_manager.set_active_bangumi_account(accounts[0]["section_name"])

    # 清理已迁移到 DB 的 INI 账号段（仅清理 DB 中已存在的段，确认迁移成功）
    _cleanup_migrated_ini_sections(ini_accounts)
    return migrated


def _cleanup_migrated_ini_sections(ini_accounts: list[dict]) -> None:
    """清理 INI 中已迁移到 DB 的 bangumi 账号段。

    只清理 DB 中已存在对应账号的段（确认迁移成功），绝不清理系统功能段
    （如 bangumi-data）。清理后 INI 不再残留账号配置，避免双重真相源与
    加密 token 密文残留。

    注意：DB 检查必须在 config 锁外执行。仓储层 ``_row_to_account`` 解密
    token 时会调 ``_master_secret()`` → ``config_manager.get()`` 需获取
    ``ConfigManager._lock``，若在 config 锁内调 DB 会形成 AB-BA 死锁
    （config_lock → db_lock vs db_lock → config_lock）。
    """
    from .config_schema import non_account_bangumi_sections
    from .logging import logger

    non_account = non_account_bangumi_sections()

    # 1. 在 config 锁外检查 DB 中已存在的段（避免 AB-BA 死锁）
    sections_to_remove: list[str] = []
    for acc in ini_accounts:
        section = acc["section_name"]
        # 只清理 DB 中已存在的段（确认迁移成功，避免误删未迁移数据）
        if not database_manager.get_bangumi_account(section):
            continue
        # 只清理账号段：[bangumi] 单用户段 或 [bangumi-*] 非系统功能段
        if section == _BANGUMI_SECTION:
            pass
        elif section.startswith("bangumi-") and section not in non_account:
            pass
        else:
            continue
        sections_to_remove.append(section)

    if not sections_to_remove:
        return

    # 2. 在 config 锁内执行 INI 段删除（仅操作 ConfigParser，不调 DB）
    try:
        with config_manager._lock:
            config = config_manager._get_config_parser_nolock()
            removed: list[str] = []
            for section in sections_to_remove:
                if config.has_section(section):
                    config.remove_section(section)
                    removed.append(section)
            if removed:
                config_manager._save_config(config)
                logger.info("已清理 INI 中迁移完成的 bangumi 账号段: " + str(removed))
    except Exception as e:
        logger.warning(f"清理 INI bangumi 账号段失败（不影响 DB 迁移）: {e}")


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


# ── 与原 INI 方法同语义的 DB 版本（供上层切换）──────────────────
def _account_to_cfg(account: Optional[dict]) -> Optional[dict[str, Any]]:
    """把 DB 账号行转换为与 ``config_manager.get_bangumi_configs`` 同结构的 dict。

    返回 None 表示账号不存在；空账号（无 username 或 access_token）也返回 None，
    与原 INI ``get_bangumi_configs`` 中 ``if section_config.get('username') and
    section_config.get('access_token')`` 的过滤语义一致。
    """
    if not account:
        return None
    if not account.get("username") or not account.get("access_token"):
        return None
    return {
        "username": account.get("username", ""),
        "access_token": account.get("access_token", ""),
        "refresh_token": account.get("refresh_token", ""),
        "token_type": account.get("token_type", "Bearer"),
        "expires_at": account.get("expires_at"),
        "auth_method": account.get("auth_method", "manual"),
        "media_server_username": account.get("media_server_usernames", []),
        "bangumi_user_id": account.get("bangumi_user_id", ""),
        "nickname": account.get("nickname", ""),
        "avatar": account.get("avatar", ""),
        "private": _to_bool(account.get("private")),
    }


def list_bangumi_configs() -> dict[str, dict[str, Any]]:
    """列出全部账号配置（与 ``config_manager.get_bangumi_configs`` 同结构）。

    返回 ``{section_name: cfg_dict}``；过滤掉无 username/access_token 的账号，
    与原 INI 版本语义一致。
    """
    result: dict[str, dict[str, Any]] = {}
    for acc in database_manager.list_bangumi_accounts():
        cfg = _account_to_cfg(acc)
        if cfg is not None:
            result[acc["section_name"]] = cfg
    return result


def get_bangumi_config_by_section(
    section_name: str,
) -> Optional[dict[str, Any]]:
    """按 section_name 获取账号配置（与 ``list_bangumi_configs`` 同结构）。

    供需要精确指定账号的场景（如"我的追番"卡片切换账号）使用；
    账号不存在或无效返回 None。
    """
    return _account_to_cfg(database_manager.get_bangumi_account(section_name))


def get_user_mappings() -> dict[str, str]:
    """返回 ``media_server_username -> section_name`` 映射（与原 INI 版本同语义）。

    多个媒体服务器用户名映射到同一 Bangumi 段时，后者覆盖前者并记录警告。
    """
    mappings: dict[str, str] = {}
    for acc in database_manager.list_bangumi_accounts():
        section = acc.get("section_name")
        if not section:
            continue
        for name in acc.get("media_server_usernames") or []:
            prev = mappings.get(name)
            if prev is not None and prev != section:
                # 与原 INI 版本一致：重复时后者覆盖，记录警告
                from .logging import logger

                logger.warning(
                    "多用户映射中媒体服务器用户名 %r 重复：原指向配置段 %s，现被 %s 覆盖",
                    name,
                    prev,
                    section,
                )
            mappings[name] = section
    return mappings


def get_active_bangumi_config(
    user_name: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """按媒体服务器用户名返回对应 Bangumi 账号配置（与原 INI 版本同结构）。

    - ``user_name`` 为 None 或空字符串：返回当前激活账号配置；
      若无激活账号则取首个可用账号。
    - ``user_name`` 为具体值：按 ``media_server_usernames`` 反查对应账号；
      找不到或账号无效返回 None。

    返回的 dict 结构与 ``config_manager.get_active_bangumi_config`` 一致，
    包含 ``username/access_token/private`` 等字段，便于上层无缝替换。
    """
    # 无指定用户：取激活账号（兼容原 multi 模式下 user_name=None 取首个映射的语义）
    if not user_name:
        return _account_to_cfg(database_manager.get_active_bangumi_account())

    # 按媒体服务器用户名反查
    target_section = get_user_mappings().get(user_name)
    if not target_section:
        return None
    return _account_to_cfg(database_manager.get_bangumi_account(target_section))


def get_bangumi_config_for_user(user_name: str) -> Optional[dict[str, Any]]:
    """按媒体服务器用户名获取对应 Bangumi 账号配置。

    供 ``sync_service._get_bangumi_config_for_user`` 切换到 DB 时直接替换。

    多用户模式下空 user_name 不回退激活账号，避免数据串号（某条记录的
    user_name 异常为空时，回退激活账号会把该记录同步到他人账号）。
    单用户模式（账号数<=1）空 user_name 仍回退激活账号（只有一个账号，
    无串号风险）。
    """
    if not user_name:
        try:
            if database_manager.count_bangumi_accounts() > 1:
                from .logging import logger

                logger.warning(
                    "多用户模式下 user_name 为空，不回退激活账号以避免数据串号；"
                    "请检查上游是否正确传递媒体服务器用户名"
                )
                return None
        except Exception:
            pass
    return get_active_bangumi_config(user_name)


def get_single_mode_media_usernames() -> list[str]:
    """单用户模式下允许的媒体服务器用户名列表。

    DB 列表化后无 single/multi 之分：当 DB 中只有一个账号时，返回该账号的
    ``media_server_usernames``；多账号时返回空列表（调用方应走多用户分支）。
    与原 ``config_manager.get_single_mode_media_usernames`` 语义对齐。
    """
    accounts = database_manager.list_bangumi_accounts()
    if len(accounts) == 1:
        return list(accounts[0].get("media_server_usernames") or [])
    return []
