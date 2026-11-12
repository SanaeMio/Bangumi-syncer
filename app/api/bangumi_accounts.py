"""Bangumi 账号管理 API（DB 为唯一真相源）

提供账号列表查询、新增/更新、删除、切换首选、启用/停用等接口，
供配置页"账号列表"卡片使用。所有操作直接落 DB，不经过 INI。

首选（is_primary）与启用（enabled）职责分离：前者是 user_name 缺失时回退的
首选账号，后者控制账号是否参与任务同步。
"""

# ruff: noqa: UP045 — Pydantic v2 在 Python 3.9 下解析模型字段的 ``str | None`` 会失败，此处保留 Optional

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.accounts import (
    count_bangumi_accounts,
    delete_bangumi_account,
    get_bangumi_account,
    get_primary_bangumi_account,
    list_bangumi_accounts,
    save_bangumi_account,
    set_enabled_bangumi_account,
    set_primary_bangumi_account,
)
from ..core.logging import logger
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/bangumi/accounts", tags=["bangumi-accounts"])


# ── 响应模型 ──────────────────────────────────────────────────────


class AccountInfo(BaseModel):
    """账号详情（供配置页列表展示）"""

    section_name: str = Field(description="账号唯一键（配置段名）")
    username: str = Field(default="", description="Bangumi 用户名")
    media_server_usernames: list[str] = Field(
        default_factory=list, description="媒体服务器用户名列表"
    )
    auth_method: str = Field(default="manual", description="认证方式 manual/oauth")
    nickname: str = Field(default="", description="昵称（OAuth 授权时获取）")
    avatar: str = Field(default="", description="头像 URL")
    bangumi_user_id: str = Field(
        default="", description="Bangumi 用户 ID（OAuth 授权时获取）"
    )
    expires_at: Optional[int] = Field(
        None, description="访问令牌过期时间戳（OAuth 账号，手动账号为 null）"
    )
    private: bool = Field(default=False, description="观看记录仅自己可见")
    is_primary: bool = Field(default=False, description="是否为当前首选账号")
    enabled: bool = Field(
        default=True, description="是否参与任务同步（停用后不参与同步）"
    )
    has_token: bool = Field(default=False, description="是否已配置访问令牌")


class AccountsListResponse(BaseModel):
    status: str = "success"
    data: list[AccountInfo] = Field(default_factory=list)
    primary: Optional[str] = Field(None, description="当前首选账号段名")


class AccountUpsertRequest(BaseModel):
    """新增/更新账号请求"""

    section_name: Optional[str] = Field(
        None, description="账号段名（更新时必填，新增时留空自动生成）"
    )
    username: str = Field(description="Bangumi 用户名")
    access_token: str = Field(default="", description="访问令牌")
    media_server_usernames: list[str] = Field(
        default_factory=list, description="媒体服务器用户名列表"
    )
    private: bool = Field(default=False, description="观看记录仅自己可见")


class AccountActionResponse(BaseModel):
    status: str = "success"
    message: str = ""


class AccountEnabledRequest(BaseModel):
    """启用/停用账号请求"""

    enabled: bool = Field(description="true 启用、false 停用（停用后不参与任务同步）")


class AccountBulkRequest(BaseModel):
    """批量保存账号请求（配置页「保存配置」一次性提交全部账号条目）"""

    accounts: list[AccountUpsertRequest] = Field(
        default_factory=list, description="账号列表，按页面顺序依次写入"
    )


class AccountBulkItem(BaseModel):
    """批量保存后单个账号的处理结果"""

    section_name: str = Field(description="账号段名（新增时由后端生成）")
    username: str = Field(default="", description="Bangumi 用户名")
    created: bool = Field(default=False, description="本次是否为新增")
    changed: bool = Field(default=False, description="本次是否有实际改动")


class AccountBulkResponse(BaseModel):
    status: str = "success"
    message: str = ""
    changed: int = Field(default=0, description="实际有改动的账号数（含新增）")
    data: list[AccountBulkItem] = Field(default_factory=list)


# ── 辅助函数 ──────────────────────────────────────────────────────


def _account_to_info(acc: dict[str, Any]) -> AccountInfo:
    """把 DB 账号行转为 API 响应模型（不暴露 token）。"""
    return AccountInfo(
        section_name=acc.get("section_name", ""),
        username=acc.get("username", ""),
        media_server_usernames=list(acc.get("media_server_usernames") or []),
        auth_method=acc.get("auth_method", "manual"),
        nickname=acc.get("nickname", ""),
        avatar=acc.get("avatar", ""),
        bangumi_user_id=acc.get("bangumi_user_id", ""),
        expires_at=acc.get("expires_at"),
        private=bool(acc.get("private")),
        is_primary=bool(acc.get("is_primary")),
        enabled=bool(acc.get("enabled", True)),
        has_token=bool(acc.get("access_token")),
    )


