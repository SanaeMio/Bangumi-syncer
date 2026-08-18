"""
Trakt 邮箱登录服务（邮箱 + 验证码 → 自动获取 Bearer 凭证）

协议（firefox-reversed 逆向实测，见 docs/development/trakt-login-protocol.md）：
1. ``POST https://auth.trakt.tv/auth/magic``  body: ``email=<urlencoded>``
   → 向邮箱发送登录邮件（6 位 OTP，5 分钟有效，新邮件作废旧 OTP）
2. ``POST https://auth.trakt.tv/auth/magic/submit``  body: ``email=...&otp=<6位>``
   → 200 + ``Set-Cookie: __Secure-better-auth.session_token``（Better Auth 会话）
3. ``GET https://auth.trakt.tv/api/auth/oauth2/authorize``（带会话 cookie + PKCE S256）
   → 302 Location: ``https://app.trakt.tv/callback?code=...&state=...``（已登录直接发码，无确认页）
4. ``POST https://auth.trakt.tv/oauth/token``  ``{grant_type: authorization_code, code, code_verifier, ...}``
   → 200 ``{access_token, refresh_token, expires_in, expires_at, id_token}``

登录成功后自动落库（auth_type=bearer），并像 OAuth 授权一样把当前应用用户名
追加到激活 Bangumi 账号的 media_server_usernames，避免同步被用户名过滤拦截。

设计要点：
- OTP / session cookie / code / token 全程在服务端流转，前端只传 email 与 otp
- pending 登录会话存内存（按 user_id 隔离），5 分钟过期，单会话覆盖
- OTP 提交失败有次数上限（MAX_OTP_ATTEMPTS），超限作废会话，防止无限重试
- 发信限流：同一用户 60 秒内不能重复发（Trakt 有 rate limit），冷却期返回 429
- 并发安全：_pending 的读写由 asyncio.Lock 保护，避免会话被并发覆盖/误删
"""

import asyncio
import base64
import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, quote, urlparse

from ...core.database import database_manager
from ...core.logging import logger
from ...models.trakt import TraktConfig
from ...utils.http_base import AsyncHttpClient
from .client import TRAKT_API_KEY
from .token_refresher import (
    DEFAULT_EXPIRES_IN,
    USER_AGENT,
    _get_refresh_lock,
)

# 登录域
AUTH_BASE = "https://auth.trakt.tv"
# OIDC 授权回调（trakt 官方 web app 的固定回调，授权码从该地址的 query 中解析）
REDIRECT_URI = "https://app.trakt.tv/callback"
SCOPE = "public openid profile email offline_access"

# 验证码有效期（与 Trakt 邮件一致）
PENDING_TTL = 300
# 同用户发信冷却（秒），防 Trakt rate limit
RESEND_COOLDOWN = 60
# OTP 提交失败次数上限（防无限重试 / 暴力尝试），超限作废会话需重新发信
MAX_OTP_ATTEMPTS = 5

# 响应头中的会话 cookie 名（Better Auth）
SESSION_COOKIE_NAME = "__Secure-better-auth.session_token"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class _PendingLogin:
    """一次进行中的邮箱登录会话（PKCE 参数与邮箱，5 分钟过期）。"""

    email: str
    verifier: str
    state: str
    created_at: float
    attempts: int = 0  # OTP 提交失败次数（超 MAX_OTP_ATTEMPTS 作废）


# 按 user_id 隔离的进行中登录会话（内存态；重启丢失则重新发起即可）
_pending: dict[str, _PendingLogin] = {}
# 保护 _pending 读写的锁（start/complete 可并发触发）
_pending_lock = asyncio.Lock()


# ===== PKCE =====


def _generate_pkce() -> str:
    """生成 PKCE verifier（43 字符）。

    S256 challenge 在 _oidc_authorize 中由 verifier 现场计算，无需此处生成。
    """
    return secrets.token_urlsafe(32)


def _resolve_expires_at(data: dict) -> int:
    """从 /oauth/token 响应解析 expires_at（unix 秒），缺省用 expires_in 兜底。"""
    if data.get("expires_at"):
        return int(data["expires_at"])
    return int(time.time()) + int(data.get("expires_in", DEFAULT_EXPIRES_IN))


