"""
Summary AI 观影报告任务管理 API。
"""

from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException

from ..core.config import config_manager
from ..core.database import database_manager
from ..core.logging import logger
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


def _reload_config_best_effort() -> None:
    """持久层写入成功后的内存重载：失败仅记日志，不向上抛出。

    持久层（ini/SQLite）已经成功，内存态会在下次启动/保存时自行收敛；
    若在此抛 500，用户会误以为操作失败而重试，但重试寻址可能已失效。
    """
    try:
        config_manager.reload_config()
    except Exception as e:
        logger.error(f"配置已写入但内存重载失败（将在下次启动/保存时收敛）: {e}")


async def _apply_scheduler_config_best_effort() -> None:
    """调度器同步：失败仅记日志，不改变持久层成功语义。"""
    try:
        await summary_scheduler.apply_config_after_save()
    except Exception as e:
        logger.error(f"配置已写入但调度器同步失败（将在下次启动/保存时收敛）: {e}")


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
    # save 成功后持久层语义已成立：reload/apply 属运行时同步，
    # 失败只降级为日志（内存会自行收敛），避免误报 500 诱导重复创建。
    _reload_config_best_effort()
    await _apply_scheduler_config_best_effort()
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
            # 先迁记忆、再改配置名：rename_task 失败时旧名配置仍在，任务依旧可寻址，
            # 用户重试即可自愈（rename_task 幂等）；避免配置改名成功后记忆迁移失败，
            # 导致旧名寻址失效、记忆成为孤儿且无法重试。
            try:
                memory_service.rename_task(
                    "summary", f"summary-{decoded}", f"summary-{updates['name']}"
                )
            except Exception as e:
                logger.error(
                    f"任务改名记忆迁移失败（{decoded}→{updates['name']}）: {e}"
                )
                raise HTTPException(
                    500, "任务改名失败：记忆迁移异常，请稍后重试"
                ) from e
            config_manager.rename_notification_type(old_type, new_type)
    config_manager.save_summary_config(updates, old_name=decoded)
    # save 成功后持久层语义已成立：reload/apply 失败只降级为日志。
    _reload_config_best_effort()
    await _apply_scheduler_config_best_effort()
    return {"status": "success", "message": "摘要任务已更新"}


@router.delete("/{name:path}")
async def delete_summary_job(name: str, _=Depends(get_current_user_flexible)):
    decoded = unquote(name)
    # 先清记忆、再删配置：clear_task 失败时配置仍在，任务名依旧可寻址，
    # 用户重试即可自愈（clear_task 幂等收敛）；若先删配置，则中途失败会留下
    # 无法寻址的孤儿记忆（主表 + 归档 + 消费标记，同一事务）。
    try:
        memory_service.clear_task("summary", f"summary-{decoded}")
    except Exception as e:
        logger.error(f"删除任务时清理记忆失败（{decoded}）: {e}")
        raise HTTPException(500, "删除任务失败：记忆清理异常，请稍后重试") from e
    config_manager.delete_summary_config(decoded)
    # 持久层已成功：reload/apply 运行时同步失败只降级为日志（无需重试，内存会自行收敛）。
    _reload_config_best_effort()
    await _apply_scheduler_config_best_effort()
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
    """手动立即触发一次摘要任务（生成摘要 + 发送通知）。

    任务已在执行（手动连点或与 cron 重叠）时返回 skipped，避免重复 LLM 调用、
    重复记忆写入与重复通知。
    """
    decoded = unquote(name)
    target = _find_config(decoded)
    job_config = SummaryJobConfig.from_config_dict(target)
    executed = await summary_service.execute_job(job_config)
    if not executed:
        return {"status": "skipped", "message": "任务正在执行中，本次触发已跳过"}
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
    try:
        deleted = memory_service.clear_task("summary", f"summary-{decoded}")
    except Exception as e:
        logger.error(f"清空任务记忆失败（{decoded}）: {e}")
        raise HTTPException(500, "清空任务记忆失败，请稍后重试") from e
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
    # 摘要失败占位行（summary=""）只承载消费标记，不计入统计与注入估算（B1 读取侧适配）
    rows = [e for e in rows if e.summary]
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
