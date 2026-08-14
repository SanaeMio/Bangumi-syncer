"""
Trakt Bearer 凭证续期服务（OAuth2 Bearer · apiz.trakt.tv 数据域）

认证链（trakt.tv 已实测验证，见 firefox-reversed 逆向资料）：
- 数据请求认证 = ``Authorization: Bearer <access_token>``（opaque 32 字符，7 天）
- 刷新 = ``POST https://auth.trakt.tv/oauth/token``
  body: ``{"grant_type": "refresh_token", "refresh_token": ..., "client_id": 201dc70c...}``
- refresh_token 为旋转式：每次刷新旧值立即作废，必须持久化新值
- 401 表现 = ``WWW-Authenticate: Bearer error="invalid_token"``，刷新后重试即恢复

本服务职责：
1. 保存凭证时立即验证刷新（validate_and_save_bearer）
2. 由 TraktScheduler 的每日心跳驱动，对「bearer 模式且启用」的用户按
   expires_at 智能续期（剩余 <1 天才真正调 /oauth/token）；同步层 401
   路径以 force=True 强制刷新（本地 expires_at 仍有效但服务端已 401）
3. 刷新失败（invalid_grant）判定失效并通知

并发安全：refresh_token 为旋转式（旧值作废），心跳/定时同步/手动同步/
保存凭证可并行触发刷新。所有刷新与保存凭证入口按 user_id 持 per-user
asyncio.Lock，持锁后重读配置并 double-check，避免两个协程各自读到旧
refresh 后先后落库把对方新换的 token 盖掉。
"""

import asyncio
import time
from typing import Optional

from ...core.database import database_manager
from ...core.logging import logger
from ...models.trakt import TraktConfig
from ...utils.http_base import AsyncHttpClient
from ..base.notifier_helpers import notify_scheduler_failure
from .client import TRAKT_API_KEY, TRAKT_OAUTH_TOKEN_URL

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0"
)
# 剩余 <1 天才触发刷新（access 有效期 7 天）
REFRESH_SLACK = 86400
# access_token 默认有效期（秒），响应缺 expires_at 时兜底
DEFAULT_EXPIRES_IN = 604800

# 心跳结果状态
STATUS_OK = "ok"  # 刷新成功（旋转式换新并落库）
STATUS_EXPIRED = "expired"  # invalid_grant：refresh 失效，需重新填写
STATUS_FAILED = "failed"  # 网络/服务端错误，非失效，下次心跳重试
STATUS_SKIPPED = "skipped"  # 无需刷新 / 非 bearer 模式 / 无 refresh_token

# per-user 刷新锁：防止并发（心跳 + 定时/手动同步 + 保存凭证）同时读到
# 旧 refresh_token，各自旋转后互相覆盖。与 TraktAuthService（oauth 路径）
# 的 _refresh_locks 设计一致。
_refresh_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


async def _get_refresh_lock(user_id: str) -> asyncio.Lock:
    """获取指定用户的刷新锁（per-user，避免不同用户互相阻塞）。"""
    async with _locks_guard:
        lock = _refresh_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _refresh_locks[user_id] = lock
        return lock


async def _call_oauth_token(
    refresh_token: str,
) -> tuple[str, Optional[dict], Optional[str]]:
    """调用 /oauth/token 刷新。

    Returns:
        (status, data, error_msg)：status 为 "ok" / "invalid_grant" / "error"
    """
    http = (
        AsyncHttpClient(label="TraktToken", timeout=30.0)
        .prefix("🔑")
        .success_tpl("TraktToken refresh [{status_code}]")
        .failure_tpl("TraktToken refresh 失败: {error_type}")
    )
    try:
        resp = await http.request(
            "POST",
            TRAKT_OAUTH_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": TRAKT_API_KEY,
            },
            headers={
                "content-type": "application/json",
                "user-agent": USER_AGENT,
            },
        )
        if resp.status_code == 200:
            return "ok", resp.json(), None
        body = resp.text or ""
        if resp.status_code == 400 and "invalid_grant" in body:
            return "invalid_grant", None, body[:200]
        return "error", None, f"HTTP {resp.status_code}: {body[:200]}"
    except Exception as e:  # noqa: BLE001
        return "error", None, str(e)
    finally:
        await http.aclose()


def _resolve_expires_at(data: dict) -> int:
    """从 /oauth/token 响应解析 expires_at（unix 秒），缺省用 expires_in 兜底。"""
    if data.get("expires_at"):
        return int(data["expires_at"])
    return int(time.time()) + int(data.get("expires_in", DEFAULT_EXPIRES_IN))


def _extract_new_tokens(data: dict) -> Optional[tuple[str, str]]:
    """从 /oauth/token 响应提取 (access_token, refresh_token)。

    缺字段时返回 None（响应异常，避免 KeyError 500）。
    """
    access = data.get("access_token") or ""
    refresh = data.get("refresh_token") or ""
    if not access or not refresh:
        return None
    return access, refresh


