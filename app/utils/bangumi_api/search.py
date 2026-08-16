"""BangumiApi 搜索与匹配（mixin）"""

from __future__ import annotations

import datetime
import os
from typing import Any

import httpx

from ...core.logging import logger
from ..bangumi_archive._title_normalize import (
    API_SIMILARITY_FALLBACK,
    API_SIMILARITY_PRIMARY,
    _normalize_title_for_match,
    fuse_title_similarity,
)

# 兜底搜索（无日期模式）拉取候选条目的上限。
# v0 search 返回完整 Subject（含 infobox），无需逐条 get_subject 补全，
# 但仍需限制候选数量避免过多低相似度结果干扰匹配。
FALLBACK_SEARCH_LIMIT = 15


class SearchMixin:
    """搜索与匹配相关方法（供 BangumiApi 组合）"""

    def get_me(self) -> dict[str, Any]:
        res = self.get("me")
        if 400 <= res.status_code < 500:
            # 发送API认证失败通知
            from ...services.notification_service import notification_service

            notification_service.notify(
                "api_auth_error",
                user_name=self.username,
                status_code=res.status_code,
                error_message="BangumiApi: 未授权, access_token不正确或未设置",
            )
            # 同时触发 token 过期事件
            notification_service.notify(
                "bangumi_token_expired",
                user_name=self.username,
                status_code=res.status_code,
                error_message="BangumiApi: 未授权, access_token不正确或未设置",
            )
            if os.name == "nt":
                os.startfile(f"{self.next_base}/demo/access-token")
            raise ValueError("BangumiApi: 未授权, access_token不正确或未设置")
        return res.json()

    def search(
        self,
        title: str,
        start_date: str = "",
        end_date: str = "",
        limit: int = 5,
        list_only: bool = True,
        subject_types: list[int] | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        # 使用实例缓存避免内存泄漏
        cache_key = (
            title,
            start_date,
            end_date,
            limit,
            list_only,
            tuple(subject_types or [2]),
        )
        if cache_key in self._cache["search"]:
            return self._cache["search"][cache_key]

        # Archive 短路：本地命中即返回，未命中降级到 API
        shortcut = self._archive.try_search(
            title,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            subject_types=subject_types,
        )
        if shortcut.hit:
            data_list = shortcut.data or []
            result = data_list if list_only else {"data": data_list}
            self._put_cache("search", cache_key, result)
            self.last_hit_source = "archive"
            # 透传预测性匹配方式（精确 / 前缀变体 / 剥离派生等），供 sync_service 如实标注
            self.last_match_method = shortcut.match_method
            return result
        # archive 未命中/未启用：走 API，清空命中来源标记
        self.last_hit_source = ""
        self.last_match_method = ""

        # API 不可达短路：archive 已尝试且未命中，若 API 处于不可达 TTL 内，
        # 跳过实际请求直接返回空结果（避免每次都等待 10s×3 重试拖垮同步流程）。
        # 注意：不写入缓存，TTL 到期后下一次调用仍会恢复探测。
        if self.is_api_unreachable():
            logger.warning("📚 Bangumi API 不可达（TTL 内），search 返回空结果")
            return [] if list_only else {"data": []}

        try:
            # air_date 过滤：start_date/end_date 任一为空时不加该 filter，
            # 用于无日期兜底搜索（原 search_old 场景，v0 search 已替代 legacy 接口）
            air_date_filter: list[str] = []
            if start_date:
                air_date_filter.append(f">={start_date}")
            if end_date:
                air_date_filter.append(f"<{end_date}")
            subject_filter: dict[str, Any] = {
                "type": subject_types if subject_types else [2],
                "nsfw": True,
            }
            if air_date_filter:
                subject_filter["air_date"] = air_date_filter
            res = self._request_with_retry(
                "POST",
                self._req_not_auth,
                f"{self.host}/search/subjects",
                json={
                    "keyword": title,
                    "filter": subject_filter,
                },
                params={"limit": limit},
            )
        except httpx.HTTPError as e:
            # 网络不可达/重试耗尽：_request_with_retry 已标记不可达并告警，
            # 这里直接返回空结果，保证 bgm_search 等调用方继续走 fallback。
            # 不进入下方 .json() 逻辑：res 此时为占位 dict 会触发 AttributeError，
            # 也会让"JSON解析失败"日志误导排查方向。
            logger.error(f"search API 请求失败（网络错误）: {e}")
            return [] if list_only else {"data": []}
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(f"search API返回非字典类型: {type(res)}, 内容: {res}")
                res = {"data": []}
        except ValueError as e:
            logger.error(f"search JSON解析失败: {e}")
            res = {"data": []}

        result = res.get("data", []) if list_only else res
        self._put_cache("search", cache_key, result)
        return result

    def get_subject(self, subject_id: int, use_archive: bool = True) -> dict[str, Any]:
        # 使用实例缓存避免内存泄漏。key 区分 use_archive：Archive 数据不含
        # images 字段，混用同一槽位会污染 API 结果的封面解析。
        cache_key = (subject_id, use_archive)
        if cache_key in self._cache["get_subject"]:
            return self._cache["get_subject"][cache_key]

        # Archive 短路：本地命中即返回，未命中降级到 API（保持原行为）。
        # 注意：Archive 数据不含 images 封面字段，需要封面图的调用方
        # （如 BgmPosterService）应传 use_archive=False 走 API。
        if use_archive:
            shortcut = self._archive.try_get_subject(subject_id)
            if shortcut.hit:
                self._put_cache("get_subject", cache_key, shortcut.data)
                return shortcut.data

        # API 不可达短路（同 search）
        if self.is_api_unreachable():
            logger.warning(
                f"📚 Bangumi API 不可达（TTL 内），get_subject({subject_id}) 返回空"
            )
            return {}

        try:
            res = self.get(f"subjects/{subject_id}")
        except httpx.HTTPError as e:
            # 网络错误直接返回空结果，不进入 .json() 逻辑（同 search）
            logger.error(f"get_subject API 请求失败（网络错误）: {e}")
            return {}
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(f"get_subject API返回非字典类型: {type(res)}, 内容: {res}")
                res = {}
        except ValueError as e:
            logger.error(f"get_subject JSON解析失败: {e}")
            res = {}

        self._put_cache("get_subject", cache_key, res)
        return res

    def get_related_subjects(
        self, subject_id: int
    ) -> list[dict[str, Any]] | dict[str, Any]:
        # 使用实例缓存避免内存泄漏
        if subject_id in self._cache["get_related_subjects"]:
            return self._cache["get_related_subjects"][subject_id]

        # Archive 短路：本地命中返回，未命中降级到 API
        shortcut = self._archive.try_get_related_subjects(subject_id)
        if shortcut.hit:
            self._put_cache("get_related_subjects", subject_id, shortcut.data)
            return shortcut.data

        # API 不可达短路（同 search）
        if self.is_api_unreachable():
            logger.warning(
                f"📚 Bangumi API 不可达（TTL 内），"
                f"get_related_subjects({subject_id}) 返回空"
            )
            return []

        try:
            res = self.get(f"subjects/{subject_id}/subjects")
        except httpx.HTTPError as e:
            # 网络错误直接返回空结果，不进入 .json() 逻辑（同 search）
            logger.error(f"get_related_subjects API 请求失败（网络错误）: {e}")
            return []
        try:
            res = res.json()
            # get_related_subjects 可能返回列表或字典，都是正常的
            if not isinstance(res, (dict, list)):
                logger.error(
                    f"get_related_subjects API返回异常类型: {type(res)}, 内容: {res}"
                )
                res = []
        except ValueError as e:
            logger.error(f"get_related_subjects JSON解析失败: {e}")
            res = []

        self._put_cache("get_related_subjects", subject_id, res)
        return res

    def bgm_search(
        self,
        title: str,
        ori_title: str | None,
        premiere_date: str,
        is_movie: bool = False,
        subject_types: list[int] | None = None,
    ) -> list[dict[str, Any]] | None:
        # 阶段二第一步：接入 SearchResetStep + SearchFinalizeStep 边界 step，
        # 中间逻辑操作 ctx 字段。死状态 last_match_method 仍写（兼容），
        # 同时写 ctx.matched_variant_method（阶段三 sync_service 消费）。
        # utils 层局部 import services.matching 是过渡，阶段三迁移调用点后清理。
        from app.models.sync import CustomItem
        from app.services.matching.context import MatchContext
        from app.services.matching.steps.api_search import (
            SearchFinalizeStep,
            SearchResetStep,
        )
        from app.services.sync_service.match_trace import MatchTrace

        ctx = MatchContext(
            item=CustomItem(
                title=title,
                ori_title=ori_title,
                season=0,
                episode=0,
                release_date=premiere_date,
                media_type="movie" if is_movie else "episode",
                user_name="bgm_search",  # 占位，SearchResetStep 仅读 title/ori_title
            ),
            bgm=self,
            trace=MatchTrace(),
        )

        # 阶段A：重置 + 预计算
        SearchResetStep().execute(ctx)

        start_date_str = "无日期"
        end_date_str = "无日期"

        # 阶段B：尝试使用 v0 接口进行带首播日期的精确搜索
        if premiere_date and len(premiere_date) >= 10:
            try:
                air_date = datetime.datetime.fromisoformat(premiere_date[:10])
                start_date = air_date - datetime.timedelta(days=2)
                end_date = air_date + datetime.timedelta(days=2)

                start_date_str = start_date.strftime("%Y-%m-%d")
                end_date_str = end_date.strftime("%Y-%m-%d")

                if ori_title:
                    ctx.bgm_data = self.search(
                        title=ori_title,
                        start_date=start_date_str,
                        end_date=end_date_str,
                        subject_types=subject_types,
                    )
                ctx.bgm_data = ctx.bgm_data or self.search(
                    title=title,
                    start_date=start_date_str,
                    end_date=end_date_str,
                    subject_types=subject_types,
                )
                # 剥离季数/集数后缀变体（仅在与原 title 不同时尝试）
                # 提升 API 场景匹配率：覆盖「完美世界 S06E279」类查询
                if (
                    not ctx.bgm_data
                    and ctx.stripped_title
                    and ctx.stripped_title != title
                ):
                    ctx.bgm_data = self.search(
                        title=ctx.stripped_title,
                        start_date=start_date_str,
                        end_date=end_date_str,
                        subject_types=subject_types,
                    )
                if (
                    not ctx.bgm_data
                    and ctx.stripped_ori
                    and ctx.stripped_ori != (ori_title or "")
                ):
                    ctx.bgm_data = self.search(
                        title=ctx.stripped_ori,
                        start_date=start_date_str,
                        end_date=end_date_str,
                        subject_types=subject_types,
                    )

                if not ctx.bgm_data and is_movie:
                    movie_search_title = ori_title or title
                    movie_end_date = air_date + datetime.timedelta(days=200)
                    end_date_str = movie_end_date.strftime("%Y-%m-%d")
                    ctx.bgm_data = self.search(
                        title=movie_search_title,
                        start_date=start_date_str,
                        end_date=end_date_str,
                        subject_types=subject_types,
                    )
            except ValueError:
                logger.warning(
                    f"首播日期格式解析失败: {premiere_date}，降级至无日期模式搜索"
                )
            except httpx.HTTPError as e:
                # 网络不可达/重试耗尽：精确搜索失败不中断，
                # 降级到下方无日期模式搜索
                logger.error(f"精确搜索 API 失败（网络错误）: {e}")

        # 阶段C：若精确搜索无结果或相似度低于阈值，使用 v0 接口无日期模式兜底搜索
        if not ctx.bgm_data or (
            ctx.bgm_data
            and len(ctx.bgm_data) > 0
            and self.title_diff_ratio(
                title=title, ori_title=ori_title, bgm_data=ctx.bgm_data[0]
            )
            < API_SIMILARITY_PRIMARY
        ):
            # 构建搜索标题列表：复用共享变体生成 build_search_variants，
            # 与 Archive 短路路径（try_search）使用同一套变体策略
            from ..bangumi_archive._title_normalize import build_search_variants

            search_titles = build_search_variants(title, ori_title or "")

            found = False
            for v in search_titles:
                t = v.query
                # v0 接口支持多 type 数组，但兜底路径保留单 type 循环
                # 以保持变体×type 笛卡尔积的尝试顺序（行为对齐原 search_old）
                types_to_try = subject_types if subject_types else [2]
                for t_type in types_to_try:
                    try:
                        # v0 search 无日期模式：start_date/end_date 为空时不加 air_date filter
                        bgm_data_old = self.search(
                            title=t,
                            start_date="",
                            end_date="",
                            limit=FALLBACK_SEARCH_LIMIT,
                            subject_types=[t_type],
                        )
                    except httpx.HTTPError as e:
                        # 网络错误不中断 fallback：跳过该变体继续尝试
                        logger.error(f"兜底搜索网络失败({t!r}): {e}")
                        bgm_data_old = []

                    if bgm_data_old:
                        # 保留相似度 > 0.3 的候选；全部低于阈值时视为未命中
                        matched = [
                            c
                            for c in bgm_data_old
                            if self.title_diff_ratio(title, ori_title, bgm_data=c)
                            > API_SIMILARITY_FALLBACK
                        ]
                        if matched:
                            ctx.bgm_data = matched
                            found = True
                            # 标注预测性匹配方式：本次命中来自哪个派生变体
                            # ctx.matched_variant_method 激活死状态（阶段三 sync_service 消费）
                            ctx.matched_variant_method = v.method
                            self.last_match_method = v.method  # 兼容
                            break
                if found:
                    break
            else:
                ctx.bgm_data = None

        # 阶段D：收尾
        outcome = SearchFinalizeStep().execute(ctx)
        if outcome.status == "miss":
            return None

        logger.debug(
            f"搜索日期区间: {start_date_str} 至 {end_date_str} | 结果: {ctx.bgm_data[0].get('name')}"
        )
        return ctx.bgm_data

    @staticmethod
    def title_diff_ratio(
        title: str, ori_title: str | None, bgm_data: dict[str, Any]
    ) -> float:
        """计算搜索标题与 Bangumi 条目的相似度（0~1）。

        三维度评分：
        1. 原始 fuzz.ratio（保持向后兼容）
        2. 核心标题包含检查（剥离媒体后缀后，核心标题互相包含则给 0.9）
        3. fuzz.partial_ratio * 0.7（捕捉部分匹配，打折抑制误判）

        防误判机制：当搜索标题和候选标题都含有媒体后缀（如"动画版"）时，
        若核心标题不相关（fuzz.ratio < 0.4 且不互相包含），则将最终得分
        限制在 0.4 以下，防止共享后缀（如"X动画版" vs "Y动画版"）导致误匹配。

        归一化：比较前对标题做空格折叠与修饰词（年番/番外等）去除，
        使"斗破苍穹年番"与"斗破苍穹 年番"这类仅差空格/修饰词的差异
        被正确识别为等价，避免真实相似度被低估而误沉淀。
        """
        ori_title = ori_title or title
        # 归一化用于相似度比较（不改变入参语义，日志/其他用途仍用原值）
        norm_title = _normalize_title_for_match(title)
        norm_ori = _normalize_title_for_match(ori_title)
        cand_name = bgm_data.get("name") or None
        cand_name_cn = bgm_data.get("name_cn") or None
        cand_aliases: list[str] = []

        # 提取 infobox 中的别名，兼容多种历史数据格式
        infobox = bgm_data.get("infobox", [])
        if isinstance(infobox, list):
            for info in infobox:
                if info.get("key") == "别名":
                    alias_value = info.get("value")
                    if isinstance(alias_value, list):
                        for alias_item in alias_value:
                            if isinstance(alias_item, dict) and "v" in alias_item:
                                cand_aliases.append(alias_item["v"])
                            elif isinstance(alias_item, str):
                                cand_aliases.append(alias_item)
                    elif isinstance(alias_value, str):
                        cand_aliases.append(alias_value)
                    break

        # G2：统一委托给 bangumi_archive._title_normalize.fuse_title_similarity
        # （Archive 模糊打分已改用同一实现），保留 API 路径切点：
        # partial*0.7、无 token_set、核心包含 0.9、媒体后缀防误判开启。
        # 候选名/别名同样用 _normalize_title_for_match 归一化，与原 title_diff_ratio
        # 行为一致（原实现在循环内对候选做归一化）；fuse 假设入参已归一化。
        norm_name = _normalize_title_for_match(cand_name) if cand_name else ""
        norm_name_cn = (
            _normalize_title_for_match(cand_name_cn) if cand_name_cn else None
        )
        norm_aliases = [_normalize_title_for_match(a) for a in cand_aliases] or None
        return fuse_title_similarity(
            norm_title,
            norm_ori,
            norm_name,
            norm_name_cn,
            norm_aliases,
            partial_weight=0.7,
            token_set_weight=0.0,
            core_contains_weight=0.9,
            media_suffix_guard=True,
            substring_boost=False,
        )
