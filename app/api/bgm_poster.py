"""Bangumi 条目封面（仪表板时间线海报）API。"""

from fastapi import APIRouter, Depends, HTTPException

from ..core.logging import logger
from ..utils.bgm_poster_service import get_poster_urls
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/bgm", tags=["bgm"])


@router.get("/subjects/{subject_id}/poster")
async def get_subject_poster(
    subject_id: int,
    current_user: dict = Depends(get_current_user_flexible),
):
    """返回条目封面图 URL（服务端取元数据，可选图片 CDN 反代改写）。"""
    if subject_id < 1:
        raise HTTPException(status_code=400, detail="无效的条目 ID")

    try:
        urls = await get_poster_urls([subject_id])
    except Exception as e:
        logger.warning("获取条目 %s 封面失败: %s", subject_id, e)
        raise HTTPException(status_code=502, detail="获取条目信息失败") from e

    poster_url = urls.get(subject_id)
    if not poster_url:
        raise HTTPException(status_code=404, detail="该条目无封面图")

    return {"status": "success", "url": poster_url}
