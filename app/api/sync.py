"""
同步相关API
"""

import ast
import asyncio
import json
import time
import traceback
from collections.abc import AsyncGenerator
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sse_starlette.sse import EventSourceResponse

from ..core.logging import logger, new_retry_sync_run_id, sync_log_context
from ..core.security import security_manager
from ..models.sync import CustomItem
from ..services.custom import custom_sync_service
from ..services.sync_service import sync_service
from ..utils.bgm_poster_service import get_poster_urls, normalize_subject_id
from ..utils.data_util import extract_plex_json
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api", tags=["sync"])
# 无前缀的同步接口（包含媒体服务器接口）
root_router = APIRouter(tags=["sync"])


@root_router.post("/Custom/{webhook_key}", status_code=202)
async def custom_sync(
    item: CustomItem,
    response: Response,
    webhook_key: str,
    source: str = "custom",
    async_mode: bool = True,
):
    """自定义同步接口（带密钥）"""
    return await _handle_custom_sync(item, response, webhook_key, source, async_mode)


@root_router.post("/Custom", status_code=202)
async def custom_sync_no_key(
    item: CustomItem,
    response: Response,
    source: str = "custom",
    async_mode: bool = True,
):
    """自定义同步接口（无密钥）"""
    return await _handle_custom_sync(item, response, "", source, async_mode)


async def _handle_custom_sync(
    item: CustomItem,
    response: Response,
    webhook_key: str = "",
    source: str = "custom",
    async_mode: bool = True,
):
    """处理自定义同步请求的内部函数

    鉴权在 API 层完成，同步逻辑委托给 custom_sync_service。
    """
    if not await _verify_webhook_auth(webhook_key):
        logger.warning("Custom webhook 认证失败，无效的 key")
        response.status_code = 401
        return {"status": "error", "message": "认证失败"}
    try:
        if async_mode:
            # 异步处理模式
            task_id = await custom_sync_service.sync_item_async(item, source)
            response.status_code = 202  # Accepted
            return {
                "status": "accepted",
                "message": "同步任务已提交到异步队列",
                "task_id": task_id,
                "check_url": f"/api/sync/status/{task_id}",
            }
        else:
            # 同步处理模式（保持向后兼容）
            result = custom_sync_service.sync_item(item, source)

            # 根据结果设置响应状态码
            if result.status == "error":
                response.status_code = 500
            elif result.status == "ignored":
                response.status_code = 200

            return result.model_dump()
    except Exception as e:
        logger.error(f"自定义同步API处理出错: {e}")
        response.status_code = 500
        return {"status": "error", "message": f"处理失败: {str(e)}"}


@router.get("/sync/status/{task_id}")
async def get_sync_status(
    task_id: str, current_user: dict = Depends(get_current_user_flexible)
):
    """获取同步任务状态"""
    try:
        task_status = sync_service.get_sync_task_status(task_id)

        if not task_status:
            raise HTTPException(status_code=404, detail="任务不存在")

        return {"status": "success", "data": task_status}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取同步任务状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取状态失败: {str(e)}")


@router.get("/sync/tasks")
async def list_sync_tasks(current_user: dict = Depends(get_current_user_flexible)):
    """获取所有同步任务列表"""
    try:
        sync_service.cleanup_old_tasks()
        tasks = sync_service.get_all_sync_tasks()
        return {"status": "success", "data": {"tasks": tasks, "total": len(tasks)}}
    except Exception as e:
        logger.error(f"获取同步任务列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取任务列表失败: {str(e)}")


