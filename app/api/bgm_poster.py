"""Bangumi 条目封面（仪表板时间线海报）API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.logging import logger
from ..utils.bgm_poster_service import get_poster_urls, normalize_subject_id
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/bgm", tags=["bgm"])


@router.get("/subjects/{subject_id}/poster")
async def get_subject_poster(
    subject_id: int,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
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


@router.get("/subjects/posters")
async def get_subjects_posters(
    subject_ids: list[int] = Query(default=[]),
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """批量返回条目封面图 URL（缺失条目不出现在 posters 中）。"""
    ids: list[int] = []
    seen: set[int] = set()
    for raw_id in subject_ids:
        subject_id = normalize_subject_id(raw_id)
        if subject_id is not None and subject_id not in seen:
            seen.add(subject_id)
            ids.append(subject_id)

    if not ids:
        return {"status": "success", "posters": {}}

    try:
        poster_map = await get_poster_urls(ids)
    except Exception as e:
        logger.warning("批量获取封面失败: %s", e)
        raise HTTPException(status_code=502, detail="获取条目信息失败") from e

    posters = {str(subject_id): url for subject_id, url in poster_map.items()}
    return {"status": "success", "posters": posters}
