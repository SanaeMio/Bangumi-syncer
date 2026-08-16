"""API 搜索 step（阶段三）

对应原 _find_subject_id 阶段 3：Bangumi API 搜索 + post_search 改选。
命中即终止，match_method=archive 或 api_search。
post_search 改选逻辑（季度改选 + 媒体类型改选 + 关联条目改选）作为本 step
内部私有方法保留，阶段四再拆为独立 PostSearchStep。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import logger
from app.services.matching.context import MatchContext
from app.services.matching.steps.base import MatchStepBase, StepOutcome
from app.services.sync_service.match_trace import MatchCandidate

# 延迟导入避免循环依赖：这些符号在 sync_service.__init__ 顶层定义
# 运行时 sync_service 已加载完成，step.execute 调用时 import 必然成功


def _import_sync_helpers():
    """延迟导入 sync_service 模块级辅助函数，避免循环依赖"""
    from app.services.sync_service import (
        RELATION_ID_PARENT_STORY,
        RELATIONS,
        SUBJECT_TYPE_ANIME,
        SUBJECT_TYPE_REAL,
        _build_error_detail,
        _detect_candidate_media_type,
        _extract_infobox_aliases,
    )
    from app.utils.media_type_detector import detect_media_type

    return (
        RELATION_ID_PARENT_STORY,
        RELATIONS,
        SUBJECT_TYPE_ANIME,
        SUBJECT_TYPE_REAL,
        _build_error_detail,
        _detect_candidate_media_type,
        _extract_infobox_aliases,
        detect_media_type,
    )


class APISearchStep(MatchStepBase):
    """Bangumi API 搜索 + post_search 改选

    - 调用 bgm.bgm_search（已 step 化，见 steps/api_search.py）
    - 记录 request_params / api_response_summary / candidates 到 trace
    - post_search 改选：季度改选 + 媒体类型改选 + 关联条目改选
    - 置信度阈值检查：低于阈值沉淀待审，不设 final_subject_id
    - 命中时设置 ctx.subject_id + ctx.match_stage + ctx.match_method_detail
    """

    stage = "api_search"

    def execute(self, ctx: MatchContext) -> StepOutcome:
        item = ctx.item
        service = ctx.service

        # 延迟导入：与 sync_service 内部使用同一 config_manager 引用，
        # 使测试 patch("app.services.sync_service.config_manager") 生效
        from app.services.sync_service import config_manager

        # 根据配置与媒体类型决定搜索的条目类型
        enable_real_action = config_manager.get(
            "sync", "enable_real_action", fallback=False
        )
        if item.media_type == "real_action":
            subject_types = [6]  # SUBJECT_TYPE_REAL，避免循环导入用字面量
        elif enable_real_action:
            subject_types = [2, 6]  # [ANIME, REAL]
        else:
            subject_types = [2]  # [ANIME]
        ctx.subject_types = subject_types

        _ctx_str = (
            f"user_name={item.user_name!r} source={item.source!r} "
            f"S{item.season:02d}E{item.episode:02d} media_type={item.media_type!r} "
            f"title={item.title!r} ori_title={item.ori_title!r}"
        )

        # 延迟到本 step 内部获取 BangumiApi 实例：
        # custom_mapping/bangumi_data 不需要 bgm，避免提前构造失败影响前置 step
        bgm = ctx.bgm or service._get_bangumi_api_for_user(item.user_name)
        ctx.bgm = bgm
        if not bgm:
            ctx.failure_detail = "无法创建 Bangumi API 实例，无法搜索条目"
            return StepOutcome(
                status="error",
                reason="无法创建 Bangumi API 实例",
                error_detail={
                    "type": "RuntimeError",
                    "message": "无法为用户创建 Bangumi API 实例",
                    "traceback": "",
                },
                is_terminal=True,
            )

        premiere_date = None
        if item.release_date and len(item.release_date) >= 8:
            premiere_date = item.release_date[:10]

        # 搜索时优先使用归一化标题
        search_title = ctx.normalized_title or item.title

        request_params = {
            "title": search_title,
            "ori_title": item.ori_title or "",
            "premiere_date": premiere_date or "",
            "is_movie": item.media_type == "movie",
            "subject_types": subject_types,
            "media_type": item.media_type,
            "season": item.season,
        }

        try:
            # 阶段五：检测 ctx.bgm_data 是否已有数据（来自 ArchiveShortcutStep 命中）
            # archive 命中时跳过 bgm_search()，直接走候选排序 + post_search 改选
            #
            # 媒体类型不匹配降级：archive 精确匹配可能只返回单一类型候选
            # （如查询"凡人修仙传"只命中真人剧 type=6，动画版标题带后缀不精确匹配）。
            # 此时 _media_type_reselect 在候选列表里找不到匹配项，改选失败。
            # 检测到此场景时清空 ctx.bgm_data，降级走 API 搜索获取更多候选。
            if ctx.bgm_data and item.media_type == "episode":
                (_, _, _, _, _, _detect_candidate_media_type, _, _) = (
                    _import_sync_helpers()
                )
                top_detected = _detect_candidate_media_type(ctx.bgm_data[0])
                if top_detected == "real_action" and not any(
                    _detect_candidate_media_type(c) == "episode"
                    for c in ctx.bgm_data[1:]
                ):
                    logger.debug(
                        "archive 命中但 top 媒体类型=real_action 不匹配 episode "
                        "请求，候选列表无 episode 候选，降级 API 搜索"
                    )
                    ctx.bgm_data = None

            if ctx.bgm_data:
                bgm_data = ctx.bgm_data
                is_archive_hit = True
            else:
                # bgm_search 内部 ctx 与主 ctx 分离，命中的变体方法通过
                # out_meta 回传（替代旧实现经共享 bgm 实例的 last_match_method
                # 隐式传递，消除并发同用户多任务时的脏读风险）
                out_meta: dict[str, Any] = {}
                bgm_data = bgm.bgm_search(
                    title=search_title,
                    ori_title=item.ori_title or "",
                    premiere_date=premiere_date or "",
                    is_movie=(item.media_type == "movie"),
                    subject_types=subject_types,
                    trace=ctx.trace,
                    out_meta=out_meta,
                )
                # 判断命中来源：archive 短路命中标记为 "archive"，否则 "api_search"
                is_archive_hit = bool(ctx.archive_hit)

            first = bgm_data[0] if bgm_data else {}
            api_response_summary = {
                "total_candidates": len(bgm_data) if bgm_data else 0,
                "is_archive_hit": is_archive_hit,
                "first_subject_id": first.get("id"),
                "first_name": first.get("name") or "",
                "first_name_cn": first.get("name_cn") or "",
            }

            if not bgm_data:
                ctx.failure_detail = "Bangumi 搜索无结果"
                return StepOutcome(
                    status="miss",
                    reason="Bangumi API 搜索无结果",
                    is_terminal=True,
                    request_params=request_params,
                    api_response_summary=api_response_summary,
                )

            match_stage = "archive" if is_archive_hit else "api_search"
            ctx.match_stage = match_stage

            # top-N platform 加权排序
            is_movie_request = item.media_type == "movie"
            bgm_data = service._sort_candidates_by_platform(
                bgm_data, is_movie=is_movie_request, limit=15
            )

            original_top = bgm_data[0] if bgm_data else {}
            original_top_id = original_top.get("id")
            original_top_name = (
                original_top.get("name_cn") or original_top.get("name") or ""
            )

            # 收集 top-5 候选
            candidates = [
                MatchCandidate(
                    subject_id=str(c.get("id", "")),
                    name=c.get("name", ""),
                    name_cn=c.get("name_cn", ""),
                    score=bgm.title_diff_ratio(search_title, item.ori_title, c),
                    platform=c.get("platform", ""),
                    air_date=c.get("date", ""),
                    source=match_stage,
                    media_type=self._detect_media_type(c),
                    infobox_aliases=self._extract_infobox_aliases(c),
                )
                for c in bgm_data[:5]
            ]

            reason = (
                f"本地归档命中：{original_top_name}"
                if is_archive_hit
                else f"API 搜索命中：{original_top_name}"
            )

            # post_search 改选（季度改选 + 媒体类型改选 + 关联条目改选）
            # 返回 (is_season_matched, post_candidates, post_reason, post_subject_id)
            post_result = self._post_search_reselect(
                ctx, bgm, bgm_data, search_title, original_top_id, original_top_name
            )
            is_api_season_matched, post_candidates, post_reason, post_subject_id = (
                post_result
            )
            candidates.extend(post_candidates)

            # 改选后同步 candidates[0]：_post_search_reselect 可能原地修改 bgm_data[0]
            # （季度改选/媒体类型改选/关联条目改选），需重建 candidates[0] 以反映
            # 改选后的 top 候选。否则 candidates[0].score 仍为改选前原 top 的低分，
            # 导致置信度检查误判 low_confidence（P1-1），且 candidates[0].subject_id
            # 与 ctx.subject_id（= bgm_data[0]["id"]）不一致（P2-3）。
            if bgm_data and bgm_data[0].get("id") != original_top_id:
                candidates[0] = self._build_candidate(
                    bgm_data[0], bgm, search_title, item, match_stage
                )

            # 置信度阈值检查：复用 candidates[0].score（已通过 title_diff_ratio 计算），
            # 避免对同一 top 候选重复调用 title_diff_ratio
            threshold = service._get_match_confidence_threshold()
            real_conf = candidates[0].score if candidates else None
            if isinstance(real_conf, (int, float)) and real_conf < threshold:
                ctx.failure_detail = (
                    f"match confidence {real_conf:.2f} below threshold {threshold:.2f}"
                )
                return StepOutcome(
                    status="low_confidence",
                    subject_id=str(bgm_data[0].get("id")),
                    reason=(
                        f"匹配相似度 {real_conf:.2f} 低于阈值 {threshold:.2f}，已沉淀待审"
                    ),
                    score=real_conf,
                    candidates=candidates,
                    request_params=request_params,
                    api_response_summary=api_response_summary,
                    is_terminal=True,
                    stage_override=match_stage,
                )

            # 命中：设置 ctx
            ctx.subject_id = str(bgm_data[0]["id"])
            ctx.is_season_matched_id = is_api_season_matched
            # 细粒度匹配方式：
            # - archive 命中：保留 ArchiveShortcutStep 设置的 match_method_detail
            #   （exact/fuzzy/prefix_variant/season_stripped 等，由 try_search 返回）
            # - API 命中：读 bgm_search 通过 out_meta 回传的变体方法
            #   （bgm_search 内部子 step 写入内部 ctx.matched_variant_method，
            #   bgm_search 收尾时回传到 out_meta，跨 ctx 边界显式传递）
            if is_archive_hit:
                ctx.match_method_detail = ctx.match_method_detail or "exact"
            else:
                ctx.match_method_detail = out_meta.get("variant_method", "") or ""
            # 歧义标记：top1/top2 分数差 < 0.05 时置 True，编排器据此发 match_ambiguous 通知
            # （原 _maybe_notify_match_ambiguous 逻辑前移到 step，通知职责留在编排器）
            # 按 score 降序取 top-2，与 _collect_candidates_from_trace 行为一致（P1-2）：
            # 改选后 post_candidates 可能追加更高分候选，若仍用 candidates[0]/[1]（按
            # bgm_data 原始顺序）会漏判歧义。
            if len(candidates) >= 2:
                # score 可能为非数值（mock 场景），用 isinstance 守卫避免排序比较失败
                def _safe_score(c: MatchCandidate) -> float:
                    s = c.score
                    return s if isinstance(s, (int, float)) else 0.0

                sorted_by_score = sorted(candidates, key=_safe_score, reverse=True)
                top1_s = _safe_score(sorted_by_score[0])
                top2_s = _safe_score(sorted_by_score[1])
                ctx.is_ambiguous = (top1_s - top2_s) < 0.05

            final_reason = reason
            if post_reason:
                final_reason = f"{reason}；{post_reason}"

            return StepOutcome(
                status="hit",
                subject_id=str(bgm_data[0]["id"]),
                reason=final_reason,
                score=(
                    real_conf
                    if isinstance(real_conf, (int, float))
                    else (1.0 if is_api_season_matched else 0.9)
                ),
                candidates=candidates,
                request_params=request_params,
                api_response_summary=api_response_summary,
                is_terminal=True,
                stage_override=match_stage,
            )
        except Exception as e:
            detail = f"Bangumi API 搜索出错: {e}"
            logger.error(f"bgm: {detail}；{_ctx_str}")
            (
                _,
                _,
                _,
                _,
                _build_error_detail,
                _,
                _,
                _,
            ) = _import_sync_helpers()
            ctx.failure_detail = detail
            return StepOutcome(
                status="error",
                reason=detail,
                error_detail=_build_error_detail(e),
                request_params=request_params,
                is_terminal=True,
            )

    # ------------------------------------------------------------------
    # post_search 改选逻辑（阶段四拆为独立 PostSearchStep）
    # ------------------------------------------------------------------

    def _post_search_reselect(
        self,
        ctx: MatchContext,
        bgm: Any,
        bgm_data: list[dict],
        search_title: str,
        original_top_id: Any,
        original_top_name: str,
    ) -> tuple[bool, list[MatchCandidate], str, Any]:
        """post_search 改选：季度改选 + 媒体类型改选 + 关联条目改选

        返回 (is_season_matched, post_candidates, post_reason, post_subject_id)
        """
        item = ctx.item
        service = ctx.service
        post_candidates: list[MatchCandidate] = []
        post_reason = ""
        post_subject_id = None

        is_api_season_matched = False

        # 季度校验：season > 1 时检查首条候选标题是否包含季度信息
        if item.season > 1:
            returned_name = bgm_data[0].get("name", "")
            returned_name_cn = bgm_data[0].get("name_cn", "")
            if service._check_season_info_in_title(
                returned_name, item.season
            ) or service._check_season_info_in_title(returned_name_cn, item.season):
                is_api_season_matched = True

        if is_api_season_matched:
            return is_api_season_matched, post_candidates, post_reason, post_subject_id

        # 季度改选：首条候选明确为第N季（N>1）时，寻找无季度后缀的第一季本体
        top_name = bgm_data[0].get("name", "")
        top_name_cn = bgm_data[0].get("name_cn", "")
        top_explicit_season = max(
            service._get_explicit_season_from_title(top_name) or 0,
            service._get_explicit_season_from_title(top_name_cn) or 0,
        )
        if top_explicit_season > 1:
            for cand in bgm_data[1:]:
                cand_name = cand.get("name", "")
                cand_name_cn = cand.get("name_cn", "")
                cand_season = max(
                    service._get_explicit_season_from_title(cand_name) or 0,
                    service._get_explicit_season_from_title(cand_name_cn) or 0,
                )
                if cand_season == 0:
                    logger.debug(
                        f"首条候选为第{top_explicit_season}季，"
                        f"改选无季度后缀的候选: "
                        f"{cand_name_cn or cand_name}(id={cand.get('id')})"
                    )
                    bgm_data[0] = cand
                    is_api_season_matched = True
                    post_subject_id = cand.get("id")
                    post_reason = (
                        f"季度改选：首条为第{top_explicit_season}季，"
                        f"改选无季度后缀的第一季本体"
                    )
                    post_candidates.append(
                        self._build_candidate(
                            cand, bgm, search_title, item, "post_search"
                        )
                    )
                    break

        # 媒体类型改选：仅在尚未通过季度改选时执行
        request_media_type = (item.media_type or "").strip().lower()
        if not is_api_season_matched and request_media_type:
            post_result = self._media_type_reselect(
                ctx, bgm, bgm_data, search_title, item, request_media_type
            )
            if post_result:
                is_api_season_matched, mt_candidates, mt_reason, mt_subject_id = (
                    post_result
                )
                post_candidates.extend(mt_candidates)
                if mt_reason:
                    post_reason = (
                        f"{post_reason}；{mt_reason}" if post_reason else mt_reason
                    )
                if mt_subject_id:
                    post_subject_id = mt_subject_id

        return is_api_season_matched, post_candidates, post_reason, post_subject_id

    def _media_type_reselect(
        self,
        ctx: MatchContext,
        bgm: Any,
        bgm_data: list[dict],
        search_title: str,
        item: Any,
        request_media_type: str,
    ) -> tuple[bool, list[MatchCandidate], str, Any] | None:
        """媒体类型改选 + 关联条目改选"""
        (
            RELATION_ID_PARENT_STORY,
            RELATIONS,
            SUBJECT_TYPE_ANIME,
            SUBJECT_TYPE_REAL,
            _,
            _detect_candidate_media_type,
            _,
            _,
        ) = _import_sync_helpers()

        service = ctx.service
        post_candidates: list[MatchCandidate] = []
        post_reason = ""
        post_subject_id = None
        is_api_season_matched = False

        # 使用 _detect_candidate_media_type（结合 subject type 字段判定三次元）：
        # 真人剧 type=6 即使标题无"日剧/真人版"关键词也能正确识别为 real_action，
        # 避免"凡人修仙传"查询返回真人剧（type=6, 标题完全相同）时误判为 episode
        top_detected = _detect_candidate_media_type(bgm_data[0])
        top_name = (bgm_data[0].get("name") or "").strip()
        top_name_cn = (bgm_data[0].get("name_cn") or "").strip()
        request_title = (item.title or "").strip()
        top_exact_match = request_title and request_title in {top_name, top_name_cn}
        need_reselect = top_detected != request_media_type or not top_exact_match

        if not need_reselect:
            return None

        # 1) 候选列表里找媒体类型一致的条目
        episode_candidates = []
        for cand in bgm_data[1:]:
            cand_detected = _detect_candidate_media_type(cand)
            if cand_detected == request_media_type:
                episode_candidates.append(cand)
        if top_detected == request_media_type and not top_exact_match:
            episode_candidates.insert(0, bgm_data[0])

        if episode_candidates:
            best_cand = service._pick_mainline_episode_candidate(
                episode_candidates, item.title or ""
            )
            # picker 返回类型为 dict | None，需守卫 None 避免 AttributeError
            if best_cand and best_cand.get("id") != bgm_data[0].get("id"):
                bgm_data[0] = best_cand
                is_api_season_matched = True
                post_subject_id = best_cand.get("id")
                post_reason = (
                    f"媒体类型改选：{best_cand.get('name_cn') or best_cand.get('name')}"
                )

        # 2) 关联条目改选
        if not is_api_season_matched:
            top_id = bgm_data[0].get("id")
            if top_id:
                related_list = self._fetch_related_subjects(bgm, top_id)
                chosen = self._pick_related_subject(
                    related_list, request_media_type, item.title or ""
                )
                if chosen:
                    chosen_id = chosen.get("id")
                    if chosen_id:
                        try:
                            chosen_info = bgm.get_subject(chosen_id)
                            if chosen_info and chosen_info.get("id"):
                                bgm_data[0] = chosen_info
                                is_api_season_matched = True
                                post_subject_id = chosen_id
                                post_reason = (
                                    f"关联条目改选：{chosen_info.get('name_cn') or chosen_info.get('name')}"
                                    f"(relation={chosen.get('relation')})"
                                )
                        except Exception as e:
                            logger.debug(
                                f"获取关联条目详情失败 (subject_id={chosen_id}): {e}"
                            )

                # 记录关联条目作为 post_search 候选
                for r in related_list:
                    if not isinstance(r, dict):
                        continue
                    post_candidates.append(
                        self._build_candidate(
                            r, bgm, search_title, item, "post_search_related"
                        )
                    )

        # 记录 episode 候选
        for c in episode_candidates:
            post_candidates.append(
                self._build_candidate(c, bgm, search_title, item, "post_search")
            )

        return is_api_season_matched, post_candidates, post_reason, post_subject_id

    @staticmethod
    def _fetch_related_subjects(bgm: Any, top_id: Any) -> list[dict]:
        """获取关联条目列表（容错）"""
        try:
            related = bgm.get_related_subjects(top_id)
            if isinstance(related, list):
                return related
            if isinstance(related, dict):
                return related.get("data", [])
        except Exception as e:
            logger.debug(f"获取关联条目失败 (subject_id={top_id}): {e}")
        return []

    @staticmethod
    def _pick_related_subject(
        related_list: list[dict], request_media_type: str, search_title: str
    ) -> dict | None:
        """从关联条目中择优选择主线故事条目

        媒体类型判定用 _detect_candidate_media_type（结合 subject type 字段），
        并保留 subject type 过滤：仅保留动画(2)/三次元(6)，排除书籍(1)/音乐(3)/
        游戏(4)等非影视条目 —— 防止 detect_media_type 仅凭标题关键词把原作小说
        误判为 episode 后选中（场景：《斗破苍穹年番》关联到原作小说 type=1）。
        """
        (
            RELATION_ID_PARENT_STORY,
            RELATIONS,
            SUBJECT_TYPE_ANIME,
            SUBJECT_TYPE_REAL,
            _,
            _detect_candidate_media_type,
            _,
            _,
        ) = _import_sync_helpers()
        from rapidfuzz import fuzz

        other_match = None
        for rel in related_list:
            if not isinstance(rel, dict):
                continue
            rel_type = rel.get("type")
            if rel_type not in (SUBJECT_TYPE_ANIME, SUBJECT_TYPE_REAL):
                continue
            rel_name = rel.get("name", "")
            rel_name_cn = rel.get("name_cn", "") or rel_name
            if search_title and rel_name_cn:
                title_sim = fuzz.ratio(rel_name_cn, search_title)
                if title_sim < 25:
                    continue
            rel_detected = _detect_candidate_media_type(rel)
            if rel_detected != request_media_type:
                continue
            rel_relation = (rel.get("relation") or "").strip()
            if rel_relation == RELATIONS[RELATION_ID_PARENT_STORY]:
                return rel
            if other_match is None:
                other_match = rel
        return other_match

    @staticmethod
    def _detect_media_type(cand: dict) -> str:
        (_, _, _, _, _, _detect_candidate_media_type, _, _) = _import_sync_helpers()
        return _detect_candidate_media_type(cand)

    @staticmethod
    def _extract_infobox_aliases(cand: dict) -> list[str]:
        (_, _, _, _, _, _, _extract_infobox_aliases, _) = _import_sync_helpers()
        return _extract_infobox_aliases(cand)

    def _build_candidate(
        self,
        cand: dict,
        bgm: Any,
        search_title: str,
        item: Any,
        source: str,
    ) -> MatchCandidate:
        return MatchCandidate(
            subject_id=str(cand.get("id", "")),
            name=cand.get("name", ""),
            name_cn=cand.get("name_cn", ""),
            score=bgm.title_diff_ratio(search_title, item.ori_title, cand),
            platform=cand.get("platform", ""),
            air_date=cand.get("date", ""),
            source=source,
            media_type=self._detect_media_type(cand),
            infobox_aliases=self._extract_infobox_aliases(cand),
        )