@router.post("/test-sync")
async def test_sync(
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
    async_mode: bool = Query(None),
):
    """测试同步功能"""
    start_time = time.time()

    try:
        data = await request.json()

        # 验证必需字段
        if not data.get("title"):
            raise HTTPException(status_code=400, detail="标题不能为空")

        media_type = (data.get("media_type") or "episode").lower()
        if media_type not in ("episode", "movie", "ova", "oad", "real_action"):
            media_type = "episode"

        # 创建测试项目
        test_item = CustomItem(
            media_type=media_type,
            title=data.get("title", ""),
            ori_title=data.get("ori_title") or None,
            season=int(data.get("season", 1)),
            episode=int(data.get("episode", 1)),
            release_date=data.get("release_date") or "",
            user_name=data.get("user_name", "test_user"),
            source=data.get("source", "test"),
        )

        # 智能判断处理模式
        # 如果没有明确指定async_mode，则根据User-Agent判断
        if async_mode is None:
            user_agent = request.headers.get("user-agent", "").lower()
            # 如果是浏览器请求（配置页面），默认使用同步模式以便立即显示结果
            is_browser = any(
                browser in user_agent
                for browser in ["mozilla", "chrome", "safari", "edge"]
            )
            async_mode = not is_browser

        if async_mode:
            # 异步处理模式
            task_id = await sync_service.sync_custom_item_async(
                test_item, source="test"
            )

            # 计算提交耗时
            elapsed_time = round(time.time() - start_time, 3)

            return {
                "status": "accepted",
                "message": "测试同步任务已提交到异步队列",
                "task_id": task_id,
                "check_url": f"/api/sync/status/{task_id}",
                "elapsed_time": f"{elapsed_time}秒",
                "test_info": {
                    "title": test_item.title,
                    "episode": f"S{test_item.season:02d}E{test_item.episode:02d}",
                    "user": test_item.user_name,
                    "media_type": test_item.media_type,
                },
            }
        else:
            # 同步处理模式（配置页面使用，立即返回结果）
            result = sync_service.sync_custom_item(test_item, source="test")

            # 计算耗时
            elapsed_time = round(time.time() - start_time, 2)

            # 精简返回信息
            response_data = result.model_dump()
            response_data["elapsed_time"] = f"{elapsed_time}秒"
            response_data["test_info"] = {
                "title": test_item.title,
                "episode": f"S{test_item.season:02d}E{test_item.episode:02d}",
                "user": test_item.user_name,
                "media_type": test_item.media_type,
            }

            return response_data

    except HTTPException:
        raise
    except Exception as e:
        elapsed_time = round(time.time() - start_time, 2)
        error_msg = f"测试失败: {str(e)}"
        raise HTTPException(status_code=500, detail=error_msg)


@router.post("/test-match")
async def test_match(
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
):
    """测试匹配过程（不执行实际同步，只返回三段式匹配追踪详情）"""
    try:
        data = await request.json()

        if not data.get("title"):
            raise HTTPException(status_code=400, detail="标题不能为空")

        media_type = (data.get("media_type") or "episode").lower()
        if media_type not in ("episode", "movie", "ova", "oad", "real_action"):
            media_type = "episode"

        test_item = CustomItem(
            media_type=media_type,
            title=data.get("title", ""),
            ori_title=data.get("ori_title") or None,
            season=int(data.get("season", 1)),
            episode=int(data.get("episode", 1)),
            release_date=data.get("release_date") or "",
            user_name=data.get("user_name", "test_user"),
            source="test-match",
        )

        result = sync_service.test_match(test_item)
        return {"status": "success", "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"测试匹配失败: {e}")
        raise HTTPException(status_code=500, detail=f"测试匹配失败: {str(e)}")


@router.get("/records")
async def get_sync_records(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status: Optional[str] = None,
    user_name: Optional[str] = None,
    source: Optional[str] = None,
    source_prefix: Optional[str] = None,
    skip_count: bool = Query(False),
    include_poster: bool = Query(False),
    current_user: dict = Depends(get_current_user_flexible),
):
    """获取同步记录"""
    try:
        result = sync_service.get_sync_records(
            limit=limit,
            offset=offset,
            status=status,
            user_name=user_name,
            source=source,
            source_prefix=source_prefix,
            skip_count=skip_count,
        )

        if include_poster and result.get("records"):
            subject_ids = [
                sid
                for r in result["records"]
                if (sid := normalize_subject_id(r.get("subject_id"))) is not None
            ]
            poster_map = await get_poster_urls(subject_ids)
            for record in result["records"]:
                sid = normalize_subject_id(record.get("subject_id"))
                record["poster_url"] = poster_map.get(sid) if sid else None

        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"获取同步记录失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取同步记录失败: {str(e)}")


