"""Summary job configuration dataclass."""

from dataclasses import dataclass


@dataclass
class SummaryJobConfig:
    id: int
    enabled: bool = True
    name: str = ""
    cron: str = "0 21 * * *"
    lookback_days: int = 1
    user_name: str = ""
    system_prompt: str = (
        "你是一个轻松有趣的追番助手。用户会给你一段观影记录，请你用亲切自然的中文生成追番总结。\n\n"
        "规则：\n"
        '1. 如果记录为 0 条，友好地告知用户"今天还没有追番记录哦~"\n'
        '2. 按番剧分组，简要描述观看进度（如"《芙莉莲》追到 S1E10"）\n'
        "3. 如果涉及多用户（记录中 user_name 不同），按用户分开描述\n"
        "4. 加一两句轻松评论，语气像朋友聊天，不要太正式\n"
        "5. 限制在 300 字以内"
    )
    max_records: int = 200

    @classmethod
    def from_config_dict(cls, data: dict) -> "SummaryJobConfig":
        """Create from a config dict (as returned by config_manager.get_summary_configs())."""
        return cls(
            id=int(data.get("id", 0)),
            enabled=data.get("enabled", True)
            if isinstance(data.get("enabled"), bool)
            else str(data.get("enabled", "true")).lower() in ("true", "1"),
            name=str(data.get("name", "")),
            cron=str(data.get("cron", "0 21 * * *")),
            lookback_days=int(data.get("lookback_days", 1)),
            user_name=str(data.get("user_name", "")),
            system_prompt=str(data.get("system_prompt", cls.system_prompt)),
            max_records=int(data.get("max_records", 200)),
        )
