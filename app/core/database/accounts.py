"""
Bangumi 账号仓库（含 OAuth 令牌）。

将原本散落在 INI 各 ``[bangumi-*]`` 段的账号配置统一迁移到 SQLite，
以「账号列表」作为唯一真相源：列表长度=1 即单用户，无需单/多分支判断。

设计要点：
- token（access_token / refresh_token）暂按明文存储，与既有 ``trakt_config`` 表保持一致；
  根密钥外置（secret_key 移出 INI）作为独立安全项，后续可加加密层。
- ``section_name`` 为账号唯一键：旧单用户段为 ``bangumi``，多用户段为 ``bangumi-{username}``。
- ``is_active`` 取代「首个映射段即激活」的隐式逻辑，前端可切换激活账号。
- OAuth 授权过程中的 CSRF state 存于独立的 ``oauth_states`` 表（带 TTL）。
"""

import json
import time
from typing import Optional

from .base_repository import BaseRepository

_BANGUMI_ACCOUNT_COLUMNS = [
    "id",
    "section_name",
    "username",
    "media_server_usernames",
    "auth_method",
    "access_token",
    "refresh_token",
    "token_type",
    "expires_at",
    "bangumi_user_id",
    "nickname",
    "avatar",
    "private",
    "is_active",
    "created_at",
    "updated_at",
]


def _now() -> int:
    return int(time.time())


def _to_json_list(value) -> str:
    """规范化 media_server_usernames 为 JSON 字符串。"""
    if value is None:
        return "[]"
    if isinstance(value, str):
        # 兼容 INI 中可能的逗号分隔写法
        if "," in value:
            return json.dumps([v.strip() for v in value.split(",") if v.strip()])
        return json.dumps([value]) if value.strip() else "[]"
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value))
    return "[]"


def _from_json_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            data = json.loads(value)
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