@router.get("/match-records")
async def get_match_records(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = None,
    match_method: Optional[str] = None,
    match_platform: Optional[str] = None,
    current_user: dict = Depends(get_current_user_flexible),
):
    """获取匹配记录列表（含匹配追踪字段）"""
    try:
        offset = (page - 1) * limit
        result = sync_service.get_match_records(
            limit=limit,
            offset=offset,
            status=status,
            match_method=match_method,
            match_platform=match_platform,
        )
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"获取匹配记录失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取匹配记录失败: {str(e)}")


@router.get("/match-records/{record_id}/trace")
async def get_match_trace(
    record_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
):
    """获取单条匹配记录的完整匹配过程详情"""
    try:
        record = sync_service.get_sync_record_by_id(record_id)
        if not record:
            raise HTTPException(status_code=404, detail="记录不存在")

        # 解析 match_trace JSON
        trace = None
        trace_str = record.get("match_trace", "")
        if trace_str:
            try:
                trace = json.loads(trace_str)
            except (json.JSONDecodeError, TypeError):
                trace = None

        return {"status": "success", "data": {"record": record, "trace": trace}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取匹配详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取匹配详情失败: {str(e)}")


# ===== 待确认候选（候选沉淀 + 确认 UI） =====


@router.get("/pending-candidates")
async def get_pending_candidates(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = None,
    current_user: dict = Depends(get_current_user_flexible),
):
    """获取待确认候选列表"""
    try:
        offset = (page - 1) * limit
        result = sync_service.get_pending_candidates(
            limit=limit, offset=offset, status=status
        )
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"获取待确认候选失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取待确认候选失败: {str(e)}")


@router.get("/pending-candidates/{candidate_id}")
async def get_pending_candidate_detail(
    candidate_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
):
    """获取单条待确认候选详情（含完整 trace）"""
    try:
        record = sync_service.get_pending_candidate_by_id(candidate_id)
        if not record:
            raise HTTPException(status_code=404, detail="候选记录不存在")

        candidates = []
        cand_str = record.get("candidates_json", "") or "[]"
        try:
            candidates = json.loads(cand_str)
        except (json.JSONDecodeError, TypeError):
            candidates = []

        trace = None
        trace_str = record.get("trace_json", "") or "{}"
        try:
            trace = json.loads(trace_str) if trace_str else None
        except (json.JSONDecodeError, TypeError):
            trace = None

        return {
            "status": "success",
            "data": {"record": record, "candidates": candidates, "trace": trace},
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取待确认候选详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取待确认候选详情失败: {str(e)}")


@router.post("/pending-candidates/{candidate_id}/confirm")
async def confirm_pending_candidate(
    candidate_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
):
    """确认待确认候选：写入自定义映射并标记为已确认"""
    try:
        body = await request.json()
        subject_id = str(body.get("subject_id", "")).strip()
        if not subject_id:
            raise HTTPException(status_code=400, detail="subject_id 不能为空")

        success, message = sync_service.confirm_pending_candidate(
            candidate_id, subject_id
        )
        if not success:
            raise HTTPException(status_code=400, detail=message)
        return {"status": "success", "message": message}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"确认待确认候选失败: {e}")
        raise HTTPException(status_code=500, detail=f"确认失败: {str(e)}")


@router.post("/pending-candidates/{candidate_id}/reject")
async def reject_pending_candidate(
    candidate_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
):
    """拒绝（忽略）待确认候选"""
    try:
        success, message = sync_service.reject_pending_candidate(candidate_id)
        if not success:
            raise HTTPException(status_code=400, detail=message)
        return {"status": "success", "message": message}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"拒绝待确认候选失败: {e}")
        raise HTTPException(status_code=500, detail=f"拒绝失败: {str(e)}")


@router.delete("/pending-candidates/{candidate_id}")
async def delete_pending_candidate(
    candidate_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
):
    """删除待确认候选"""
    try:
        success, message = sync_service.delete_pending_candidate(candidate_id)
        if not success:
            raise HTTPException(status_code=404, detail=message)
        return {"status": "success", "message": message}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除待确认候选失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@router.get("/records/{record_id}")
async def get_sync_record(
    record_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
):
    """获取单个同步记录详情"""
    try:
        result = sync_service.get_sync_record_by_id(record_id)

        if not result:
            raise HTTPException(status_code=404, detail="记录不存在")

        return {"status": "success", "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取同步记录详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取同步记录详情失败: {str(e)}")


