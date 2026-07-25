"""
Summary AI 观影报告数据模型。
"""

from typing import Optional

from pydantic import BaseModel


class LLMConfigResponse(BaseModel):
    """GET /api/summary/llm 响应"""

    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""  # 掩码值（前端展示为 "***"）
    model: str = "gpt-4o-mini"
    max_tokens: int = 2000
    temperature: float = 0.7
    timeout: int = 60


class LLMConfigUpdate(BaseModel):
    """PUT /api/summary/llm 请求"""

    api_base: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    timeout: Optional[int] = None


class LLMTestResponse(BaseModel):
    """POST /api/summary/llm/test 响应"""

    success: bool
    message: str
    model: Optional[str] = None
    latency_ms: Optional[int] = None


class SummaryJobCreate(BaseModel):
    """POST /api/summary/jobs 请求"""

    name: str = "New Summary"
    cron: str = "0 21 * * *"
    lookback_days: int = 1
    user_name: str = ""
    system_prompt: str = ""
    max_records: int = 200
    enabled: bool = True


class SummaryJobUpdate(BaseModel):
    """PUT /api/summary/jobs/{id} 请求"""

    name: Optional[str] = None
    cron: Optional[str] = None
    lookback_days: Optional[int] = None
    user_name: Optional[str] = None
    system_prompt: Optional[str] = None
    max_records: Optional[int] = None
    enabled: Optional[bool] = None


class SummaryJobResponse(BaseModel):
    """summary job CRUD 响应"""

    name: str
    cron: str
    lookback_days: int
    user_name: str
    system_prompt: str
    max_records: int
    enabled: bool
    # 只读的 notification_type，供前端展示
    notification_type: str = ""

    @classmethod
    def from_config_dict(cls, data: dict) -> "SummaryJobResponse":
        """从 config_manager.get_summary_configs() 字典构建"""

        def _int(key: str, default: int) -> int:
            v = data.get(key, default)
            if v == "" or v is None:
                return default
            return int(v)

        name = str(data.get("name", ""))
        user_name = str(data.get("user_name", "") or "")
        notif_type = f"watching_summary_{name}"

        enabled = data.get("enabled", True)
        if not isinstance(enabled, bool):
            enabled = str(enabled).lower() in ("true", "1")

        return cls(
            name=str(data.get("name", "")),
            cron=str(data.get("cron", "0 21 * * *")),
            lookback_days=_int("lookback_days", 1),
            user_name=user_name,
            system_prompt=str(data.get("system_prompt", "")),
            max_records=_int("max_records", 200),
            enabled=enabled,
            notification_type=notif_type,
        )


class SummaryJobTestResponse(BaseModel):
    """POST /api/summary/jobs/{id}/test 响应"""

    success: bool
    job_name: str
    summary_text: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    record_count: int = 0
    error_message: str = ""


class LLMUsageStatsResponse(BaseModel):
    """GET /api/summary/llm/stats 响应"""

    total_calls: int = 0
    total_tokens: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    error_count: int = 0
    avg_latency_ms: int = 0
    by_model: list[dict] = []
    by_job: list[dict] = []
    daily: list[dict] = []
