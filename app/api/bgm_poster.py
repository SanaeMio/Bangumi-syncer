"""Bangumi 条目封面（仪表板时间线海报）API。"""

from fastapi import APIRouter, Depends, HTTPException

from ..core.config import config_manager
from ..core.logging import logger
from ..utils.bangumi_api import BangumiApi
from ..utils.bgm_image_url import extract_poster_url, rewrite_bgm_image_url
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/bgm", tags=["bgm"])


def _get_public_bangumi_api() -> BangumiApi:
    return BangumiApi(
        http_proxy=config_manager.get("dev", "script_proxy", fallback=""),
        ssl_verify=config_manager.get("dev", "ssl_verify", fallback=True),
        bgm_api_proxy=config_manager.get("dev", "bgm_api_proxy", fallback=""),
    )


@router.get("/subjects/{subject_id}/poster")
async def get_subject_poster(
    subject_id: int,
    current_user: dict = Depends(get_current_user_flexible),
):
    """返回条目封面图 URL（服务端取元数据，可选图片 CDN 反代改写）。"""
    if subject_id < 1:
        raise HTTPException(status_code=400, detail="无效的条目 ID")

    bgm = _get_public_bangumi_api()
    try:
        subject = bgm.get_subject(subject_id)
    except Exception as e:
        logger.warning("获取条目 %s 封面失败: %s", subject_id, e)
        raise HTTPException(status_code=502, detail="获取条目信息失败") from e

    if not subject or not subject.get("id"):
        raise HTTPException(status_code=404, detail="条目不存在")

    poster_url = extract_poster_url(subject)
    if not poster_url:
        raise HTTPException(status_code=404, detail="该条目无封面图")

    image_proxy = config_manager.get("dev", "bgm_image_proxy", fallback="").strip()
    poster_url = rewrite_bgm_image_url(poster_url, image_proxy)

    return {"status": "success", "url": poster_url}
