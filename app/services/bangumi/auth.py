"""Bangumi OAuth 认证服务（委托通用 OAuth 抽象层实现）。

授权 URL 构建、令牌交换/刷新、CSRF state 管理统一由
``app.services.oauth`` 的通用 ``OAuthService`` 处理；本模块仅负责
Bangumi 特有的令牌落地（写入数据库 ``bangumi_accounts`` 表）与连接状态查询。
"""

import os
import threading
import time
from typing import Optional

from app.core.accounts import (
    get_active_bangumi_account,
    get_bangumi_account,
    list_bangumi_accounts,
    save_bangumi_account,
    set_active_bangumi_account,
)
from app.core.config import config_manager
from app.core.public_url import get_public_base_path
from app.services.oauth import get_oauth_service, get_provider

# 应用级 OAuth 凭证（留空时由内置默认值与环境变量注入，详见配置文档）
DEFAULT_OAUTH_CLIENT_ID = os.environ.get("BANGUMI_OAUTH_CLIENT_ID", "")
DEFAULT_OAUTH_CLIENT_SECRET = os.environ.get("BANGUMI_OAUTH_CLIENT_SECRET", "")

# 本地回退地址（当未显式配置 redirect_uri 时使用）
DEFAULT_LOCAL_BASE = "http://localhost:8000"
# 统一的 OAuth 回调路径
OAUTH_CALLBACK_PATH = "/api/oauth/bangumi/callback"


def get_app_credentials() -> tuple[str, str]:
    """返回 (client_id, client_secret)。

    解析优先级：配置文件 ``[bangumi-oauth]`` -> 环境变量注入的内置默认值。
    """
    client_id = (
        config_manager.get("bangumi-oauth", "client_id", fallback="") or ""
    ).strip()
    client_secret = (
        config_manager.get("bangumi-oauth", "client_secret", fallback="") or ""
    ).strip()
    if not client_id:
        client_id = DEFAULT_OAUTH_CLIENT_ID
    if not client_secret:
        client_secret = DEFAULT_OAUTH_CLIENT_SECRET
    return client_id, client_secret


def get_redirect_uri() -> str:
    """返回 OAuth 回调地址。

    优先使用配置 ``[bangumi-oauth] redirect_uri``；未配置时基于公开基址推导。
    """
    configured = (
        config_manager.get("bangumi-oauth", "redirect_uri", fallback="") or ""
    ).strip()
    if configured:
        return configured
    base = (get_public_base_path() or "").strip().rstrip("/")
    if not base:
        base = DEFAULT_LOCAL_BASE
    return f"{base}{OAUTH_CALLBACK_PATH}"