def _generate_section_name(username: str) -> str:
    """为新增账号生成唯一的 section_name（bangumi-{username}，冲突时加序号）。"""
    base = f"bangumi-{username}"
    candidate = base
    counter = 1
    while get_bangumi_account(candidate):
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _apply_account_upsert(request: AccountUpsertRequest) -> tuple[str, bool, bool]:
    """写入单个账号（新增或更新），返回 (段名, 是否新增, 是否有实际改动)。

    调用方须先校验用户名非空、更新目标段已存在（见 upsert_account 与
    bulk_upsert_accounts 的预校验），本函数不再重复报 400/404。

    与库里完全一致的账号不重复写库：配置页「保存配置」每次都会提交全部
    账号条目，未改动的账号没必要再写一遍，也免得日志刷屏。
    """
    username = (request.username or "").strip()
    access_token = (request.access_token or "").strip()
    media_usernames = [
        u.strip() for u in (request.media_server_usernames or []) if u.strip()
    ]

    section = (request.section_name or "").strip()
    is_new = not section
    if is_new:
        section = _generate_section_name(username)

    # 构建账号 dict（保留已有 OAuth 字段）
    existing = get_bangumi_account(section) or {}
    # 手动粘贴了新令牌：上一份 OAuth 凭据的刷新令牌与过期时间对新令牌无效，
    # 继续保留会让界面一直显示「令牌已过期」（并让同步层反复拿旧 refreshed_token
    # 去刷新）。按 disconnect() 的同一套语义落成手动模式。
    token_replaced = bool(access_token) and access_token != existing.get(
        "access_token", ""
    )
    account = {
        "section_name": section,
        "username": username,
        "media_server_usernames": media_usernames,
        "auth_method": "manual"
        if token_replaced
        else existing.get("auth_method", "manual"),
        "access_token": access_token or existing.get("access_token", ""),
        "refresh_token": "" if token_replaced else existing.get("refresh_token", ""),
        "token_type": existing.get("token_type", "Bearer"),
        "expires_at": None if token_replaced else existing.get("expires_at"),
        "bangumi_user_id": existing.get("bangumi_user_id", ""),
        "nickname": existing.get("nickname", ""),
        "avatar": existing.get("avatar", ""),
        "private": request.private,
        "is_primary": existing.get("is_primary", False),
        "enabled": existing.get("enabled", True),
    }

    changed = is_new or (
        username != existing.get("username", "")
        or bool(request.private) != bool(existing.get("private"))
        or media_usernames != list(existing.get("media_server_usernames") or [])
        or (access_token != "" and access_token != existing.get("access_token", ""))
    )
    if not changed:
        return section, False, False

    if not save_bangumi_account(account):
        raise HTTPException(status_code=500, detail="保存账号失败")

    # 首个账号自动首选
    if count_bangumi_accounts() == 1:
        set_primary_bangumi_account(section)

    logger.info(
        f"{'新增' if is_new else '更新'} Bangumi 账号: {section} (username={username})"
    )
    return section, is_new, True


# ── 路由 ──────────────────────────────────────────────────────────


@router.get("", response_model=AccountsListResponse)
async def get_accounts(
    _current_user: dict = Depends(get_current_user_flexible),
) -> AccountsListResponse:
    """列出全部 Bangumi 账号（供配置页列表展示，不返回 token）。"""
    accounts = [_account_to_info(acc) for acc in list_bangumi_accounts()]
    primary_acc = get_primary_bangumi_account()
    primary = primary_acc.get("section_name") if primary_acc else None
    return AccountsListResponse(data=accounts, primary=primary)


