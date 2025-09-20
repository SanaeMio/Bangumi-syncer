"""
同步相关API
"""
import ast
import json
import time
import traceback
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, Response, HTTPException, status, Depends, Query
from fastapi.responses import JSONResponse

from ..models.sync import CustomItem, SyncResponse
from ..services.sync_service import sync_service
from ..core.logging import logger
from ..core.database import database_manager
from ..utils.data_util import extract_plex_json
from .deps import get_current_user_flexible


router = APIRouter(prefix="/api", tags=["sync"])
# 无前缀的同步接口（包含媒体服务器接口）
root_router = APIRouter(tags=["sync"])


@root_router.post("/Custom", status_code=200)
async def custom_sync(item: CustomItem, response: Response, source: str = "custom"):
    """自定义同步接口"""
    try:
        # 调用同步服务
        result = sync_service.sync_custom_item(item, source)
        
        # 根据结果设置响应状态码
        if result.status == "error":
            response.status_code = 500
        elif result.status == "ignored":
            response.status_code = 200
        
        return result.dict()
    except Exception as e:
        logger.error(f'自定义同步API处理出错: {e}')
        response.status_code = 500
        return {"status": "error", "message": f"处理失败: {str(e)}"}


@router.post("/test-sync")
async def test_sync(request: Request, current_user: dict = Depends(get_current_user_flexible)):
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
            source=data.get("source", "test")
        )
        
        # 执行同步测试
        result = sync_service.sync_custom_item(test_item, source="test")
        
        # 计算耗时
        elapsed_time = round(time.time() - start_time, 2)
        
        # 精简返回信息
        response_data = result.dict()
        response_data["elapsed_time"] = f"{elapsed_time}秒"
        response_data["test_info"] = {
            "title": test_item.title,
            "episode": f"S{test_item.season:02d}E{test_item.episode:02d}",
            "user": test_item.user_name
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
    current_user: dict = Depends(get_current_user_flexible)
):
    """获取同步记录"""
    try:
        result = database_manager.get_sync_records(
            limit=limit,
            offset=offset,
            status=status,
            user_name=user_name,
            source=source
        )
        
        return {
            "status": "success",
            "data": result
        }
    except Exception as e:
        logger.error(f"获取同步记录失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取同步记录失败: {str(e)}")


@router.get("/records/{record_id}")
async def get_sync_record(
    record_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible)
):
    """获取单个同步记录详情"""
    try:
        result = database_manager.get_sync_record_by_id(record_id)
        
        if not result:
            raise HTTPException(status_code=404, detail="记录不存在")
        
        return {
            "status": "success",
            "data": result
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取同步记录详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取同步记录详情失败: {str(e)}")


@router.get("/stats")
async def get_sync_stats(
    request: Request,
    current_user: dict = Depends(get_current_user_flexible)
):
    """获取同步统计信息"""
    try:
        result = database_manager.get_sync_stats()
        
        return {
            "status": "success",
            "data": result
        }
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
        
        # 调用同步服务
        result = sync_service.sync_plex_item(plex_data)
        return result.dict()
    except Exception as e:
        logger.error(f'Plex同步处理出错: {e}')
        return {"status": "error", "message": f"处理失败: {str(e)}"}


@root_router.post("/Emby")
async def emby_sync(emby_request: Request):
    """Emby同步接口（实际webhook端点）"""
    try:
        # 获取请求内容
        body = await emby_request.body()
        body_str = body.decode('utf-8')
        
        # 检查内容格式并进行相应处理
        if body_str.startswith('{') and body_str.endswith('}'):
            try:
                # 尝试作为JSON解析
                emby_data = json.loads(body_str)
            except json.JSONDecodeError:
                # 如果JSON解析失败，尝试作为Python字典字符串解析
                try:
                    emby_data = ast.literal_eval(body_str)
                except (SyntaxError, ValueError) as e:
                    logger.error(f'无法解析Emby请求数据: {e}')
                    return {"status": "error", "message": f"数据格式错误: {str(e)}"}
        else:
            logger.error(f'Emby请求数据格式无效: {body_str[:100]}...')
            return {"status": "error", "message": "无效的请求格式"}
        
        # 调用同步服务
        result = sync_service.sync_emby_item(emby_data)
        return result.dict()
    except Exception as e:
        logger.error(f'Emby同步处理出错: {e}')
        logger.error(traceback.format_exc())
        return {"status": "error", "message": f"处理失败: {str(e)}"}





@root_router.post("/Jellyfin")
async def jellyfin_sync(jellyfin_request: Request):
    """Jellyfin同步接口（实际webhook端点）"""
    try:
        json_str = await jellyfin_request.body()
        jellyfin_data = json.loads(json_str)
        
        # 调用同步服务
        result = sync_service.sync_jellyfin_item(jellyfin_data)
        return result.dict()
    except Exception as e:
        logger.error(f'Jellyfin同步处理出错: {e}')
        return {"status": "error", "message": f"处理失败: {str(e)}"} 