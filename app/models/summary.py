"""
Summary AI watching report data models.
"""

from typing import Optional

from pydantic import BaseModel


class LLMConfigResponse(BaseModel):
    """Response for GET /api/summary/llm"""

    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""  # masked value (show "***" prefix)
    model: str = "gpt-4o-mini"
    max_tokens: int = 2000
    temperature: float = 0.7
    timeout: int = 60


class LLMConfigUpdate(BaseModel):
    """Request for PUT /api/summary/llm"""

    api_base: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    timeout: Optional[int] = None


class LLMTestResponse(BaseModel):
    """Response for POST /api/summary/llm/test"""

    success: bool
    message: str
    model: Optional[str] = None
    latency_ms: Optional[int] = None


class SummaryJobCreate(BaseModel):
    """Request for POST /api/summary/jobs"""

    name: str = "New Summary"
    cron: str = "0 21 * * *"
    lookback_days: int = 1
    user_name: str = ""
    system_prompt: str = ""
    max_records: int = 200
    enabled: bool = True


class SummaryJobUpdate(BaseModel):
    """Request for PUT /api/summary/jobs/{id}"""

    name: Optional[str] = None
    cron: Optional[str] = None
    lookback_days: Optional[int] = None
    user_name: Optional[str] = None
    system_prompt: Optional[str] = None
    max_records: Optional[int] = None
    enabled: Optional[bool] = None


class SummaryJobResponse(BaseModel):
    """Response for summary job CRUD"""

    id: int
    name: str
    cron: str
    lookback_days: int
    user_name: str
    system_prompt: str
    max_records: int
    enabled: bool
    # Read-only notification type for UI display
    notification_type: str = ""

    @classmethod
    def from_config_dict(cls, data: dict) -> "SummaryJobResponse":
        """Build from config_manager.get_summary_configs() dict"""
        user_name = str(data.get("user_name", "") or "")
        notif_type = (
            f"watching_summary_{user_name}" if user_name else "watching_summary"
        )

        enabled = data.get("enabled", True)
        if not isinstance(enabled, bool):
            enabled = bool(enabled)

        return cls(
            id=int(data.get("id", 0)),
            name=str(data.get("name", "")),
            cron=str(data.get("cron", "0 21 * * *")),
            lookback_days=int(data.get("lookback_days", 1)),
            user_name=user_name,
            system_prompt=str(data.get("system_prompt", "")),
            max_records=int(data.get("max_records", 200)),
            enabled=enabled,
            notification_type=notif_type,
        )


class SummaryJobTestResponse(BaseModel):
    """Response for POST /api/summary/jobs/{id}/test"""

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
    """Response for GET /api/summary/llm/stats"""

    total_calls: int = 0
    total_tokens: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    error_count: int = 0
    avg_latency_ms: int = 0
    by_model: list[dict] = []
    by_job: list[dict] = []
    daily: list[dict] = []
