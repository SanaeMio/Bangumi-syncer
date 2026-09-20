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

    access_token = (request.access_token or "").strip()
    media_usernames = [
        u.strip() for u in (request.media_server_usernames or []) if u.strip()
    ]

    section = (request.section_name or "").strip()
    is_new = not section

    if is_new:
        section = _generate_section_name(username)
    else:
        existing = get_bangumi_account(section)
        if not existing:
            raise HTTPException(
                status_code=404, detail=f"账号 {section} 不存在，无法更新"
            )

    # 构建账号 dict（保留已有 OAuth 字段）
    existing = get_bangumi_account(section) or {}
    account = {
        "section_name": section,
        "username": username,
        "media_server_usernames": media_usernames,
        "auth_method": existing.get("auth_method", "manual"),
        "access_token": access_token or existing.get("access_token", ""),
        "refresh_token": existing.get("refresh_token", ""),
        "token_type": existing.get("token_type", "Bearer"),
        "expires_at": existing.get("expires_at"),
        "bangumi_user_id": existing.get("bangumi_user_id", ""),
        "nickname": existing.get("nickname", ""),
        "avatar": existing.get("avatar", ""),
        "private": request.private,
        "is_primary": existing.get("is_primary", False),
        "enabled": existing.get("enabled", True),
    }

    if not save_bangumi_account(account):
        raise HTTPException(status_code=500, detail="保存账号失败")

    # 首个账号自动首选
    if count_bangumi_accounts() == 1:
        set_primary_bangumi_account(section)

    logger.info(
        f"{'新增' if is_new else '更新'} Bangumi 账号: {section} (username={username})"
    )
    return AccountActionResponse(
        status="success",
        message=f"账号 {'新增' if is_new else '更新'}成功",
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
