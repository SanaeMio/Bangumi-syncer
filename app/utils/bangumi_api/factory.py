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


def build_bangumi_api_from_active_config() -> BangumiApi | None:
    """从配置构造 BangumiApi 实例（兼容单/多用户模式）

    单用户模式读 [bangumi] 段，多用户模式取首个有效账号段。
    仅当 username 和 access_token 均非空才返回实例。

    Returns:
        BangumiApi 实例；若无有效配置返回 None。调用方负责 close()。
    """
    cfg = config_manager.get_active_bangumi_config()
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