@router.post("/records/{record_id}/retry")
async def retry_sync_record(
    record_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
):
    """重试同步记录"""
    try:
        # 获取原始记录
        record = sync_service.get_sync_record_by_id(record_id)

        if not record:
            raise HTTPException(status_code=404, detail="记录不存在")

        # 只允许重试失败的记录
        if record.get("status") != "error":
            raise HTTPException(status_code=400, detail="只能重试失败的记录")

        # 获取原始来源并添加重试标记
        original_source = record.get("source", "custom")
        retry_source = (
            f"retry-{original_source}"  # 组合来源，如 "retry-plex", "retry-emby" 等
        )

        # 重新构建同步项目
        retry_item = _build_retry_item(record, retry_source)

        logger.info(
            f"重试同步记录 {record_id}: {retry_item.title} S{retry_item.season:02d}E{retry_item.episode:02d}, 原始来源: {original_source}, 重试来源: {retry_source}"
        )

        # 执行同步
        with sync_log_context(new_retry_sync_run_id(record_id)):
            result = sync_service.sync_custom_item(retry_item, source=retry_source)

        # 如果重试成功，更新原记录的状态
        if result.status == "success":
            sync_service.update_sync_record_status(
                record_id=record_id,
                status="retried",  # 标记为已重试
                message=f"已重试成功: {result.message}",
            )
        elif result.status == "ignored":
            sync_service.update_sync_record_status(
                record_id=record_id,
                status="retried",
                message=f"重试被忽略: {result.message}",
            )
        # 如果重试仍然失败，保持原状态不变

        return {"status": "success", "message": "重试完成", "data": result.model_dump()}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重试同步记录失败: {e}")
        raise HTTPException(status_code=500, detail=f"重试失败: {str(e)}")


def _build_retry_item(record: dict, retry_source: str) -> CustomItem:
    """从同步记录构建重试用的 CustomItem"""
    retry_media = (record.get("media_type") or "episode").lower()
    if retry_media not in ("episode", "movie"):
        retry_media = "episode"
    return CustomItem(
        media_type=retry_media,
        title=record.get("title", ""),
        ori_title=record.get("ori_title") or None,
        season=record.get("season", 1),
        episode=record.get("episode", 1),
        release_date="",
        user_name=record.get("user_name", ""),
        source=retry_source,
    )


