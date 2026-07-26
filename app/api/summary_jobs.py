"""
Summary AI 观影报告任务管理 API。
"""

from urllib.parse import unquote

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


def _validate_job_name(name: str, old_name: str = "") -> None:
    """校验任务名称：不能含逗号，不能与已有任务重名。"""
    if "," in name:
        raise HTTPException(422, "任务名称不能包含逗号")
    for cfg in config_manager.get_summary_configs():
        existing = cfg.get("name", "")
        if existing == old_name:
            continue
        if existing == name:
            raise HTTPException(409, f"任务名称 '{name}' 已存在")


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
    _validate_job_name(body.name)
    data = body.model_dump()
    config_manager.save_summary_config(data)
    config_manager.reload_config()
    await summary_scheduler.apply_config_after_save()
    return {"status": "success", "message": "摘要任务已创建"}


@router.put("/{name:path}")
async def update_summary_job(
    name: str, body: SummaryJobUpdate, _=Depends(get_current_user_flexible)
):
    decoded = unquote(name)
    updates = body.model_dump(exclude_none=True)
    if "name" in updates:
        _validate_job_name(updates["name"], old_name=decoded)
        if updates["name"] != decoded:
            old_type = f"watching_summary_{decoded}"
            new_type = f"watching_summary_{updates['name']}"
            config_manager.rename_notification_type(old_type, new_type)
    config_manager.save_summary_config(updates, old_name=decoded)
    config_manager.reload_config()
    await summary_scheduler.apply_config_after_save()
    return {"status": "success", "message": "摘要任务已更新"}


@router.delete("/{name:path}")
async def delete_summary_job(name: str, _=Depends(get_current_user_flexible)):
    decoded = unquote(name)
    config_manager.delete_summary_config(decoded)
    config_manager.reload_config()
    await summary_scheduler.apply_config_after_save()
    return {"status": "success", "message": "摘要任务已删除"}


# test 与 trigger 的区别：
# - test：调用 LLM 生成摘要，结果直接返回给用户，不发送通知。
#         用于前端预览摘要效果、调试 system_prompt。
# - trigger：完整执行一次任务（生成摘要 + 发送通知），等同于调度器定时触发。
#           用于手动立即执行已配置好的任务。


def _find_config(name: str) -> dict:
    for c in config_manager.get_summary_configs():
        if c.get("name") == name:
            return c
    raise HTTPException(status_code=404, detail="摘要任务未找到")


@router.post("/{name:path}/test", response_model=SummaryJobTestResponse)
async def test_summary_job(name: str, _=Depends(get_current_user_flexible)):
    """测试运行摘要任务——仅生成摘要并返回结果，不发送通知。"""
    decoded = unquote(name)
    target = _find_config(decoded)
    job_config = SummaryJobConfig.from_config_dict(target)
    result = await summary_service.generate_summary(job_config)
    summary_text = result["summary_text"]
    usage = result.get("usage")

    if not summary_text and usage is None:
        return SummaryJobTestResponse(
            success=False,
            job_name=job_config.name,
            error_message="LLM 调用失败：所有重试均已耗尽",
            record_count=result["record_count"],
        )

    return SummaryJobTestResponse(
        success=True,
        job_name=job_config.name,
        summary_text=summary_text,
        model=result["model"],
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        total_tokens=usage.total_tokens if usage else 0,
        latency_ms=result.get("latency_ms", 0),
        record_count=result["record_count"],
    )


@router.post("/{name:path}/trigger")
async def trigger_summary_job(name: str, _=Depends(get_current_user_flexible)):
    """手动立即触发一次摘要任务（生成摘要 + 发送通知）。"""
    decoded = unquote(name)
    target = _find_config(decoded)
    job_config = SummaryJobConfig.from_config_dict(target)
    await summary_service.execute_job(job_config)
    return {"status": "success", "message": f"任务 '{job_config.name}' 已触发"}
