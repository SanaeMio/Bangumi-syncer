"""
同步相关API
"""

import ast
import json
import time
import traceback
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..core.database import database_manager
from ..core.logging import logger
from ..models.sync import CustomItem
from ..services.sync_service import sync_service
from ..utils.data_util import extract_plex_json
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api", tags=["sync"])
# 无前缀的同步接口（包含媒体服务器接口）
root_router = APIRouter(tags=["sync"])


@root_router.post("/Custom", status_code=202)
async def custom_sync(
    item: CustomItem,
    response: Response,
    source: str = "custom",
    async_mode: bool = True,
):
    """自定义同步接口"""
    try:
        if async_mode:
            # 异步处理模式
            task_id = await sync_service.sync_custom_item_async(item, source)
            response.status_code = 202  # Accepted
            return {
                "status": "accepted",
                "message": "同步任务已提交到异步队列",
                "task_id": task_id,
                "check_url": f"/api/sync/status/{task_id}",
            }
        else:
            # 同步处理模式（保持向后兼容）
            result = sync_service.sync_custom_item(item, source)

            # 根据结果设置响应状态码
            if result.status == "error":
                response.status_code = 500
            elif result.status == "ignored":
                response.status_code = 200

            return result.dict()
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
        # 清理旧任务
        sync_service.cleanup_old_tasks()

        tasks = sync_service._sync_tasks
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

        # 创建测试项目
        test_item = CustomItem(
            media_type="episode",
            title=data.get("title", ""),
            ori_title=data.get("ori_title", ""),
            season=data.get("season", 1),
            episode=data.get("episode", 1),
            release_date=data.get("release_date", ""),
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
            }

            return response_data

    except HTTPException:
        raise
    except Exception as e:
        elapsed_time = round(time.time() - start_time, 2)
        error_msg = f"测试失败: {str(e)}"
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/records")
async def get_sync_records(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status: Optional[str] = None,
    user_name: Optional[str] = None,
    source: Optional[str] = None,
    source_prefix: Optional[str] = None,
    current_user: dict = Depends(get_current_user_flexible),
):
    """获取同步记录"""
    try:
        result = database_manager.get_sync_records(
            limit=limit,
            offset=offset,
            status=status,
            user_name=user_name,
            source=source,
            source_prefix=source_prefix,
        )

        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"获取同步记录失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取同步记录失败: {str(e)}")