class BangumiAuthService:
    """Bangumi OAuth 服务。"""

    def __init__(self) -> None:
        self.oauth = get_oauth_service()
        # per-section 刷新锁：避免并发请求重复刷新同一账号 token
        self._refresh_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _get_refresh_lock(self, section: str) -> threading.Lock:
        """获取指定账号的刷新锁（per-section，避免不同账号互相阻塞）。"""
        with self._locks_guard:
            lock = self._refresh_locks.get(section)
            if lock is None:
                lock = threading.Lock()
                self._refresh_locks[section] = lock
            return lock

    @staticmethod
    def _is_expiring_soon(acc: dict) -> bool:
        """判断账号 token 是否临近过期（提前 1 小时阈值）。"""
        expires_at = acc.get("expires_at")
        if not expires_at:
            return False
        try:
            expires_at_ts = int(expires_at)
        except (TypeError, ValueError):
            return False
        return expires_at_ts - int(time.time()) <= 3600

    def _do_refresh(self, section: str, acc: dict) -> bool:
        """实际执行 token 刷新（不含锁，由调用方持锁后调用）。"""
        refresh_token = (acc.get("refresh_token") or "").strip()
        if not refresh_token:
            return False
        try:
            token = self.oauth.refresh_token("bangumi", refresh_token)
        except Exception as e:
            # 刷新失败（如 refresh_token 已失效），交由上层提示重新授权；
            # 记录日志避免异常被静默吞没导致排障困难
            from app.core.logging import logger

            logger.warning(
                f"刷新账号 '{section}' 的 Bangumi token 失败: {e}，可能需要重新授权"
            )
            return False
        self._persist_token(token, section)
        return True

    def get_app_credentials(self) -> tuple[str, str]:
        return get_app_credentials()

    def get_redirect_uri(self) -> str:
        return get_redirect_uri()

    def get_auth_url(self) -> tuple[str, str]:
        """生成授权 URL 与一次性 state。

        返回 ``(auth_url, state)``；未配置 client_id 时抛出异常。
        """
        client_id, _ = self.get_app_credentials()
        if not client_id:
            raise ValueError(
                "尚未配置 Bangumi OAuth 应用的 client_id。请在设置中填写，"
                "或通过环境变量 BANGUMI_OAUTH_CLIENT_ID 注入。"
            )
        account_key = self._active_section_name() or "bangumi"
        state = self.oauth.create_state("bangumi", account_key)
        provider = get_provider("bangumi")
        return self.oauth.build_authorize_url(provider, state=state), state

    def verify_state(self, state: str) -> bool:
        """校验 state 是否有效（校验后消费，不可重复使用）。"""
        return self.oauth.consume_state("bangumi", state) is not None

    def exchange_code_for_token(self, code: str, state: str) -> dict:
        """用授权码换取访问令牌并落地。state 无效时抛出异常。"""
        if self.oauth.consume_state("bangumi", state) is None:
            raise ValueError("OAuth state 校验失败，请重新发起授权")
        token = self.oauth.exchange_code("bangumi", code)
        self._persist_token(token)
        return token

    def refresh_active_token(self, section: Optional[str] = None) -> bool:
        """刷新当前激活账号的访问令牌；成功返回 True。

        使用 per-section 锁确保同一账号同一时刻只有一个刷新操作，
        避免并发请求重复调用 OAuth refresh 接口（可能触发限流）或
        后刷新的覆盖先刷新的导致 token 失效。
        """
        section = section or self._active_section_name()
        if not section:
            return False
        lock = self._get_refresh_lock(section)
        with lock:
            acc = get_bangumi_account(section)
            if not acc:
                return False
            return self._do_refresh(section, acc)

    def refresh_active_token_if_needed(self, section: Optional[str] = None) -> bool:
        """按需刷新：仅当采用 OAuth 且临近/已经过期时才刷新。

        持锁后 double-check 过期时间，避免并发场景下多个线程同时触发刷新
        重复调用 OAuth 接口（可能触发限流）或后刷新的使先刷新的 token 失效。
        """
        section = section or self._active_section_name()
        if not section:
            return False
        acc = get_bangumi_account(section)
        if not acc:
            return False
        if (acc.get("auth_method") or "manual") != "oauth":
            return False
        if not self._is_expiring_soon(acc):
            return False
        # 临近过期：加锁后 double-check，避免并发重复刷新
        lock = self._get_refresh_lock(section)
        with lock:
            acc = get_bangumi_account(section)
            if not acc or (acc.get("auth_method") or "manual") != "oauth":
                return False
            if not self._is_expiring_soon(acc):
                # 已被并发线程刷新，无需重复
                return False
            return self._do_refresh(section, acc)

    # ── 令牌落地与状态查询（Bangumi 特有，DB 为唯一真相源）────
    def _persist_token(self, token: dict, section: Optional[str] = None) -> None:
        """将令牌写入数据库（upsert 账号）。

        定位策略（对齐 Trakt 的"授权后添加账号"语义）：

        - ``section`` 显式传入（刷新场景）：更新指定 section 的 token
        - ``section`` 为 None（授权场景）：按 token 中的 ``user_id`` 查找已存在账号，
          找到则更新（保留 media_server_usernames 等用户配置），未找到则新建账号
          到列表，section_name 取 ``bangumi-{user_id}``。

        不强制激活新建账号，避免覆盖用户当前选择；但若账号列表为空（首次授权），
        自动激活新建账号以保持开箱即用体验。
        """
        access_token = token.get("access_token")
        refresh_token = token.get("refresh_token")
        expires_in = int(token.get("expires_in", 0) or 0)
        expires_at = int(time.time()) + expires_in if expires_in else 0

        # Bangumi OAuth 令牌响应直接携带 user_id / username / nickname
        user_id = token.get("user_id")

        # 处理 avatar（Bangumi OAuth 响应为 dict，回写时取 large/medium）
        avatar = token.get("avatar")
        avatar_url = ""
        if isinstance(avatar, dict):
            avatar_url = avatar.get("large") or avatar.get("medium") or ""
        elif isinstance(avatar, str):
            avatar_url = avatar

        # ── 定位目标账号 ──
        existing: dict = {}
        if section:
            # 显式指定 section（刷新场景）
            existing = get_bangumi_account(section) or {}
        elif user_id is not None:
            # 授权场景：按 user_id 查找已存在账号，避免覆盖其他账号
            user_id_str = str(user_id)
            for acc in list_bangumi_accounts():
                if str(acc.get("bangumi_user_id") or "") == user_id_str:
                    existing = acc
                    section = acc["section_name"]
                    break
            if not existing:
                # 新账号：section_name 用 bangumi-{user_id}，避免与系统功能段冲突
                section = f"bangumi-{user_id_str}"
        else:
            # 无 user_id 也无 section：回退到激活账号（兼容边界场景）
            section = self._active_section_name() or "bangumi"
            existing = get_bangumi_account(section) or {}

        # 补全 avatar：新建账号时 existing 为空
        if not avatar_url:
            avatar_url = existing.get("avatar", "")

        username = (
            token.get("username")
            or (str(user_id) if user_id is not None else "")
            or existing.get("username", "")
        )

        # 新建账号自动激活（用户主动授权说明想用此账号，对齐 Trakt 行为）；
        # 更新已存在账号保留原激活状态
        is_new_account = not existing
        was_active = bool(existing.get("is_active") or False)

        account = {
            "section_name": section,
            "username": username,
            "media_server_usernames": existing.get("media_server_usernames") or [],
            "auth_method": "oauth",
            "access_token": access_token or "",
            "refresh_token": refresh_token or "",
            "token_type": token.get("token_type") or "Bearer",
            "expires_at": expires_at or None,
            "bangumi_user_id": (
                str(user_id)
                if user_id is not None
                else existing.get("bangumi_user_id", "")
            ),
            "nickname": token.get("nickname") or existing.get("nickname") or "",
            "avatar": avatar_url,
            "private": bool(existing.get("private") or False),
            "is_active": is_new_account or was_active,
        }
        save_bangumi_account(account)
        if is_new_account:
            set_active_bangumi_account(section)

    def _active_section_name(self) -> Optional[str]:
        """返回当前激活 Bangumi 账号配置段名（DB 为唯一真相源）。"""
        acc = get_active_bangumi_account()
        return acc.get("section_name") if acc else None

    def get_connection_status(self) -> dict:
        """返回当前 Bangumi 连接状态（供前端展示，从 DB 读取）。"""
        acc = get_active_bangumi_account()
        if not acc:
            return {
                "connected": False,
                "auth_method": "manual",
                "username": "",
                "user_id": "",
                "nickname": "",
                "avatar": "",
                "oauth_url": "",
                "callback_path": OAUTH_CALLBACK_PATH,
                "has_token": False,
            }
        access_token = (acc.get("access_token") or "").strip()
        expires_at = acc.get("expires_at")
        expired = bool(expires_at) and int(time.time()) >= int(expires_at)
        return {
            "connected": bool(access_token),
            "auth_method": (acc.get("auth_method") or "manual").strip(),
            "username": (acc.get("username") or "").strip(),
            "user_id": (acc.get("bangumi_user_id") or "").strip(),
            "nickname": (acc.get("nickname") or "").strip(),
            "avatar": (acc.get("avatar") or "").strip(),
            "oauth_url": "",
            "callback_path": OAUTH_CALLBACK_PATH,
            "has_token": bool(access_token),
            "expired": expired,
        }

    def disconnect(self) -> None:
        """断开当前账号：回退到手动模式，清除 OAuth 刷新令牌（保留手动填写的访问令牌）。"""
        section = self._active_section_name()
        if not section:
            return
        acc = get_bangumi_account(section)
        if not acc:
            return
        acc["auth_method"] = "manual"
        acc["refresh_token"] = ""
        acc["expires_at"] = None
        save_bangumi_account(acc)


# 模块级单例（供 API 路由使用）
bangumi_auth_service = BangumiAuthService()
