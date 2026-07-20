"""
Summary AI watching report API.
"""

import time

from fastapi import APIRouter, Depends, HTTPException

from ..core.config import config_manager
from ..core.database import database_manager
from ..models.summary import (
    LLMConfigResponse,
    LLMConfigUpdate,
    LLMTestResponse,
    LLMUsageStatsResponse,
    SummaryJobCreate,
    SummaryJobResponse,
    SummaryJobTestResponse,
    SummaryJobUpdate,
)
from ..services.llm import Message, get_llm_client
from ..services.summary import SummaryJobConfig, summary_scheduler, summary_service
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/summary", tags=["summary"])


# ===== LLM Config =====
# 文件名改为 llm.py
# review 实际应该叫 llm/config，配置相关的接口路径都改了
@router.get("/llm", response_model=LLMConfigResponse)
async def get_llm_config(_=Depends(get_current_user_flexible)):
    cfg = config_manager.get_llm_config()
    # Mask api_key for display
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


@router.put("/llm")
async def update_llm_config(
    body: LLMConfigUpdate, _=Depends(get_current_user_flexible)
):
    """Update LLM config fields (partial update)."""
    updates = body.model_dump(exclude_none=True)
    for key, value in updates.items():
        # review 提取 llm 为常量，并引用常量
        config_manager.set_config("llm", key, str(value))
    config_manager.reload_config()
    return {"status": "success", "message": "LLM configuration updated"}


@router.post("/llm/test", response_model=LLMTestResponse)
async def test_llm_connection(_=Depends(get_current_user_flexible)):
    """Send a simple ping to verify LLM connectivity."""
    try:
        client = get_llm_client()
        t0 = time.time()
        response = await client.chat([Message(role="user", content="Hello")])
        latency = int((time.time() - t0) * 1000)
        # review 这里也要记录下用量
        return LLMTestResponse(
            success=True,
            message=response.content[:200],
            model=response.model,
            latency_ms=latency,
        )
    except Exception as e:
        return LLMTestResponse(success=False, message=str(e))


@router.get("/llm/stats", response_model=LLMUsageStatsResponse)
async def get_llm_stats(
    scope: str = "aggregate", days: int = 30, _=Depends(get_current_user_flexible)
):
    """Get LLM usage statistics."""
    stats = database_manager.llm_usage.get_stats(scope=scope, days=days)
    return LLMUsageStatsResponse(**stats)


# ===== Summary Jobs =====

# review 前缀不一样的接口应该区分到不同的文件中，新文件叫 summary_jobs
# review 前缀从 jobs 改为 summary_jobs

@router.get("/jobs")
async def list_summary_jobs(_=Depends(get_current_user_flexible)):
    configs = config_manager.get_summary_configs()
    return {
        "status": "success",
        "data": [SummaryJobResponse.from_config_dict(c).model_dump() for c in configs],
    }


@router.post("/jobs")
async def create_summary_job(
    body: SummaryJobCreate, _=Depends(get_current_user_flexible)
):
    data = body.model_dump()
    config_manager.save_summary_config(data)
    config_manager.reload_config()
    await summary_scheduler.apply_config_after_save()
    return {"status": "success", "message": "Summary job created"}


@router.put("/jobs/{job_id}")
async def update_summary_job(
    job_id: int, body: SummaryJobUpdate, _=Depends(get_current_user_flexible)
):
    updates = body.model_dump(exclude_none=True)
    updates["id"] = job_id
    config_manager.save_summary_config(updates)
    config_manager.reload_config()
    await summary_scheduler.apply_config_after_save()
    return {"status": "success", "message": "Summary job updated"}


@router.delete("/jobs/{job_id}")
async def delete_summary_job(job_id: int, _=Depends(get_current_user_flexible)):
    config_manager.delete_summary_config(job_id)
    config_manager.reload_config()
    await summary_scheduler.apply_config_after_save()
    return {"status": "success", "message": "Summary job deleted"}

# TODO 需要区分 test 和 trigger 的用途，主要是从前端调用来看。
# TODO 仅从后端代码来看，test 没有调用notify，而是直接将 LLM 的响应返回给了用户
@router.post("/jobs/{job_id}/test", response_model=SummaryJobTestResponse)
async def test_summary_job(job_id: int, _=Depends(get_current_user_flexible)):
    """Test-run a summary job (generate + send)."""
    configs = config_manager.get_summary_configs()
    target = None
    for c in configs:
        if int(c.get("id", 0)) == job_id:
            target = c
            break
    if not target:
        raise HTTPException(status_code=404, detail="Summary job not found")

    job_config = SummaryJobConfig.from_config_dict(target)
    result = await summary_service.generate_summary(job_config)
    usage = result.get("usage")
    return SummaryJobTestResponse(
        success=True,
        job_name=job_config.name,
        summary_text=result["summary_text"],
        model=result["model"],
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        total_tokens=usage.total_tokens if usage else 0,
        latency_ms=0,
        record_count=result["record_count"],
    )


@router.post("/jobs/{job_id}/trigger")
async def trigger_summary_job(job_id: int, _=Depends(get_current_user_flexible)):
    # review 中文注释
    """Manually trigger a summary job now."""
    configs = config_manager.get_summary_configs()
    target = None
    for c in configs:
        if int(c.get("id", 0)) == job_id:
            target = c
            break
    if not target:
        raise HTTPException(status_code=404, detail="Summary job not found")

    job_config = SummaryJobConfig.from_config_dict(target)
    await summary_service.execute_job(job_config)
    return {"status": "success", "message": f"Job '{job_config.name}' triggered"}
