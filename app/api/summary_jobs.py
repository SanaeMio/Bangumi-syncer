"""
Summary AI 观影报告任务管理 API。
"""

from fastapi import APIRouter, Depends, HTTPException

from ..core.config import config_manager
from ..models.summary import (
    SummaryJobCreate,
    SummaryJobResponse,
    SummaryJobTestResponse,
    SummaryJobUpdate,
)
from ..services.summary import SummaryJobConfig, summary_scheduler, summary_service
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/summary/jobs", tags=["summary_jobs"])


@router.get("")
async def list_summary_jobs(_=Depends(get_current_user_flexible)):
    configs = config_manager.get_summary_configs()
    return {
        "status": "success",
        "data": [SummaryJobResponse.from_config_dict(c).model_dump() for c in configs],
    }


@router.post("")
async def create_summary_job(
    body: SummaryJobCreate, _=Depends(get_current_user_flexible)
):
    data = body.model_dump()
    config_manager.save_summary_config(data)
    config_manager.reload_config()
    await summary_scheduler.apply_config_after_save()
    return {"status": "success", "message": "摘要任务已创建"}


@router.put("/{job_id}")
async def update_summary_job(
    job_id: int, body: SummaryJobUpdate, _=Depends(get_current_user_flexible)
):
    updates = body.model_dump(exclude_none=True)
    updates["id"] = job_id
    config_manager.save_summary_config(updates)
    config_manager.reload_config()
    await summary_scheduler.apply_config_after_save()
    return {"status": "success", "message": "摘要任务已更新"}


@router.delete("/{job_id}")
async def delete_summary_job(job_id: int, _=Depends(get_current_user_flexible)):
    config_manager.delete_summary_config(job_id)
    config_manager.reload_config()
    await summary_scheduler.apply_config_after_save()
    return {"status": "success", "message": "摘要任务已删除"}


# test 与 trigger 的区别：
# - test：调用 LLM 生成摘要，结果直接返回给用户，不发送通知。
#         用于前端预览摘要效果、调试 system_prompt。
# - trigger：完整执行一次任务（生成摘要 + 发送通知），等同于调度器定时触发。
#           用于手动立即执行已配置好的任务。


@router.post("/{job_id}/test", response_model=SummaryJobTestResponse)
async def test_summary_job(job_id: int, _=Depends(get_current_user_flexible)):
    """测试运行摘要任务——仅生成摘要并返回结果，不发送通知。"""
    configs = config_manager.get_summary_configs()
    target = None
    for c in configs:
        if int(c.get("id", 0)) == job_id:
            target = c
            break
    if not target:
        raise HTTPException(status_code=404, detail="摘要任务未找到")

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


@router.post("/{job_id}/trigger")
async def trigger_summary_job(job_id: int, _=Depends(get_current_user_flexible)):
    """手动立即触发一次摘要任务（生成摘要 + 发送通知）。"""
    configs = config_manager.get_summary_configs()
    target = None
    for c in configs:
        if int(c.get("id", 0)) == job_id:
            target = c
            break
    if not target:
        raise HTTPException(status_code=404, detail="摘要任务未找到")

    job_config = SummaryJobConfig.from_config_dict(target)
    await summary_service.execute_job(job_config)
    return {"status": "success", "message": f"任务 '{job_config.name}' 已触发"}
