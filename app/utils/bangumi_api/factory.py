"""BangumiApi 工厂函数

从数据库账号目录构造 BangumiApi 实例，供需要"按当前活跃账号临时构造客户端"的场景复用：
- airing_calendar API（用户在看列表查询）
- airing_today 调度器（今日放送提醒）
- bangumi_replay 调度器（API 可达性探测）

短生命周期的实例应由调用方在 try/finally 中调用 close() 释放连接池。
"""

from __future__ import annotations

from app.core.accounts import (
    get_active_bangumi_config,
    get_bangumi_config_by_section,
)
from app.core.config import config_manager

from . import BangumiApi


def build_bangumi_api_from_active_config(
    section_name: str | None = None,
) -> BangumiApi | None:
    """从数据库账号目录构造 BangumiApi 实例

    Args:
        section_name: 指定账号段名（用于"我的追番"卡片切换账号）。
            传入时直接从 DB 取该 section 对应账号；None 时取当前激活账号。

    Returns:
        BangumiApi 实例；若无有效配置返回 None。调用方负责 close()。
    """
    # DB 为唯一真相源：section_name 用于精确指定账号，None 走激活账号
    if section_name:
        cfg = get_bangumi_config_by_section(section_name)
    else:
        cfg = get_active_bangumi_config()

    # OAuth 令牌临近过期时静默续期（仅 oauth 授权且配置 refresh_token 时生效），
    # 续期后重新读取，确保拿到最新 access_token。
    if cfg and cfg.get("access_token"):
        try:
            from app.services.bangumi.auth import bangumi_auth_service

            # 指定 section 时按 section 续期；否则由 auth 服务自行取激活账号
            bangumi_auth_service.refresh_active_token_if_needed(section_name)
        except Exception:
            pass
        # 续期可能更新了 DB，重新读取一次
        if section_name:
            cfg = get_bangumi_config_by_section(section_name) or cfg
        else:
            cfg = get_active_bangumi_config() or cfg

    if not cfg or not cfg.get("username") or not cfg.get("access_token"):
        return None
    dev_snapshot = config_manager.get_dev_http_snapshot()
    return BangumiApi(
        username=cfg["username"],
        access_token=cfg["access_token"],
        private=cfg.get("private", False),
        http_proxy=dev_snapshot["script_proxy"],
        ssl_verify=dev_snapshot["ssl_verify"],
        bgm_api_proxy=dev_snapshot["bgm_api_proxy"],
        bgm_next_proxy=dev_snapshot["bgm_next_proxy"],
    )