@router.get("/records/{record_id}/retry/stream")
async def retry_sync_record_stream(
    record_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
):
    """重试同步记录（SSE 流式推送 debug 日志）

    事件类型：
    - start: 重试开始，包含记录基本信息
    - log: 实时日志行，包含 level 和 line
    - done: 重试完成，包含最终状态和消息
    - error: 重试过程异常
    - ping: 心跳保活
    """
    # 获取原始记录
    record = sync_service.get_sync_record_by_id(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    if record.get("status") != "error":
        raise HTTPException(status_code=400, detail="只能重试失败的记录")

    original_source = record.get("source", "custom")
    retry_source = f"retry-{original_source}"
    retry_item = _build_retry_item(record, retry_source)

    logger.info(
        f"重试同步记录 {record_id}（流式）: {retry_item.title} S{retry_item.season:02d}E{retry_item.episode:02d}, 原始来源: {original_source}, 重试来源: {retry_source}"
    )

    async def event_generator() -> AsyncGenerator[dict[str, Any], None]:
        loop = asyncio.get_event_loop()
        log_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

        def log_listener(log_line: str, level: str) -> None:
            # 线程安全地投递日志到事件循环的队列
            loop.call_soon_threadsafe(log_queue.put_nowait, (log_line, level))

        # 注册日志监听器
        logger.add_listener(log_listener)

        try:
            # 推送开始事件
            yield {
                "event": "start",
                "data": json.dumps(
                    {
                        "record_id": record_id,
                        "title": retry_item.title,
                        "season": retry_item.season,
                        "episode": retry_item.episode,
                        "source": retry_source,
                    },
                    ensure_ascii=False,
                ),
            }

            # 在线程中执行同步任务（sync_custom_item 是同步方法，会阻塞事件循环）
            def _run_retry() -> Any:
                with sync_log_context(new_retry_sync_run_id(record_id)):
                    return sync_service.sync_custom_item(
                        retry_item, source=retry_source
                    )

            task = asyncio.create_task(asyncio.to_thread(_run_retry))

            # 等待任务完成，同时实时推送日志
            while not task.done():
                try:
                    log_line, level = await asyncio.wait_for(
                        log_queue.get(), timeout=1.0
                    )
                    yield {
                        "event": "log",
                        "data": json.dumps(
                            {"line": log_line, "level": level}, ensure_ascii=False
                        ),
                    }
                except asyncio.TimeoutError:
                    # 超时无日志，发送心跳保持连接
                    yield {"event": "ping", "data": ""}

            # 推送任务完成前残留的日志
            while not log_queue.empty():
                log_line, level = log_queue.get_nowait()
                yield {
                    "event": "log",
                    "data": json.dumps(
                        {"line": log_line, "level": level}, ensure_ascii=False
                    ),
                }

            # 获取同步结果
            result = await task

            # 更新原记录状态
            if result.status == "success":
                sync_service.update_sync_record_status(
                    record_id=record_id,
                    status="retried",
                    message=f"已重试成功: {result.message}",
                )
            elif result.status == "ignored":
                sync_service.update_sync_record_status(
                    record_id=record_id,
                    status="retried",
                    message=f"重试被忽略: {result.message}",
                )

            # 推送完成事件
            yield {
                "event": "done",
                "data": json.dumps(
                    {
                        "status": result.status,
                        "message": result.message,
                        "data": result.data,
                    },
                    ensure_ascii=False,
                ),
            }

        except Exception as e:
            logger.error(f"重试同步记录失败（流式）: {e}")
            yield {
                "event": "error",
                "data": json.dumps(
                    {"message": f"重试失败: {str(e)}"}, ensure_ascii=False
                ),
            }
        finally:
            logger.remove_listener(log_listener)

    return EventSourceResponse(event_generator())


@router.get("/stats")
async def get_sync_stats(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
):
    """获取同步统计信息"""
    try:
        result = sync_service.get_sync_stats()

        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"获取统计信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计信息失败: {str(e)}")


@router.get("/stats/heatmap")
async def get_heatmap_stats(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
):
    """获取热力图数据（过去365天每天同步数）"""
    try:
        result = sync_service.get_heatmap_stats()
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"获取热力图数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取热力图数据失败: {str(e)}")


# ========== 媒体服务器专用接口 ==========


async def _verify_webhook_auth(webhook_key: str) -> bool:
    """验证webhook认证"""
    return security_manager.verify_webhook_key(webhook_key)


async def _dispatch_media_server_webhook(
    request: Request,
    webhook_key: str,
    *,
    source_name: str,
    parse_body,
    async_fn,
    sync_fn,
):
    """媒体服务器 webhook 通用分发

    统一处理鉴权、body 解析、异步提交+同步回退、异常包装。

    Args:
        request: FastAPI Request
        webhook_key: webhook 密钥（空字符串表示无密钥路由）
        source_name: 源名称（如 "Plex"），用于日志和响应消息
        parse_body: async callable(request) -> dict，解析请求体
        async_fn: async callable(data) -> str，异步同步方法
        sync_fn: callable(data) -> SyncResponse，同步回退方法
    """
    if not await _verify_webhook_auth(webhook_key):
        logger.warning(f"{source_name} webhook 认证失败，无效的 key")
        return Response(
            content='{"status": "error", "message": "认证失败"}',
            status_code=401,
            media_type="application/json",
        )

    try:
        data = await parse_body(request)

        # 异步调用同步服务（不阻塞webhook响应）
        try:
            task_id = await async_fn(data)
            logger.info(f"{source_name} webhook已提交异步任务: {task_id}")
            return {
                "status": "accepted",
                "message": f"{source_name}同步请求已接收",
                "task_id": task_id,
            }
        except Exception as sync_error:
            logger.error(f"{source_name}同步服务调用失败: {sync_error}")
            # 如果异步提交失败，回退到同步模式
            try:
                sync_fn(data)
                return {
                    "status": "accepted",
                    "message": f"{source_name}同步请求已接收（同步模式）",
                }
            except Exception as fallback_error:
                logger.error(f"{source_name}同步回退模式也失败: {fallback_error}")
                return {
                    "status": "error",
                    "message": f"{source_name}同步处理失败: {fallback_error}",
                }

    except Exception as e:
        logger.error(f"{source_name}同步处理出错: {e}")
        return {"status": "error", "message": f"处理失败: {str(e)}"}


