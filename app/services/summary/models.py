"""Summary job 配置数据类。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SummaryJobConfig:
    name: str = ""
    enabled: bool = True
    cron: str = "0 21 * * *"
    lookback_days: int = 1
    user_name: str = ""
    system_prompt: str = (
        "你是一个轻松有趣的追番助手。用户会给你一段指定时间范围内的观影记录，请你用亲切自然的中文生成追番总结。\n\n"
        "规则：\n"
        '1. 如果记录为 0 条，告知用户"这段时间还没有追番记录哦~"\n'
        '2. 按番剧分组，简要描述观看进度（如"《芙莉莲》追到 S1E10"）\n'
        "3. 如果涉及多用户（记录中 user_name 不同），按用户分开描述\n"
        "4. 加一两句轻松评论，语气像朋友聊天，不要太正式\n"
        "5. 限制在 300 字以内"
    )
    max_records: int = -1  # -1 表示不限制
    # 记忆特性（未发布，直接重构）：
    # memory_limit=0 关闭记忆（不注入/不写入/不排除已消费记录）；
    # >0 注入最近 N 条摘要 + 已消费记录排除（信息由摘要承继）。
    memory_limit: int = 0  # 0=关；1–1000=注入最近 N 条摘要（对齐 prune 上限）
    related_limit: int = 0  # 0=关；1–1000=同剧关联最近 N 条（日期倒序）

    @classmethod
    def from_config_dict(cls, data: dict) -> SummaryJobConfig:
        """从 config_manager.get_summary_configs() 字典创建实例。"""

        def _limit(key: str) -> int:
            try:
                # 0–1000，负值/非法回落 0
                return max(0, min(1000, int(data.get(key, 0))))
            except (TypeError, ValueError):
                return 0

        def _int(key: str, default: int) -> int:
            """H2：非法值回落默认——单个坏配置不得拖垮调度注册（与 _limit 同款保护）。"""
            try:
                return int(data.get(key, default))
            except (TypeError, ValueError):
                return default

        return cls(
            name=str(data.get("name", "")),
            enabled=data.get("enabled", True)
            if isinstance(data.get("enabled"), bool)
            else str(data.get("enabled", "true")).lower() in ("true", "1"),
            cron=str(data.get("cron", "0 21 * * *")),
            lookback_days=_int("lookback_days", 1),
            user_name=str(data.get("user_name", "")),
            system_prompt=str(data.get("system_prompt", cls.system_prompt)),
            max_records=_int("max_records", -1),
            memory_limit=_limit("memory_limit"),
            related_limit=_limit("related_limit"),
        )


@dataclass
class SummaryRecord:
    """summary 链路内部观影记录载体（_query_records 返回类型）。"""

    id: int
    timestamp: str
    user_name: str
    title: str
    bgm_title: str
    season: int
    episode: int
    media_type: str
    source: str
    status: str
    consumed_run_ids: set[str] = field(
        default_factory=set
    )  # 消费标记（多对多，空集=未消费）