# ===== 协议步骤 =====


async def _send_magic(email: str) -> tuple[bool, str]:
    """① 发送登录邮件。返回 (ok, error)。"""
    http = (
        AsyncHttpClient(label="TraktLogin", timeout=30.0, follow_redirects=False)
        .prefix("📧")
        .success_tpl("验证码邮件已发送 [{status_code}]")
        .failure_tpl("发送验证码邮件失败: {error_type}")
    )
    try:
        resp = await http.request(
            "POST",
            f"{AUTH_BASE}/auth/magic",
            data=f"email={quote(email, safe='')}",
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "user-agent": USER_AGENT,
                "accept": "*/*",
                "origin": AUTH_BASE,
                "referer": f"{AUTH_BASE}/signin",
            },
        )
        if resp.status_code == 200:
            return True, ""
        return False, f"HTTP {resp.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    finally:
        await http.aclose()


async def _submit_otp(email: str, otp: str) -> tuple[bool, str, str]:
    """② 提交 OTP。返回 (ok, error, session_cookie)。"""
    http = (
        AsyncHttpClient(label="TraktLogin", timeout=30.0, follow_redirects=False)
        .prefix("🔑")
        .success_tpl("验证码提交成功 [{status_code}]")
        .failure_tpl("验证码提交失败: {error_type}")
    )
    try:
        resp = await http.request(
            "POST",
            f"{AUTH_BASE}/auth/magic/submit",
            data=f"email={quote(email, safe='')}&otp={otp}",
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "user-agent": USER_AGENT,
                "accept": "*/*",
                "origin": AUTH_BASE,
                "referer": f"{AUTH_BASE}/signin",
            },
        )
        if resp.status_code != 200:
            if resp.status_code == 401:
                return False, "验证码无效或已过期，请重新输入或重新发送", ""
            return False, f"HTTP {resp.status_code}", ""
        for set_cookie in resp.headers.get_list("set-cookie"):
            if set_cookie.startswith(SESSION_COOKIE_NAME + "="):
                return True, "", set_cookie.split(";")[0]
        return False, "登录响应异常：未收到会话 cookie", ""
    except Exception as e:  # noqa: BLE001
        return False, str(e), ""
    finally:
        await http.aclose()