# ===== Plex =====


async def _parse_plex_body(request: Request) -> dict:
    json_str = await request.body()
    return json.loads(extract_plex_json(json_str))


async def _handle_plex_sync(
    plex_request: Request, webhook_key: str = ""
) -> dict[str, Any]:
    """处理Plex同步请求的内部函数"""
    return await _dispatch_media_server_webhook(
        plex_request,
        webhook_key,
        source_name="Plex",
        parse_body=_parse_plex_body,
        async_fn=sync_service.sync_plex_item_async,
        sync_fn=sync_service.sync_plex_item,
    )


@root_router.post("/Plex/{webhook_key}")
async def plex_sync(plex_request: Request, webhook_key: str) -> dict[str, Any]:
    """Plex同步接口（带密钥）"""
    return await _handle_plex_sync(plex_request, webhook_key)


@root_router.post("/Plex")
async def plex_sync_no_key(plex_request: Request) -> dict[str, Any]:
    """Plex同步接口（无密钥）"""
    return await _handle_plex_sync(plex_request, "")


# ===== Emby =====


async def _parse_emby_body(request: Request) -> dict:
    body = await request.body()
    body_str = body.decode("utf-8")

    if body_str.startswith("{") and body_str.endswith("}"):
        try:
            return json.loads(body_str)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(body_str)
            except (SyntaxError, ValueError) as e:
                logger.error(f"无法解析Emby请求数据: {e}")
                raise ValueError(f"数据格式错误: {str(e)}")
    else:
        logger.error(f"Emby请求数据格式无效: {body_str[:100]}...")
        raise ValueError("无效的请求格式")


async def _handle_emby_sync(
    emby_request: Request, webhook_key: str = ""
) -> dict[str, Any]:
    """处理Emby同步请求的内部函数"""
    try:
        return await _dispatch_media_server_webhook(
            emby_request,
            webhook_key,
            source_name="Emby",
            parse_body=_parse_emby_body,
            async_fn=sync_service.sync_emby_item_async,
            sync_fn=sync_service.sync_emby_item,
        )
    except ValueError as e:
        # _parse_emby_body 可能抛出 ValueError（格式错误），需单独处理
        return {"status": "error", "message": str(e)}
    except Exception as e:
        logger.error(f"Emby同步处理出错: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error", "message": f"处理失败: {str(e)}"}


@root_router.post("/Emby/{webhook_key}")
async def emby_sync(emby_request: Request, webhook_key: str) -> dict[str, Any]:
    """Emby同步接口（带密钥）"""
    return await _handle_emby_sync(emby_request, webhook_key)


@root_router.post("/Emby")
async def emby_sync_no_key(emby_request: Request) -> dict[str, Any]:
    """Emby同步接口（无密钥）"""
    return await _handle_emby_sync(emby_request, "")


# ===== Jellyfin =====


async def _parse_jellyfin_body(request: Request) -> dict:
    json_str = await request.body()
    return json.loads(json_str)


async def _handle_jellyfin_sync(
    jellyfin_request: Request, webhook_key: str = ""
) -> dict[str, Any]:
    """处理Jellyfin同步请求的内部函数"""
    return await _dispatch_media_server_webhook(
        jellyfin_request,
        webhook_key,
        source_name="Jellyfin",
        parse_body=_parse_jellyfin_body,
        async_fn=sync_service.sync_jellyfin_item_async,
        sync_fn=sync_service.sync_jellyfin_item,
    )


@root_router.post("/Jellyfin/{webhook_key}")
async def jellyfin_sync(jellyfin_request: Request, webhook_key: str) -> dict[str, Any]:
    """Jellyfin同步接口（带密钥）"""
    return await _handle_jellyfin_sync(jellyfin_request, webhook_key)


@root_router.post("/Jellyfin")
async def jellyfin_sync_no_key(jellyfin_request: Request) -> dict[str, Any]:
    """Jellyfin同步接口（无密钥）"""
    return await _handle_jellyfin_sync(jellyfin_request, "")