@router.get("/sync/history")
async def get_sync_history(
    limit: int = Query(20, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    source_prefix: str = Query("trakt"),
):
    """获取同步历史（前端兼容接口）

    注意：此端点为前端 Trakt 配置页面提供兼容接口，
    直接返回前端期望的数据格式，不包含状态包装。
    """
    try:
        result = database_manager.get_sync_records(
            limit=limit,
            offset=offset,
            source_prefix=source_prefix,
        )

        # 前端期望直接返回数据库结果（包含 records 字段）
        # 而不是 {"status": "success", "data": result} 格式
        return result
    except Exception as e:
        logger.error(f"获取同步历史失败: {e}")
        # 返回空结果而不是抛出异常，避免前端报错
        return {"records": [], "total": 0, "limit": limit, "offset": offset}


@router.get("/records/{record_id}")
async def get_sync_record(
    record_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
):
    """获取单个同步记录详情"""
    try:
        result = database_manager.get_sync_record_by_id(record_id)

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
        record = database_manager.get_sync_record_by_id(record_id)

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
        retry_item = CustomItem(
            media_type="episode",
            title=record.get("title", ""),
            ori_title=record.get("ori_title", ""),
            season=record.get("season", 1),
            episode=record.get("episode", 1),
            release_date="",
            user_name=record.get("user_name", ""),
            source=retry_source,  # 使用组合来源标识这是重试记录
        )

        logger.info(
            f"重试同步记录 {record_id}: {retry_item.title} S{retry_item.season:02d}E{retry_item.episode:02d}, 原始来源: {original_source}, 重试来源: {retry_source}"
        )

        # 执行同步
        result = sync_service.sync_custom_item(retry_item, source=retry_source)

        # 如果重试成功，更新原记录的状态
        if result.status == "success":
            database_manager.update_sync_record_status(
                record_id=record_id,
                status="retried",  # 标记为已重试
                message=f"已重试成功: {result.message}",
            )
        elif result.status == "ignored":
            database_manager.update_sync_record_status(
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


@router.get("/stats")
async def get_sync_stats(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
):
    """获取同步统计信息"""
    try:
        result = database_manager.get_sync_stats()

        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"获取统计信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计信息失败: {str(e)}")


# ========== 媒体服务器专用接口 ==========


@root_router.post("/Plex")
async def plex_sync(plex_request: Request):
    """Plex同步接口（实际webhook端点）"""
    try:
        json_str = await plex_request.body()
        plex_data = json.loads(extract_plex_json(json_str))

        # 异步调用同步服务（不阻塞webhook响应）
        # 对于webhook，我们立即返回成功响应，同步在后台进行
        try:
            # 提交到异步队列处理
            task_id = await sync_service.sync_plex_item_async(plex_data)
            logger.info(f"Plex webhook已提交异步任务: {task_id}")
            # 对于webhook，立即返回成功响应
            return {
                "status": "accepted",
                "message": "Plex同步请求已接收",
                "task_id": task_id,
            }
        except Exception as sync_error:
            logger.error(f"Plex同步服务调用失败: {sync_error}")
            # 如果异步提交失败，回退到同步模式
            try:
                sync_service.sync_plex_item(plex_data)
                return {
                    "status": "accepted",
                    "message": "Plex同步请求已接收（同步模式）",
                }
            except Exception as fallback_error:
                logger.error(f"Plex同步回退模式也失败: {fallback_error}")
                return {
                    "status": "accepted",
                    "message": "Plex同步请求已接收，但处理时出现错误",
                }

    except Exception as e:
        logger.error(f"Plex同步处理出错: {e}")
        return {"status": "error", "message": f"处理失败: {str(e)}"}


@root_router.post("/Emby")
async def emby_sync(emby_request: Request):
    """Emby同步接口（实际webhook端点）"""
    try:
        # 获取请求内容
        body = await emby_request.body()
        body_str = body.decode("utf-8")

        # 检查内容格式并进行相应处理
        if body_str.startswith("{") and body_str.endswith("}"):
            try:
                # 尝试作为JSON解析
                emby_data = json.loads(body_str)
            except json.JSONDecodeError:
                # 如果JSON解析失败，尝试作为Python字典字符串解析
                try:
                    emby_data = ast.literal_eval(body_str)
                except (SyntaxError, ValueError) as e:
                    logger.error(f"无法解析Emby请求数据: {e}")
                    return {"status": "error", "message": f"数据格式错误: {str(e)}"}
        else:
            logger.error(f"Emby请求数据格式无效: {body_str[:100]}...")
            return {"status": "error", "message": "无效的请求格式"}

        # 异步调用同步服务（不阻塞webhook响应）
        try:
            # 提交到异步队列处理
            task_id = await sync_service.sync_emby_item_async(emby_data)
            logger.info(f"Emby webhook已提交异步任务: {task_id}")
            # 对于webhook，立即返回成功响应
            return {
                "status": "accepted",
                "message": "Emby同步请求已接收",
                "task_id": task_id,
            }
        except Exception as sync_error:
            logger.error(f"Emby同步服务调用失败: {sync_error}")
            # 如果异步提交失败，回退到同步模式
            try:
                sync_service.sync_emby_item(emby_data)
                return {
                    "status": "accepted",
                    "message": "Emby同步请求已接收（同步模式）",
                }
            except Exception as fallback_error:
                logger.error(f"Emby同步回退模式也失败: {fallback_error}")
                return {
                    "status": "accepted",
                    "message": "Emby同步请求已接收，但处理时出现错误",
                }
    except Exception as e:
        logger.error(f"Emby同步处理出错: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error", "message": f"处理失败: {str(e)}"}


@root_router.post("/Jellyfin")
async def jellyfin_sync(jellyfin_request: Request):
    """Jellyfin同步接口（实际webhook端点）"""
    try:
        json_str = await jellyfin_request.body()
        jellyfin_data = json.loads(json_str)

        # 异步调用同步服务（不阻塞webhook响应）
        # 对于webhook，我们立即返回成功响应，同步在后台进行
        try:
            # 提交到异步队列处理
            task_id = await sync_service.sync_jellyfin_item_async(jellyfin_data)
            logger.info(f"Jellyfin webhook已提交异步任务: {task_id}")
            # 对于webhook，立即返回成功响应
            return {
                "status": "accepted",
                "message": "Jellyfin同步请求已接收",
                "task_id": task_id,
            }
        except Exception as sync_error:
            logger.error(f"Jellyfin同步服务调用失败: {sync_error}")
            # 如果异步提交失败，回退到同步模式
            try:
                sync_service.sync_jellyfin_item(jellyfin_data)
                return {
                    "status": "accepted",
                    "message": "Jellyfin同步请求已接收（同步模式）",
                }
            except Exception as fallback_error:
                logger.error(f"Jellyfin同步回退模式也失败: {fallback_error}")
                return {
                    "status": "accepted",
                    "message": "Jellyfin同步请求已接收，但处理时出现错误",
                }

    except Exception as e:
        logger.error(f"Jellyfin同步处理出错: {e}")
        return {"status": "error", "message": f"处理失败: {str(e)}"}
