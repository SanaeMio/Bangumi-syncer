"""
Summary AI 观影报告任务管理 API。
"""

from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException

from ..core.config import config_manager
from ..core.database import database_manager
from ..models.summary import (
    ClearMemoryRequest,
    SummaryJobCreate,
    SummaryJobResponse,
    SummaryJobTestResponse,
    SummaryJobUpdate,
)
from ..services.memory.service import MemoryService
from ..services.summary import SummaryJobConfig, summary_scheduler, summary_service
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/summary/jobs", tags=["summary_jobs"])

# 记忆清理入口（改名联动 / clear-memory 端点）
memory_service = MemoryService(database_manager.memory)


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
            # 记忆跟随任务（与 rename_notification_type 同流程）
            memory_service.rename_task(
                "summary", f"summary-{decoded}", f"summary-{updates['name']}"
            )
    config_manager.save_summary_config(updates, old_name=decoded)
    config_manager.reload_config()
    await summary_scheduler.apply_config_after_save()
    return {"status": "success", "message": "摘要任务已更新"}


@router.delete("/{name:path}")
async def delete_summary_job(name: str, _=Depends(get_current_user_flexible)):
    decoded = unquote(name)
    config_manager.delete_summary_config(decoded)
    # 清理该任务记忆（主表 + 归档 + 消费标记，同一事务）：
    # 避免孤儿记忆行与悬挂 consumed_run_id（重名重建 job 时产生虚假 overlap）。
    memory_service.clear_task("summary", f"summary-{decoded}")
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

    if not summary_text:
        # H1-API 修正：空内容即失败（usage 存在但空 choices 仍可能是失败调用）
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


@router.post("/{name:path}/clear-memory")
async def clear_summary_job_memory(
    name: str,
    body: ClearMemoryRequest,
    _=Depends(get_current_user_flexible),
):
    """清空任务记忆（不可恢复，二次确认）。

    同一事务删除主表 + 归档表 + 清相关消费标记（不筛 outcome 全部删除）。
    想保留偏好重新开始 → 复制为新 job（旧 job 记忆完整保留）。
    """
    decoded = unquote(name)
    _find_config(decoded)  # 任务不存在 404
    if not body.confirm:
        raise HTTPException(422, "必须携带 confirm=true 确认清空")
    deleted = memory_service.clear_task("summary", f"summary-{decoded}")
    return {
        "status": "success",
        "message": "任务记忆已清空",
        "deleted_records": deleted,
    }


@router.get("/{name:path}/memory-stats")
async def summary_job_memory_stats(name: str, _=Depends(get_current_user_flexible)):
    """记忆规模统计：该任务已积累多少记忆、按当前配置将注入多大上下文。

    返回绝对量（不做百分比）：
    - total_count / total_chars / avg_chars：任务已积累的摘要规模（热层）
    - memory_limit / related_limit：当前配置
    - injected_estimate_tokens：按配置估算的注入量（估算口径：
      字符数 × 0.7 粗略中文 token 系数，见 closeout §评测；仅展示参考）
    """
    decoded = unquote(name)
    _find_config(decoded)  # 任务不存在 404
    task_id = f"summary-{decoded}"

    rows = database_manager.memory.get_recent("summary", task_id, limit=1000)
    total_count = len(rows)
    total_chars = sum(len(e.summary) for e in rows)
    avg_chars = round(total_chars / total_count) if total_count else 0

    cfg = SummaryJobConfig.from_config_dict(_find_config(decoded))
    memory_limit = cfg.memory_limit
    related_limit = cfg.related_limit
    # 估算：recent 注入 = min(存量, memory_limit) 条；related 按配置深度估算
    injected_count = min(total_count, memory_limit) + related_limit
    injected_estimate_tokens = round(injected_count * avg_chars * 0.7)

    return {
        "status": "success",
        "data": {
            "task_id": task_id,
            "total_count": total_count,
            "total_chars": total_chars,
            "avg_chars": avg_chars,
            "memory_limit": memory_limit,
            "related_limit": related_limit,
            "injected_estimate_tokens": injected_estimate_tokens,
        },
    }
