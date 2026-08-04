"""BangumiApi 工厂函数

从配置构造 BangumiApi 实例，供需要"按当前活跃账号临时构造客户端"的场景复用：
- airing_calendar API（用户在看列表查询）
- airing_today 调度器（今日放送提醒）
- bangumi_replay 调度器（API 可达性探测）

短生命周期的实例应由调用方在 try/finally 中调用 close() 释放连接池。
"""

from __future__ import annotations

from ...core.config import config_manager
from . import BangumiApi


def build_bangumi_api_from_active_config(
    section_name: str | None = None,
) -> BangumiApi | None:
    """从配置构造 BangumiApi 实例（兼容单/多用户模式）

    Args:
        section_name: 多用户模式下指定 ``[bangumi-{username}]`` 段名。
            传入时直接从该段取配置（用于"我的追番"卡片切换账号）。
            None 时按原逻辑：单用户读 ``[bangumi]``，多用户取首个有效账号。

    Returns:
        BangumiApi 实例；若无有效配置返回 None。调用方负责 close()。
    """
    # 指定段名且非单用户默认段：直接从多账号段表精确查找
    if section_name and section_name != "bangumi":
        configs = config_manager.get_bangumi_configs()
        cfg = configs.get(section_name)
    else:
        cfg = config_manager.get_active_bangumi_config()

    # OAuth 令牌临近过期时静默续期（仅 oauth 授权且配置 refresh_token 时生效），
    # 续期后重新读取，确保拿到最新 access_token。
    if cfg and cfg.get("access_token"):
        target = section_name if (section_name and section_name != "bangumi") else None
        try:
            from app.services.bangumi.auth import bangumi_auth_service

            bangumi_auth_service.refresh_active_token_if_needed(target)
        except Exception:
            pass
        if section_name and section_name != "bangumi":
            cfg = config_manager.get_bangumi_configs().get(section_name) or cfg
        else:
            cfg = config_manager.get_active_bangumi_config() or cfg

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