class BangumiAccountRepository(BaseRepository):
    """Bangumi 账号（含 OAuth 令牌）的增删改查。"""

    def save_account(self, account: dict) -> bool:
        """保存或更新一个账号（按 section_name upsert）。"""
        section = account.get("section_name")
        if not section:
            return False

        def _write(conn):
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM bangumi_accounts WHERE section_name = ?", (section,)
            )
            existing = cursor.fetchone()
            now = _now()
            if existing:
                cursor.execute(
                    """
                    UPDATE bangumi_accounts SET
                        username = ?,
                        media_server_usernames = ?,
                        auth_method = ?,
                        access_token = ?,
                        refresh_token = ?,
                        token_type = ?,
                        expires_at = ?,
                        bangumi_user_id = ?,
                        nickname = ?,
                        avatar = ?,
                        private = ?,
                        is_active = ?,
                        updated_at = ?
                    WHERE section_name = ?
                    """,
                    (
                        account.get("username", ""),
                        _to_json_list(account.get("media_server_usernames")),
                        account.get("auth_method", "manual"),
                        account.get("access_token"),
                        account.get("refresh_token"),
                        account.get("token_type", "Bearer"),
                        account.get("expires_at"),
                        account.get("bangumi_user_id", ""),
                        account.get("nickname", ""),
                        account.get("avatar", ""),
                        1 if account.get("private") else 0,
                        1 if account.get("is_active") else 0,
                        now,
                        section,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO bangumi_accounts
                    (section_name, username, media_server_usernames, auth_method,
                     access_token, refresh_token, token_type, expires_at,
                     bangumi_user_id, nickname, avatar, private, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        section,
                        account.get("username", ""),
                        _to_json_list(account.get("media_server_usernames")),
                        account.get("auth_method", "manual"),
                        account.get("access_token"),
                        account.get("refresh_token"),
                        account.get("token_type", "Bearer"),
                        account.get("expires_at"),
                        account.get("bangumi_user_id", ""),
                        account.get("nickname", ""),
                        account.get("avatar", ""),
                        1 if account.get("private") else 0,
                        1 if account.get("is_active") else 0,
                        now,
                        now,
                    ),
                )
            return True

        return self._run_write(_write, error_msg="保存 Bangumi 账号失败", default=False)

    def get_account(self, section_name: str) -> Optional[dict]:
        """按 section_name 获取账号，不存在返回 None。"""

        def _read(conn):
            cursor = conn.cursor()
            cursor.execute(
                "SELECT {} FROM bangumi_accounts WHERE section_name = ?".format(
                    ", ".join(_BANGUMI_ACCOUNT_COLUMNS)
                ),
                (section_name,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return _row_to_account(row)

        return self._run_read(_read, error_msg="获取 Bangumi 账号失败", default=None)

    def list_accounts(self) -> list[dict]:
        """列出全部账号（按 id 升序）。"""

        def _read(conn):
            cursor = conn.cursor()
            cursor.execute(
                "SELECT {} FROM bangumi_accounts ORDER BY id ASC".format(
                    ", ".join(_BANGUMI_ACCOUNT_COLUMNS)
                )
            )
            return [_row_to_account(r) for r in cursor.fetchall()]

        return self._run_read(_read, error_msg="列出 Bangumi 账号失败", default=[])

    def get_active_account(self) -> Optional[dict]:
        """获取当前激活账号（is_active=1）；无激活时返回首个。"""

        def _read(conn):
            cursor = conn.cursor()
            cursor.execute(
                "SELECT {} FROM bangumi_accounts WHERE is_active = 1 "
                "ORDER BY id ASC LIMIT 1".format(", ".join(_BANGUMI_ACCOUNT_COLUMNS))
            )
            row = cursor.fetchone()
            if row:
                return _row_to_account(row)
            cursor.execute(
                "SELECT {} FROM bangumi_accounts ORDER BY id ASC LIMIT 1".format(
                    ", ".join(_BANGUMI_ACCOUNT_COLUMNS)
                )
            )
            row = cursor.fetchone()
            return _row_to_account(row) if row else None

        return self._run_read(
            _read, error_msg="获取激活 Bangumi 账号失败", default=None
        )

    def delete_account(self, section_name: str) -> bool:
        """删除账号，返回是否影响到行。"""

        def _write(conn):
            cursor = conn.execute(
                "DELETE FROM bangumi_accounts WHERE section_name = ?", (section_name,)
            )
            return cursor.rowcount

        affected = self._run_write(_write, error_msg="删除 Bangumi 账号失败", default=0)
        return affected > 0

    def set_active(self, section_name: str) -> bool:
        """将指定账号设为激活，其余置非激活。"""

        def _write(conn):
            cursor = conn.cursor()
            cursor.execute("UPDATE bangumi_accounts SET is_active = 0")
            cursor.execute(
                "UPDATE bangumi_accounts SET is_active = 1 WHERE section_name = ?",
                (section_name,),
            )
            return True

        return self._run_write(_write, error_msg="设置激活账号失败", default=False)

    def update_token(self, section_name: str, token: dict) -> bool:
        """仅更新令牌相关字段（OAuth 授权/刷新后回写）。"""

        def _write(conn):
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM bangumi_accounts WHERE section_name = ?",
                (section_name,),
            )
            if not cursor.fetchone():
                return False
            cursor.execute(
                """
                UPDATE bangumi_accounts SET
                    access_token = ?,
                    refresh_token = ?,
                    token_type = ?,
                    expires_at = ?,
                    auth_method = 'oauth',
                    bangumi_user_id = ?,
                    nickname = ?,
                    avatar = ?,
                    updated_at = ?
                WHERE section_name = ?
                """,
                (
                    token.get("access_token"),
                    token.get("refresh_token"),
                    token.get("token_type", "Bearer"),
                    token.get("expires_at"),
                    token.get("bangumi_user_id", ""),
                    token.get("nickname", ""),
                    token.get("avatar", ""),
                    _now(),
                    section_name,
                ),
            )
            return True

        return self._run_write(_write, error_msg="更新 Bangumi 令牌失败", default=False)

    def count_accounts(self) -> int:
        """账号数量。"""

        def _read(conn):
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM bangumi_accounts")
            return cursor.fetchone()[0]

        return self._run_read(_read, error_msg="统计账号失败", default=0)


def _row_to_account(row) -> dict:
    account = dict(zip(_BANGUMI_ACCOUNT_COLUMNS, row))
    account["media_server_usernames"] = _from_json_list(
        account.get("media_server_usernames")
    )
    account["private"] = bool(account.get("private"))
    account["is_active"] = bool(account.get("is_active"))
    return account


class OAuthStateRepository(BaseRepository):
    """OAuth 授权过程中的 CSRF state（临时会话，带 TTL）。"""

    def save_state(
        self, state: str, section_name: str, expires_at: int, provider: str = ""
    ) -> bool:
        """保存一个授权 state（provider 用于区分不同 OAuth 来源）。"""

        def _write(conn):
            conn.execute(
                """
                INSERT OR REPLACE INTO oauth_states
                (state, section_name, provider, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (state, section_name, provider, _now(), expires_at),
            )
            return True

        return self._run_write(_write, error_msg="保存 OAuth state 失败", default=False)

    def get_state(self, state: str) -> Optional[dict]:
        """获取并校验 state；过期或不存在返回 None（同时清理过期项）。"""

        def _read(conn):
            cursor = conn.cursor()
            cursor.execute(
                "SELECT state, section_name, provider, created_at, expires_at "
                "FROM oauth_states WHERE state = ?",
                (state,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            record = {
                "state": row[0],
                "section_name": row[1],
                "provider": row[2],
                "created_at": row[3],
                "expires_at": row[4],
            }
            if record["expires_at"] and _now() >= record["expires_at"]:
                cursor.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
                return None
            return record

        return self._run_read(_read, error_msg="读取 OAuth state 失败", default=None)

    def delete_state(self, state: str) -> bool:
        """删除 state（授权完成后调用）。"""

        def _write(conn):
            cursor = conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            return cursor.rowcount

        affected = self._run_write(_write, error_msg="删除 OAuth state 失败", default=0)
        return affected > 0

    def cleanup_expired(self) -> int:
        """清理所有过期 state，返回删除行数。"""

        def _write(conn):
            cursor = conn.execute(
                "DELETE FROM oauth_states WHERE expires_at IS NOT NULL "
                "AND expires_at <= ?",
                (_now(),),
            )
            return cursor.rowcount

        return self._run_write(_write, error_msg="清理过期 OAuth state 失败", default=0)
