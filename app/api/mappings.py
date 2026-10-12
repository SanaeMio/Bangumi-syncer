"""映射相关API"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core.logging import logger
from ..services.mapping_service import mapping_service
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api", tags=["mappings"])


@router.get("/mappings")
async def get_custom_mappings(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """获取自定义映射（含正则规则）"""
    try:
        mappings = mapping_service.get_all_mappings()
        rules = mapping_service.get_all_rules()
        return {
            "status": "success",
            "data": {"mappings": mappings, "rules": rules},
        }
    except Exception as e:
        logger.error(f"获取自定义映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取自定义映射失败: {str(e)}")


@router.post("/mappings")
async def update_custom_mappings(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """更新自定义映射（支持附带 rules）"""
    try:
        data = await request.json()
        mappings = data.get("mappings", {})
        rules = data.get("rules")  # None 表示保留现有 rules

        if not isinstance(mappings, dict):
            raise HTTPException(status_code=400, detail="mappings 必须是对象")
        if rules is not None and not isinstance(rules, list):
            raise HTTPException(status_code=400, detail="rules 必须是数组")

        # 写入前校验本次提交的 subject_id（含 segments 与 rules 内的），避免把
        # 书籍/音乐/游戏条目的 ID 写进映射 —— 那样直到真正同步时才报错，错误
        # 信息离病因很远。校验对「无可用账号 / 网络异常」降级放行，不会把用户
        # 的编辑挡回去。
        #
        # 只校验**相对磁盘配置新增或发生变化的** ID，而不是整份提交：前端保存
        # 时总是把全量 mappings + rules 发上来，若对全量校验，每次保存都要为
        # 配置里每个 ID 打一轮网络（archive 未命中时每个 ID 都新建 API 客户端
        # 同步 GET）；且配置里只要存在一个历史遗留的无效 ID（条目被删/合并、
        # 或早期写入的书籍条目），之后连「改另一条映射」「加一条正则」都会被
        # 400 挡住，只能先手动删掉那条或清空配置才能继续。
        #
        # 校验是阻塞式网络请求，放在线程池里跑：每个 ID 都要新建 API 客户端并
        # 同步 GET，直接在 async 处理函数里循环会阻塞事件循环。validate_subject_id
        # 是纯函数（内部只用局部客户端），故并发安全。
        from ..utils.bangumi_api.subject_validation import (
            collect_changed_subject_ids,
            validate_subject_id,
        )

        # 磁盘上的现状，用于求「本次实际改动了什么」的差集。
        # 注意 get_all_rules() 返回的是过滤后的匹配用视图（丢弃 pattern/subject_id
        # 缺失或正则编译失败的条目）——这里只做前后比对，两侧口径一致，不会因此
        # 产生误判；真正写盘时 update_custom_mappings 会重新从磁盘读原始 rules。
        existing_mappings = mapping_service.get_all_mappings()
        existing_rules = mapping_service.get_all_rules()

        ids_to_check = collect_changed_subject_ids(
            mappings, rules, existing_mappings, existing_rules
        )
        invalid: list[str] = []
        if ids_to_check:
            results = await asyncio.gather(
                *(asyncio.to_thread(validate_subject_id, sid) for sid in ids_to_check)
            )
            for sid, (ok, reason) in zip(ids_to_check, results, strict=True):
                if not ok:
                    invalid.append(f"{sid}（{reason}）")
        if invalid:
            raise HTTPException(
                status_code=400,
                detail="以下 Bangumi ID 校验失败，未写入：" + "；".join(invalid),
            )

        # 更新映射（rules=None 时保留现有）。
        # 必须检查返回值：写盘失败（权限、磁盘满）或配置文件损坏被拒绝写入时
        # 返回 False，此时若照旧回 success，前端会显示「保存成功」而改动其实
        # 没有落盘，用户下次同步仍然命中旧配置。
        if not mapping_service.update_custom_mappings(mappings, rules=rules):
            raise HTTPException(
                status_code=500,
                detail="映射写入失败：配置文件可能损坏或无写入权限，"
                "请检查 bangumi_mapping.json 的 JSON 语法与文件权限",
            )

        return {"status": "success", "message": "映射更新成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新自定义映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新自定义映射失败: {str(e)}")


@router.get("/mappings/subject/{subject_id}/episodes")
async def get_subject_episodes(
    subject_id: int,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """返回条目的章节列表，供映射弹窗「集数分段」自动推算偏移量。

    只返回正片章节（type=0）的序号与标题。前端用它在用户选定目标条目后，
    按播出日/集号推算「该段第 1 集对应该条目的第几集」，避免用户手算。
    """
    if subject_id < 1:
        raise HTTPException(status_code=400, detail="无效的条目 ID")

    from ..utils.bangumi_api.factory import build_bangumi_api_from_primary_config

    api = build_bangumi_api_from_primary_config()
    if api is None:
        raise HTTPException(status_code=400, detail="未配置可用的 Bangumi 账号")

    try:
        data = api.get_episodes(subject_id, 0, fetch_all=True)
        rows = data.get("data") if isinstance(data, dict) else data
        eps: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            sort = row.get("sort")
            if sort is None:
                continue
            eps.append(
                {
                    "sort": sort,
                    "name": row.get("name") or "",
                    "name_cn": row.get("name_cn") or "",
                    "airdate": row.get("airdate") or "",
                }
            )
        return {
            "status": "success",
            "total": len(eps),
            "episodes": eps,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取条目 {subject_id} 章节列表失败: {e}")
        raise HTTPException(status_code=502, detail=f"获取章节列表失败: {str(e)}")
    finally:
        try:
            api.close()
        except Exception:  # noqa: BLE001 — 关闭失败不影响响应
            pass


@router.delete("/mappings/{title}")
async def delete_custom_mapping(
    title: str,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """删除单个自定义映射"""
    try:
        # 检查映射是否存在（保留 404 语义）
        if title not in mapping_service.get_all_mappings():
            raise HTTPException(status_code=404, detail="映射不存在")

        # 删除并写回（读全量→删除→写回由 service 层封装）
        if not mapping_service.delete_single_mapping(title):
            raise HTTPException(status_code=500, detail="删除映射失败")

        return {"status": "success", "message": "映射删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除自定义映射失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除自定义映射失败: {str(e)}")


# ======================================================================
# 屏蔽关键词（统一黑名单）
#
# 合并了历史 [sync] blocked_keywords（INI）与 title_blacklist（DB subject_id）
# 两套机制：现全部存 DB、按标题关键词判定、匹配前生效，由本组接口管理。
# 自定义映射优先级高于屏蔽关键词（命中映射时即使含屏蔽词也照常同步）。
# ======================================================================


@router.get("/blocked-keywords")
async def get_blocked_keywords(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """获取全部屏蔽关键词（含来源：manual 手填 / reject 拒绝候选时自动记录）"""
    try:
        from ..core.database import database_manager

        return {
            "status": "success",
            "data": {"keywords": database_manager.list_blocked_keywords()},
        }
    except Exception as e:
        logger.error(f"获取屏蔽关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取屏蔽关键词失败: {str(e)}")


@router.post("/blocked-keywords")
async def add_blocked_keyword(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """新增屏蔽关键词（幂等：已存在不报错，返回 added=False）"""
    try:
        from ..core.database import database_manager

        data = await request.json()
        keyword = (data.get("keyword") or "").strip()
        if not keyword:
            raise HTTPException(status_code=400, detail="关键词不能为空")

        added = database_manager.add_blocked_keyword(keyword, source="manual")
        return {
            "status": "success",
            "data": {"added": bool(added), "keyword": keyword},
            "message": "已添加" if added else "该关键词已存在",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"新增屏蔽关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"新增屏蔽关键词失败: {str(e)}")


@router.delete("/blocked-keywords/{keyword}")
async def delete_blocked_keyword(
    keyword: str,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """删除单个屏蔽关键词"""
    try:
        from ..core.database import database_manager

        if not database_manager.remove_blocked_keyword(keyword):
            raise HTTPException(status_code=404, detail="关键词不存在")
        return {"status": "success", "message": "已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除屏蔽关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除屏蔽关键词失败: {str(e)}")


@router.delete("/blocked-keywords")
async def clear_blocked_keywords(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
) -> dict[str, Any]:
    """清空全部屏蔽关键词（批量保存时的整体覆盖入口）"""
    try:
        from ..core.database import database_manager

        removed = database_manager.clear_blocked_keywords()
        return {
            "status": "success",
            "data": {"removed": removed},
            "message": f"已清空 {removed} 条",
        }
    except Exception as e:
        logger.error(f"清空屏蔽关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"清空屏蔽关键词失败: {str(e)}")


@router.put("/blocked-keywords")
async def replace_blocked_keywords(
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """整体覆盖屏蔽关键词（清空后批量写入，供配置页一次保存）

    ``source='reject'`` 的记录（用户拒绝候选时自动记录的）**会被保留** ——
    它们不是用户在配置页里手写的，整体覆盖不应误删。
    """
    try:
        from ..core.database import database_manager

        data = await request.json()
        keywords = data.get("keywords") or []
        if not isinstance(keywords, list):
            raise HTTPException(status_code=400, detail="keywords 必须是数组")

        # 只清理手动添加的，保留 reject 自动记录的
        existing = database_manager.list_blocked_keywords()
        for item in existing:
            if item.get("source") != "reject":
                database_manager.remove_blocked_keyword(item.get("keyword", ""))

        added = database_manager.bulk_add_blocked_keywords(
            [str(k).strip() for k in keywords if str(k).strip()],
            source="manual",
        )
        return {
            "status": "success",
            "data": {"added": added},
            "message": "屏蔽关键词已保存",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存屏蔽关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存屏蔽关键词失败: {str(e)}")