@router.post("", response_model=AccountActionResponse)
async def upsert_account(
    request: AccountUpsertRequest,
    _current_user: dict = Depends(get_current_user_flexible),
) -> AccountActionResponse:
    """新增或更新账号（upsert by section_name）。

    - ``section_name`` 为空时自动生成（``bangumi-{username}``）。
    - 已存在则更新 username/access_token/media_server_usernames/private 字段，
      保留原有的 auth_method/expires_at/nickname/avatar 等 OAuth 字段。
    - 首个账号自动首选。
    """
    username = (request.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")

    section = (request.section_name or "").strip()
    if section and not get_bangumi_account(section):
        raise HTTPException(status_code=404, detail=f"账号 {section} 不存在，无法更新")

    _section, is_new, _changed = _apply_account_upsert(request)
    return AccountActionResponse(
        status="success",
        message=f"账号 {'新增' if is_new else '更新'}成功",
    )


@router.post("/bulk", response_model=AccountBulkResponse)
async def bulk_upsert_accounts(
    request: AccountBulkRequest,
    _current_user: dict = Depends(get_current_user_flexible),
) -> AccountBulkResponse:
    """批量新增/更新账号（配置页「保存配置」一次性提交全部账号条目）。

    先整体校验再逐个写入：任一账号校验不通过则整批拒绝，不会只写一半。
    """
    accounts = request.accounts or []

    # 1. 先校验：用户名非空、更新目标段存在
    for index, item in enumerate(accounts, start=1):
        if not (item.username or "").strip():
            raise HTTPException(
                status_code=400, detail=f"第 {index} 个账号：用户名不能为空"
            )
        section = (item.section_name or "").strip()
        if section and not get_bangumi_account(section):
            raise HTTPException(
                status_code=404, detail=f"账号 {section} 不存在，无法更新"
            )

    # 2. 逐个写入（新增段名按顺序生成，不会互相冲突）
    results: list[AccountBulkItem] = []
    for item in accounts:
        section, is_new, changed = _apply_account_upsert(item)
        results.append(
            AccountBulkItem(
                section_name=section,
                username=(item.username or "").strip(),
                created=is_new,
                changed=changed,
            )
        )

    created = sum(1 for r in results if r.created)
    updated = sum(1 for r in results if not r.created and r.changed)
    changed_count = sum(1 for r in results if r.changed)
    logger.info(
        f"批量保存 Bangumi 账号: 共 {len(results)} 个（有改动 {changed_count} 个）"
    )
    if not results:
        message = "没有需要保存的账号"
    elif created and updated:
        message = f"账号已保存（新增 {created} 个、更新 {updated} 个）"
    elif created:
        message = f"账号已保存（新增 {created} 个）"
    elif updated:
        message = f"账号已保存（更新 {updated} 个）"
    else:
        message = "账号信息没有变化"
    return AccountBulkResponse(
        status="success",
        message=message,
        changed=changed_count,
        data=results,
    )


@router.delete("/{section_name}", response_model=AccountActionResponse)
async def remove_account(
    section_name: str,
    _current_user: dict = Depends(get_current_user_flexible),
) -> AccountActionResponse:
    """删除指定账号。"""
    if not get_bangumi_account(section_name):
        raise HTTPException(status_code=404, detail="账号不存在")

    if not delete_bangumi_account(section_name):
        raise HTTPException(status_code=500, detail="删除账号失败")

    # 删除的是首选账号时，自动首选剩余首个
    remaining = list_bangumi_accounts()
    if remaining and not get_primary_bangumi_account():
        set_primary_bangumi_account(remaining[0]["section_name"])

    logger.info(f"删除 Bangumi 账号: {section_name}")
    return AccountActionResponse(status="success", message="账号已删除")


@router.post("/{section_name}/primary", response_model=AccountActionResponse)
async def set_primary_account(
    section_name: str,
    _current_user: dict = Depends(get_current_user_flexible),
) -> AccountActionResponse:
    """将指定账号设为首选账号。"""
    if not get_bangumi_account(section_name):
        raise HTTPException(status_code=404, detail="账号不存在")

    if not set_primary_bangumi_account(section_name):
        raise HTTPException(status_code=500, detail="切换首选账号失败")

    logger.info(f"切换首选 Bangumi 账号: {section_name}")
    return AccountActionResponse(status="success", message="已设为首选账号")


@router.post("/{section_name}/enabled", response_model=AccountActionResponse)
async def set_account_enabled(
    section_name: str,
    request: AccountEnabledRequest,
    _current_user: dict = Depends(get_current_user_flexible),
) -> AccountActionResponse:
    """启用/停用指定账号；停用后该账号不参与任务同步。

    用于多账号场景下的临时停用（如某用户未观看该番剧时临时跳过其同步），
    账号仍保留在列表中，重新启用后恢复同步。
    """
    if not get_bangumi_account(section_name):
        raise HTTPException(status_code=404, detail="账号不存在")

    if not set_enabled_bangumi_account(section_name, request.enabled):
        raise HTTPException(status_code=500, detail="设置账号启用状态失败")

    action = "启用" if request.enabled else "停用"
    logger.info(f"{action} Bangumi 账号: {section_name}")
    return AccountActionResponse(status="success", message=f"账号已{action}")