async def validate_and_save_bearer(user_id: str, refresh_token: str) -> dict:
    """保存 Bearer 凭证：立即调 /oauth/token 验证并刷新。

    - 200：凭证有效，旋转式换新 access/refresh/expires_at 落库（auth_type=bearer）
    - invalid_grant：凭证无效/已过期，不落库
    - 其他错误：不落库，返回错误

    Args:
        user_id: 用户名。
        refresh_token: 用户从浏览器粘贴的 refresh_token（access_token 无需
            单独校验：/oauth/token 刷新成功即证明凭证对有效，且会旋转换新）。

    Returns:
        {"success": bool, "message": str, "expires_at": Optional[int]}
    """
    lock = await _get_refresh_lock(user_id)
    async with lock:
        status, data, err = await _call_oauth_token(refresh_token)

        if status == "invalid_grant":
            return {
                "success": False,
                "message": "凭证无效或已过期（invalid_grant），请重新从浏览器复制",
                "expires_at": None,
            }
        if status != "ok":
            return {
                "success": False,
                "message": f"凭证验证失败: {err}",
                "expires_at": None,
            }

        new_tokens = _extract_new_tokens(data)
        if new_tokens is None:
            return {
                "success": False,
                "message": "凭证验证失败: 刷新响应缺少 access_token / refresh_token",
                "expires_at": None,
            }
        access, refresh = new_tokens

        config_dict = database_manager.get_trakt_config(user_id)
        config = (
            TraktConfig.from_dict(config_dict)
            if config_dict
            else TraktConfig(user_id=user_id)
        )
        config.auth_type = "bearer"
        config.access_token = access
        config.refresh_token = refresh  # 旋转式，落库新值
        config.expires_at = _resolve_expires_at(data)
        database_manager.save_trakt_config(config.to_dict())
        logger.info(
            f"用户 {user_id} 的 Trakt Bearer 凭证已验证并保存，"
            f"有效期至 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(config.expires_at))}"
        )
        return {
            "success": True,
            "message": "凭证有效，已保存",
            "expires_at": config.expires_at,
        }


async def refresh_user_bearer(user_id: str, force: bool = False) -> str:
    """对单个用户执行一次 Bearer 续期检查，并更新存储。

    Args:
        user_id: 用户名。
        force: 为 True 时忽略 expires_at 的 slack 判断，强制调 /oauth/token。
            同步层 401 路径必须 force=True：本地 expires_at 可能仍认为有效
            （提前吊销 / 时钟差 / 粘贴了已旋转过的 token），此时按 slack
            跳过会让「认证失败」被误判为「刷新失败」。心跳可继续用 slack。

    Returns:
        STATUS_OK / STATUS_EXPIRED / STATUS_FAILED / STATUS_SKIPPED
    """
    lock = await _get_refresh_lock(user_id)
    async with lock:
        # 持锁后重读配置，可能已被并发协程刷新
        config_dict = database_manager.get_trakt_config(user_id)
        if not config_dict:
            return STATUS_SKIPPED
        config = TraktConfig.from_dict(config_dict)
        if not config or config.auth_type != "bearer" or not config.refresh_token:
            return STATUS_SKIPPED

        # 按 expires_at 智能触发：剩余 >1 天不刷新（避免无效请求）
        # force=True 时跳过该判断（同步层 401 必须真正调 /oauth/token）
        if (
            not force
            and config.expires_at
            and time.time() < config.expires_at - REFRESH_SLACK
        ):
            return STATUS_SKIPPED

        status, data, err = await _call_oauth_token(config.refresh_token)

        if status == "invalid_grant":
            # refresh 失效：标记失效（expires_at 置 0）+ 通知，保留配置供重新填写
            config.expires_at = 0
            config.updated_at = int(time.time())
            database_manager.save_trakt_config(config.to_dict())
            logger.warning(
                f"用户 {user_id} 的 Trakt Bearer 凭证已失效（invalid_grant），需重新填写"
            )
            notify_scheduler_failure(
                "trakt",
                f"用户 {user_id} 的 Trakt Bearer 凭证已失效，请重新填写",
                user_id=user_id,
            )
            return STATUS_EXPIRED

        if status != "ok":
            # 网络/服务端错误：非失效，保留原状态，下次心跳重试
            logger.warning(
                f"用户 {user_id} 的 Trakt Bearer 刷新失败（{err}），保留原状态"
            )
            return STATUS_FAILED

        new_tokens = _extract_new_tokens(data)
        if new_tokens is None:
            logger.warning(
                f"用户 {user_id} 的 Trakt Bearer 刷新响应缺少 token 字段，保留原状态"
            )
            return STATUS_FAILED
        access, refresh = new_tokens

        # ok：旋转式换新 access/refresh/expires_at 落库
        config.access_token = access
        config.refresh_token = refresh
        config.expires_at = _resolve_expires_at(data)
        config.updated_at = int(time.time())
        database_manager.save_trakt_config(config.to_dict())
        logger.info(
            f"用户 {user_id} 的 Trakt Bearer 凭证已续期，"
            f"有效期至 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(config.expires_at))}"
        )
        return STATUS_OK


async def heartbeat_all_users() -> dict:
    """每日心跳：对所有「bearer 模式且启用」的用户按 expires_at 智能续期。

    Returns:
        {"checked": n, "ok": n, "expired": n, "failed": n, "skipped": n}
    """
    results = {"checked": 0, "ok": 0, "expired": 0, "failed": 0, "skipped": 0}
    try:
        configs = database_manager.get_trakt_configs_with_sync_enabled()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Trakt Bearer 心跳：读取启用配置失败: {e}")
        return results

    for cfg in configs:
        config = TraktConfig.from_dict(cfg)
        if not config or config.auth_type != "bearer" or not config.refresh_token:
            continue
        results["checked"] += 1
        try:
            status = await refresh_user_bearer(config.user_id)
            if status == STATUS_OK:
                results["ok"] += 1
            elif status == STATUS_EXPIRED:
                results["expired"] += 1
            elif status == STATUS_SKIPPED:
                results["skipped"] += 1
            else:
                results["failed"] += 1
        except Exception as e:  # noqa: BLE001
            logger.error(f"用户 {config.user_id} 的 Bearer 续期异常: {e}")
            results["failed"] += 1

    logger.info(
        "Trakt Bearer 每日心跳完成: "
        f"检查 {results['checked']} 个用户, "
        f"{results['ok']} 续期, {results['expired']} 失效, "
        f"{results['failed']} 失败, {results['skipped']} 跳过"
    )
    return results
