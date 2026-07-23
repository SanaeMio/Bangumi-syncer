"""
LLM 配置与连接测试 API。
"""

import time
from dataclasses import asdict

from fastapi import APIRouter, Depends

from ..core.config import LLM_SECTION, config_manager
from ..core.database import database_manager
from ..models.summary import (
    LLMConfigResponse,
    LLMConfigUpdate,
    LLMTestResponse,
    LLMUsageStatsResponse,
)
from ..services.llm import Message, get_llm_client
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/summary/llm", tags=["llm"])


@router.get("/conf", response_model=LLMConfigResponse)
async def get_llm_config(_=Depends(get_current_user_flexible)):
    cfg = config_manager.get_llm_config()
    # 遮掩 api_key 显示
    api_key = cfg.get("api_key", "")
    if api_key:
        api_key = "***" + api_key[-4:] if len(api_key) > 4 else "***"
    return LLMConfigResponse(
        api_base=cfg.get("api_base", ""),
        api_key=api_key,
        model=cfg.get("model", ""),
        max_tokens=cfg.get("max_tokens", 2000),
        temperature=cfg.get("temperature", 0.7),
        timeout=cfg.get("timeout", 60),
    )


@router.put("/conf")
async def update_llm_config(
    body: LLMConfigUpdate, _=Depends(get_current_user_flexible)
):
    """部分更新 LLM 配置。"""
    updates = body.model_dump(exclude_none=True)
    for key, value in updates.items():
        config_manager.set_config(LLM_SECTION, key, str(value))
    config_manager.reload_config()
    return {"status": "success", "message": "LLM 配置已更新"}


@router.post("/test", response_model=LLMTestResponse)
async def test_llm_connection(_=Depends(get_current_user_flexible)):
    """发送简单 ping 验证 LLM 连通性。"""
    try:
        client = get_llm_client()
        t0 = time.time()
        response = await client.chat(
            [Message(role="user", content="Hello")],
            job_name="llm_test",
        )
        latency = int((time.time() - t0) * 1000)
        return LLMTestResponse(
            success=True,
            message=response.content[:200],
            model=response.model,
            latency_ms=latency,
        )
    except Exception as e:
        return LLMTestResponse(success=False, message=str(e))


@router.get("/stats", response_model=LLMUsageStatsResponse)
async def get_llm_stats(
    scope: str = "aggregate", days: int = 30, _=Depends(get_current_user_flexible)
):
    """获取 LLM 用量统计。"""
    stats = database_manager.llm_usage.get_stats(scope=scope, days=days)
    return LLMUsageStatsResponse(**asdict(stats))