async def _oidc_authorize(
    session_cookie: str, state: str, verifier: str
) -> tuple[str, str]:
    """③ OIDC 授权码流：带会话 cookie 发起 authorize，解析 302 回跳中的 code。

    Returns:
        (code, error)：code 为空表示失败。
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    params = {
        "response_type": "code",
        "client_id": TRAKT_API_KEY,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    http = (
        AsyncHttpClient(label="TraktLogin", timeout=30.0, follow_redirects=False)
        .prefix("🔐")
        .success_tpl("OIDC 授权 [{status_code}]")
        .failure_tpl("OIDC 授权失败: {error_type}")
    )
    try:
        resp = await http.request(
            "GET",
            f"{AUTH_BASE}/api/auth/oauth2/authorize",
            params=params,
            headers={
                "user-agent": USER_AGENT,
                "accept": "text/html,application/json",
                "cookie": session_cookie,
            },
        )
        # 1) 302/301 直接跳转：Location 带 code
        location = resp.headers.get("location", "")
        if resp.status_code in (301, 302, 303, 307, 308) and location:
            query = parse_qs(urlparse(location).query)
            state_back = query.get("state", [""])[0]
            code = query.get("code", [""])[0]
            # fail-closed：state 缺失或与发起时不匹配一律拒绝
            if state_back != state:
                return "", "授权状态校验失败，请重新发起登录"
            if code:
                return code, ""
        # 2) 200 + JSON：SvelteKit 服务端返回跳转目标
        #    {"redirect": true, "url": "https://app.trakt.tv/callback?code=...&state=..."}
        #    （实测 authorize 用这种方式下发授权码，非 HTTP 302）
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = None
        if isinstance(data, dict) and data.get("url"):
            query = parse_qs(urlparse(data["url"]).query)
            state_back = query.get("state", [""])[0]
            code = query.get("code", [""])[0]
            # fail-closed：state 缺失或与发起时不匹配一律拒绝
            if state_back != state:
                return "", "授权状态校验失败，请重新发起登录"
            if code:
                return code, ""
        return "", f"授权请求未返回授权码 (HTTP {resp.status_code})"
    except Exception as e:  # noqa: BLE001
        return "", f"授权请求异常: {e}"
    finally:
        await http.aclose()


async def _exchange_code(code: str, verifier: str) -> tuple[Optional[dict], str]:
    """④ 授权码换 token。返回 (tokens, error)。"""
    http = (
        AsyncHttpClient(label="TraktLogin", timeout=30.0)
        .prefix("🎟️")
        .success_tpl("令牌换取成功 [{status_code}]")
        .failure_tpl("令牌换取失败: {error_type}")
    )
    try:
        resp = await http.request(
            "POST",
            f"{AUTH_BASE}/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": TRAKT_API_KEY,
                "code_verifier": verifier,
            },
            headers={
                "content-type": "application/json",
                "user-agent": USER_AGENT,
            },
        )
        if resp.status_code == 200:
            return resp.json(), ""
        return None, f"HTTP {resp.status_code}: {(resp.text or '')[:200]}"
    except Exception as e:  # noqa: BLE001
        return None, str(e)
    finally:
        await http.aclose()


# ===== 落库与账号路由 =====


def _ensure_media_server_username(user_id: str) -> None:
    """登录成功后自动将当前用户名追加到激活账号的 media_server_usernames。

    与 OAuth 授权成功逻辑一致（DB 为唯一真相源）：避免 Trakt 同步
    被 _check_user_permission 的媒体服务器用户名过滤拦截。
    """
    from ...core.accounts import get_active_bangumi_account, save_bangumi_account

    acc = get_active_bangumi_account()
    if not acc:
        return
    existing = list(acc.get("media_server_usernames") or [])
    if user_id in existing:
        return
    existing.append(user_id)
    acc["media_server_usernames"] = existing
    save_bangumi_account(acc)
    logger.info(
        f"Trakt 邮箱登录成功：已自动将用户名 '{user_id}' 追加到 "
        f"激活 Bangumi 账号 '{acc.get('section_name', '')}' 的 "
        f"media_server_usernames，确保该用户的 Trakt 同步不被过滤拦截。"
    )


async def _persist_tokens(user_id: str, tokens: dict) -> Optional[int]:
    """Bearer 凭证落库（auth_type=bearer + access/refresh/expires_at）。

    与刷新共用 per-user 刷新锁：持锁后重读配置再写，避免与心跳/同步的
    并发旋转互相覆盖。

    Returns:
        expires_at；响应缺字段时返回 None（不落库，避免 KeyError 500）。
    """
    access = tokens.get("access_token") or ""
    refresh = tokens.get("refresh_token") or ""
    if not access or not refresh:
        logger.error(f"用户 {user_id} 邮箱登录：token 响应缺少 access/refresh，不落库")
        return None
    lock = await _get_refresh_lock(user_id)
    async with lock:
        config_dict = database_manager.get_trakt_config(user_id)
        config = (
            TraktConfig.from_dict(config_dict)
            if config_dict
            else TraktConfig(user_id=user_id)
        )
        config.auth_type = "bearer"
        config.access_token = access
        config.refresh_token = refresh  # 旋转式，落库新值
        config.expires_at = _resolve_expires_at(tokens)
        database_manager.save_trakt_config(config.to_dict())
        _ensure_media_server_username(user_id)
        logger.info(
            f"用户 {user_id} 通过邮箱登录完成，Bearer 凭证已保存，"
            f"有效期至 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(config.expires_at))}"
        )
        return config.expires_at


# ===== 对外接口 =====


async def start_email_login(user_id: str, email: str) -> dict:
    """开始邮箱登录：校验邮箱 → 发验证码 → 记录 PKCE pending 会话。

    Returns:
        {"success": bool, "message": str, "retry_after": Optional[int],
         "rate_limited": bool}
    """
    email = (email or "").strip().lower()
    if not _EMAIL_RE.fullmatch(email):
        return {"success": False, "message": "邮箱格式无效", "retry_after": None}

    now = time.time()
    # 冷却检查 + 发信 + 写入 pending 持同一把锁原子完成：并发 start 无法在
    # 冷却检查与真正发信之间插入第二次发信（避免绕过 60s 冷却 / 重复发信）。
    async with _pending_lock:
        prev = _pending.get(user_id)
        if prev and now - prev.created_at < RESEND_COOLDOWN:
            wait = int(RESEND_COOLDOWN - (now - prev.created_at))
            return {
                "success": False,
                "message": f"发送过于频繁，请 {wait} 秒后再试",
                "retry_after": wait,
                "rate_limited": True,
            }

        verifier = _generate_pkce()
        ok, err = await _send_magic(email)
        if not ok:
            return {
                "success": False,
                "message": f"发送验证码失败: {err}",
                "retry_after": None,
            }

        # 只有发信成功才建立 pending（verifier/state 与本次邮件一一对应）
        _pending[user_id] = _PendingLogin(
            email=email,
            verifier=verifier,
            state=secrets.token_hex(16),
            created_at=now,
        )
    return {
        "success": True,
        "message": f"验证码已发送至 {email}，请查收邮件（5 分钟内有效）",
        "retry_after": None,
    }


async def complete_email_login(user_id: str, otp: str) -> dict:
    """完成邮箱登录：提交 OTP → OIDC 授权码流 → 自动落库。

    Returns:
        {"success": bool, "message": str, "expires_at": Optional[int]}
    """
    otp = (otp or "").strip()
    async with _pending_lock:
        pending = _pending.get(user_id)
        if not pending or time.time() - pending.created_at > PENDING_TTL:
            _pending.pop(user_id, None)
            return {
                "success": False,
                "message": "登录会话已过期，请重新发送验证码",
                "expires_at": None,
            }
        if not (otp.isdigit() and len(otp) == 6):
            return {
                "success": False,
                "message": "请输入 6 位数字验证码",
                "expires_at": None,
            }
        # 网络调用前先检查并占用一次提交名额：并发提交在计数更新前无法
        # 全部打到上游，严格限制 OTP 尝试次数不超过 MAX_OTP_ATTEMPTS
        if pending.attempts >= MAX_OTP_ATTEMPTS:
            if _pending.get(user_id) is pending:
                _pending.pop(user_id, None)
            return {
                "success": False,
                "message": "验证码错误次数过多，请重新发送验证码",
                "expires_at": None,
            }
        pending.attempts += 1
        session = pending  # 持对象引用，后续不再依赖 dict 中的会话

    ok, err, session_cookie = await _submit_otp(session.email, otp)
    if not ok:
        # OTP 无效/失败：保留 pending 供重试（次数已在网络调用前占用）
        return {"success": False, "message": err, "expires_at": None}

    # OTP 已提交成功：本次会话已消费，无论后续成败都清理 pending
    async with _pending_lock:
        if _pending.get(user_id) is session:
            _pending.pop(user_id, None)
    try:
        code, err = await _oidc_authorize(
            session_cookie, session.state, session.verifier
        )
        if not code:
            return {"success": False, "message": err, "expires_at": None}
        tokens, err = await _exchange_code(code, session.verifier)
        if not tokens:
            return {"success": False, "message": err, "expires_at": None}
    except Exception as e:  # noqa: BLE001
        logger.error(f"用户 {user_id} 邮箱登录流程异常: {e}")
        return {"success": False, "message": f"登录流程异常: {e}", "expires_at": None}

    expires_at = await _persist_tokens(user_id, tokens)
    if expires_at is None:
        return {
            "success": False,
            "message": "登录响应缺少令牌字段，请重新发送验证码重试",
            "expires_at": None,
        }
    return {
        "success": True,
        "message": "登录成功，Bearer 凭证已保存并切换为 Bearer 模式",
        "expires_at": expires_at,
    }
