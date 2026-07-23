"""BangumiData 标题匹配 Mixin

职责：
- 根据标题/原始标题/发布日期/季度查找 bangumi_id
- 精确索引匹配 + 线性扫描模糊匹配
- 标题相似度计算、日期差异、关键字符校验
- 搜索调试接口

所有方法通过 self. 访问其他 mixin（CacheMixin 提供 _parse_data 等），
由 __init__.py 中的 BangumiData 组合类统一持有实例状态。
"""

from __future__ import annotations

import re
from datetime import datetime

from rapidfuzz import fuzz

from ...core.logging import logger
from ...utils.media_type_detector import detect_media_type


class MatchingMixin:
    """标题匹配与番剧 ID 查找相关方法"""

    # ----- 公开查找入口 -----

    def find_bangumi_id(
        self,
        title: str,
        ori_title: str = None,
        release_date: str = None,
        season: int = 1,
        media_type: str = "",
    ) -> tuple[str, str, bool] | None:
        """
        根据标题和其他信息查找 bangumi id

        Args:
            title: 中文标题
            ori_title: 原版标题（通常是日文）
            release_date: 发布日期，格式为 YYYY-MM-DD
            season: 季度，默认为 1（第一季）
            media_type: 请求侧媒体类型（episode/movie/ova/oad/real_action），
                用于在无 release_date 且有多个同标题候选时按类型择优

        Returns:
            找到匹配的 (bangumi_id, matched_title, date_matched) 或 None
            date_matched: 是否通过日期匹配找到的（用于判断季度ID的可信度）
        """
        logger.debug(
            f"正在查找番剧 ID: {title=}, {ori_title=}, {release_date=}, {season=}, {media_type=}"
        )

        # 如果是非第一季，尝试从标题中识别第一季的标题
        original_title = title
        if season > 1:
            # 尝试移除标题中可能包含的季度信息
            title_without_season = re.sub(r"\s*[第]?\s*\d+\s*期?[話话集]?$", "", title)
            title_without_season = re.sub(
                r"\s*Season\s*\d+$", "", title_without_season, flags=re.IGNORECASE
            )
            title_without_season = re.sub(
                r"\s*S\d+$", "", title_without_season, flags=re.IGNORECASE
            )
            title_without_season = re.sub(r"\s*\d+$", "", title_without_season)
            title_without_season = re.sub(r"\s*II+$", "", title_without_season)
            title_without_season = re.sub(
                r"\s*[第]?\s*\d+\s*[期季]$", "", title_without_season
            )

            if title_without_season != title:
                logger.debug(f"移除季度信息后的标题: {title_without_season}")
                title = title_without_season

        # 使用优化的匹配算法，避免重复计算
        result = self._find_bangumi_id_optimized(
            title, ori_title, release_date, original_title, season, media_type
        )
        if result:
            return result
        return None

    # ----- 精确索引匹配 -----

    def _try_exact_match(
        self,
        title: str,
        ori_title: str,
        release_date: str,
        media_type: str = "",
    ) -> tuple[str, str, bool] | None:
        """尝试通过标题索引进行精确匹配（O(1)查找，避免线性扫描）

        Returns:
            匹配结果或 None（None 表示需要回退到线性扫描）
        """
        exact_candidates = []
        lookup_keys = [k for k in (title, ori_title) if k]
        for key in lookup_keys:
            items = self._title_index.get(key)
            if items:
                for item in items:
                    bangumi_id = self._extract_bangumi_id(item)
                    if bangumi_id:
                        exact_candidates.append((item, bangumi_id, key))

        if exact_candidates:
            matched_key = exact_candidates[0][2]
            logger.debug(
                f"标题索引命中: key='{matched_key}', 候选数={len(exact_candidates)}"
            )
            if release_date:
                min_diff = float("inf")
                best = None
                for item, bangumi_id, matched_key in exact_candidates:
                    item_date = item.get("begin", "")
                    if item_date:
                        try:
                            d1 = datetime.strptime(release_date[:10], "%Y-%m-%d")
                            d2 = datetime.strptime(item_date[:10], "%Y-%m-%d")
                            diff = abs((d1 - d2).days)
                            if diff < min_diff:
                                min_diff = diff
                                best = (bangumi_id, matched_key, True)
                        except ValueError:
                            continue
                if best:
                    if min_diff <= 180:
                        return best
                    # 日期差>180天，fall through 到线性扫描检查部分匹配
                    logger.debug(
                        f"标题索引命中但日期差 {min_diff} 天 > 180，回退线性扫描检查部分匹配"
                    )
            else:
                # 无日期时：若提供了 media_type 且有多个候选，
                # 按 detect_media_type(候选标题) 与请求 media_type 一致性择优
                if media_type and len(exact_candidates) > 1:
                    best = self._select_candidate_by_media_type(
                        exact_candidates, media_type
                    )
                    if best:
                        item, bangumi_id, matched_key = best
                        return (bangumi_id, matched_key, False)
                # 无 media_type 或择优未命中（候选均不匹配该类型），回退首个
                first = exact_candidates[0]
                return (first[1], first[2], False)

        return None

    def _select_candidate_by_media_type(
        self,
        candidates: list[tuple[dict, str, str]],
        request_media_type: str,
    ) -> tuple[dict, str, str] | None:
        """在多个同标题候选中按媒体类型择优。

        每个候选从 title + titleTranslate.zh-Hans 中收集所有标题，
        调用 detect_media_type 判断其媒体类型，与 request_media_type 一致者胜出。
        若无一致者，返回 None（由调用方回退首个候选）。

        Args:
            candidates: [(item, bangumi_id, matched_key), ...]
            request_media_type: 请求侧媒体类型（episode/movie/ova/oad/real_action）

        Returns:
            最佳候选或 None
        """
        if not request_media_type or not candidates:
            return None

        request_type = (request_media_type or "").strip().lower()

        def detect_item(item: dict) -> str:
            """扫描候选的所有标题（原文 + 中文翻译）判断媒体类型。"""
            all_titles = self._get_zh_hans_titles(item)
            # 把所有标题拼接成单个字符串传入 title，让 detect_media_type 一次性扫描
            # （detect_media_type 的关键词扫描对中英文均生效）
            combined = " ".join(t for t in all_titles if t)
            return detect_media_type(title=combined, ori_title=item.get("title", ""))

        # episode 是默认类型：优先排除标题中明确含剧场版/OVA/OAD/电影/真人版关键词的候选
        if request_type == "episode":
            preferred = []
            for item, bangumi_id, matched_key in candidates:
                if detect_item(item) == "episode":
                    preferred.append((item, bangumi_id, matched_key))
            if preferred:
                if len(preferred) == 1:
                    logger.debug(
                        f"media_type={request_type} 择优命中唯一剧集候选: "
                        f"{preferred[0][0].get('title', '')}"
                    )
                    return preferred[0]
                logger.debug(
                    f"media_type={request_type} 择优后仍有 {len(preferred)} 个剧集候选，"
                    f"取首个: {preferred[0][0].get('title', '')}"
                )
                return preferred[0]
            logger.debug(
                f"media_type={request_type} 但所有候选均非剧集类型，回退首个候选"
            )
            return None

        # 非 episode：优先选 detect 出来与请求类型一致的候选
        for item, bangumi_id, matched_key in candidates:
            if detect_item(item) == request_type:
                logger.debug(
                    f"media_type={request_type} 择优命中: {item.get('title', '')}"
                )
                return (item, bangumi_id, matched_key)
        return None

    # ----- 线性扫描 -----

    def _scan_candidates(
        self,
        title: str,
        ori_title: str,
        release_date: str,
    ) -> tuple[list, list, int]:
        """线性扫描数据，收集精确匹配与部分匹配候选

        Returns:
            (exact_matches, partial_matches, processed_count)
        """
        exact_matches = []
        partial_matches = []
        processed_count = 0

        # 优化：先进行快速预筛选
        for item in self._parse_data():
            processed_count += 1

            # 快速预筛选：默认要求有简中翻译；若提供了 ori_title，仍可对无 zh-Hans 的条目做日文原标题匹配
            missing_zh = (
                "titleTranslate" not in item or "zh-Hans" not in item["titleTranslate"]
            )
            if title and missing_zh:
                ori_ok = ori_title and str(ori_title).strip() and item.get("title")
                if not ori_ok:
                    continue

            # 一次性计算所有相似度，避免重复计算
            match_info = self._calculate_match_info(
                item, title, ori_title, release_date
            )

            if match_info["exact_match"]:
                # 完全匹配
                bangumi_id = self._extract_bangumi_id(item)
                if bangumi_id:
                    exact_matches.append((item, bangumi_id, match_info["match_type"]))

                    # 如果找到完全匹配，可以提前退出（除非需要检查日期）
                    if not release_date or len(exact_matches) >= 3:
                        break
            elif match_info["score"] > 0.4:
                # 部分匹配
                bangumi_id = self._extract_bangumi_id(item)
                if bangumi_id:
                    partial_matches.append((item, match_info["score"], bangumi_id))

                    # 限制部分匹配的数量以提高性能
                    if len(partial_matches) >= 10:
                        break

        return exact_matches, partial_matches, processed_count

    # ----- 选择最佳匹配 -----

    def _select_from_exact_matches(
        self,
        title: str,
        exact_matches: list,
        partial_matches: list,
        release_date: str,
        season: int,
    ) -> tuple[str, str, bool] | None:
        """从完全匹配结果中选择最佳匹配

        Returns:
            匹配结果或 None
        """
        # 按匹配类型排序，优先使用中文翻译匹配
        exact_matches.sort(key=lambda x: x[2])

        if release_date:
            min_exact_diff = float("inf")
            best_exact_match = None

            # 找到日期最近的完全匹配
            for match_item, match_id, match_type in exact_matches:
                if "begin" in match_item:
                    diff = self._date_diff(match_item["begin"], release_date)
                    if diff < min_exact_diff:
                        min_exact_diff = diff
                        best_exact_match = (match_item, match_id, match_type)

            # 当完全匹配的最佳日期相差大于 180 天时，检查部分匹配列表中是否存在日期更接近的条目
            # 用于处理同名 OVA 或前传导致误判正片的情况
            if min_exact_diff > 180 and partial_matches:
                logger.debug(
                    f"完全匹配的最佳日期误差高达 {min_exact_diff} 天，启动全局日期择优机制"
                )
                min_partial_diff = float("inf")
                best_partial_match = None

                for match_item, score, match_id in partial_matches:
                    if "begin" in match_item:
                        diff = self._date_diff(match_item["begin"], release_date)
                        if diff < min_partial_diff:
                            min_partial_diff = diff
                            best_partial_match = (match_item, match_id, score)

                # 若部分匹配中有日期误差小于等于 90 天的条目，启动安全校验决定是否采用该部分匹配条目
                if best_partial_match and min_partial_diff <= 90:
                    pm_item = best_partial_match[0]
                    pm_score = best_partial_match[2]
                    # 收集该条目的原名和所有中文翻译
                    pm_all_names = [
                        pm_item.get("title", "")
                    ] + self._get_zh_hans_titles(pm_item)

                    # 安全校验：搜索词必须被包含在候选名的其中之一里，模糊匹配相似度 > 0.8，或关键字符一致
                    is_safe_to_override = (
                        pm_score >= 0.8
                        or any(title in name for name in pm_all_names)
                        or any(
                            self._check_key_characters(title, name)
                            for name in pm_all_names
                        )
                    )

                    if is_safe_to_override:
                        matched_title = self._get_best_matched_title(pm_item)
                        logger.debug(
                            f"因完全匹配结果日期差异过大，采纳日期择优番剧: {matched_title} (日期差距 {min_partial_diff} 天)"
                        )
                        return (best_partial_match[1], matched_title, True)
                    else:
                        logger.debug(
                            f"拒绝日期择优: {pm_item.get('title', '')} 虽然日期接近，但名称差异过大"
                        )

            # 处理存在多个完全匹配的情况，返回日期最接近的条目
            if len(exact_matches) > 1 and best_exact_match:
                logger.debug(
                    f"从多个完全匹配中择优: {best_exact_match[0].get('title', '')}, 日期差距: {min_exact_diff}天"
                )
                matched_title = self._get_best_matched_title(best_exact_match[0])
                date_matched = season > 1 and min_exact_diff <= 180
                return (best_exact_match[1], matched_title, date_matched)

        # 返回最高优先级的匹配结果
        result_item = exact_matches[0][0]
        zh_hans = result_item.get("titleTranslate", {}).get("zh-Hans", [])
        zh_hans_str = ", ".join(zh_hans) if zh_hans else ""
        logger.debug(
            f"找到匹配的番剧: {result_item.get('title', '')}, 中文翻译: {zh_hans_str}, bangumi_id: {exact_matches[0][1]}, 匹配方式: {exact_matches[0][2]}"
        )
        # 获取匹配到的标题
        matched_title = self._get_best_matched_title(result_item)
        # 没有通过日期筛选，标记为非日期匹配
        return (exact_matches[0][1], matched_title, False)

    def _select_from_partial_matches(
        self,
        partial_matches: list,
    ) -> tuple[str, str, bool] | None:
        """从部分匹配结果中选择最佳匹配（模糊匹配）

        Returns:
            匹配结果或 None
        """
        logger.debug("没有找到完全匹配的番剧，尝试进行模糊匹配...")

        # 按匹配度排序
        partial_matches.sort(key=lambda x: x[1], reverse=True)

        if self.verbose_logging:
            logger.debug(f"找到 {len(partial_matches)} 个可能的匹配项:")
            for i, (item, score, _) in enumerate(partial_matches[:5]):
                zh_hans = item.get("titleTranslate", {}).get("zh-Hans", [])
                zh_hans_str = ", ".join(zh_hans) if zh_hans else ""
                logger.debug(
                    f"  {i + 1}. {item.get('title', '')}, 中文翻译: {zh_hans_str}, 匹配度: {score}"
                )

        if partial_matches[0][1] >= 0.6:
            best_match = partial_matches[0][0]
            highest_score = partial_matches[0][1]
            bangumi_id = partial_matches[0][2]
            zh_hans = best_match.get("titleTranslate", {}).get("zh-Hans", [])
            zh_hans_str = ", ".join(zh_hans) if zh_hans else ""
            logger.debug(
                f"找到最佳匹配的番剧: {best_match.get('title', '')}, 中文翻译: {zh_hans_str}, bangumi_id: {bangumi_id}, 匹配度: {highest_score}"
            )
            # 获取匹配到的标题
            matched_title = self._get_best_matched_title(best_match)
            # 模糊匹配标记为非日期匹配
            return (bangumi_id, matched_title, False)

        return None

    # ----- 优化查找主流程 -----

    def _find_bangumi_id_optimized(
        self,
        title: str,
        ori_title: str = None,
        release_date: str = None,
        original_title: str = None,
        season: int = 1,
        media_type: str = "",
    ) -> tuple[str, str, bool] | None:
        """优化的番剧ID查找算法，避免重复计算相似度

        Returns:
            Optional[tuple[str, str, bool]]: (bangumi_id, matched_title, date_matched) 或 None
            date_matched: 是否通过日期匹配找到的（用于判断季度ID的可信度）
        """
        # 首先尝试精确匹配索引（O(1)查找，避免线性扫描）
        result = self._try_exact_match(title, ori_title, release_date, media_type)
        if result:
            return result

        # 精确索引未命中，回退到线性扫描模糊匹配
        logger.debug("开始尝试完全匹配...")
        exact_matches, partial_matches, processed_count = self._scan_candidates(
            title, ori_title, release_date
        )

        if self.verbose_logging:
            logger.debug(
                f"处理了 {processed_count} 个项目，找到 {len(exact_matches)} 个完全匹配，{len(partial_matches)} 个部分匹配"
            )

        # 处理完全匹配
        if exact_matches:
            return self._select_from_exact_matches(
                title, exact_matches, partial_matches, release_date, season
            )

        # 处理部分匹配
        if partial_matches:
            result = self._select_from_partial_matches(partial_matches)
            if result:
                return result

        # 如果处理过标题，再用原始标题尝试一次
        if original_title and original_title != title:
            logger.debug(f"使用原始标题 {original_title} 再次尝试匹配")
            return self._find_bangumi_id_optimized(
                original_title, ori_title, release_date, None, season, media_type
            )

        logger.debug("未找到匹配的番剧 ID")
        return None

    # ----- 标题匹配辅助 -----

    def _match_title_fuzzy(self, item: dict, title: str, ori_title: str = None) -> bool:
        """检查番剧条目是否可能匹配给定的标题（模糊匹配）"""
        if not item or "title" not in item:
            return False

        # 检查中文标题包含关系或高度相似
        if title:
            # 首先检查中文翻译
            if "titleTranslate" in item and "zh-Hans" in item["titleTranslate"]:
                for zh_title in item["titleTranslate"]["zh-Hans"]:
                    # 检查包含关系
                    if title in zh_title or zh_title in title:
                        return True
                    # 检查高度相似（相似度>0.7）
                    similarity = fuzz.ratio(zh_title, title) / 100.0
                    if similarity > 0.7:
                        return True

        # 检查原始标题包含关系
        if ori_title and "title" in item:
            if ori_title in item["title"] or item["title"] in ori_title:
                return True

        # 用中文标题检查原始标题包含关系
        if title and "title" in item:
            if title in item["title"] or item["title"] in title:
                return True

        return False

    def _match_title(self, item: dict, title: str, ori_title: str = None) -> str | None:
        """
        检查番剧条目是否匹配给定的标题

        返回:
            匹配的类型，用于排序优先级，None表示不匹配
            'zh-hans': 中文翻译匹配（优先级最高）
            'title': 原始标题匹配
        """
        if not item or "title" not in item:
            return None

        # 1. 首先检查中文翻译匹配 (优先级最高)
        if title and "titleTranslate" in item and "zh-Hans" in item["titleTranslate"]:
            if title in item["titleTranslate"]["zh-Hans"]:
                return "zh-hans"

        # 2. 检查原始标题匹配
        if ori_title and item["title"] == ori_title:
            return "title"
        elif title and item["title"] == title:  # 用中文标题也匹配原始标题字段
            return "title"

        return None

    def _get_zh_hans_titles(self, item: dict) -> list[str]:
        """获取条目的所有中文标题"""
        titles = []

        # 添加原始标题（如果存在）
        if "title" in item:
            titles.append(item["title"])

        # 添加中文翻译标题
        if "titleTranslate" in item and "zh-Hans" in item["titleTranslate"]:
            titles.extend(item["titleTranslate"]["zh-Hans"])

        return titles

    def _get_best_matched_title(self, item: dict) -> str:
        """获取最佳匹配的标题（优先返回中文标题）"""
        # 优先返回中文翻译标题
        if "titleTranslate" in item and "zh-Hans" in item["titleTranslate"]:
            zh_titles = item["titleTranslate"]["zh-Hans"]
            if zh_titles:
                return zh_titles[0]  # 返回第一个中文标题

        # 如果没有中文标题，返回原始标题
        return item.get("title", "")

    def _is_date_close(self, date1: str, date2: str, max_days: int = 60) -> bool:
        """检查两个日期是否在允许的范围内"""
        try:
            diff = self._date_diff(date1, date2)
            return diff <= max_days
        except Exception:
            return True  # 如果日期解析失败，默认认为日期匹配

    def _check_key_characters(self, title1: str, title2: str) -> bool:
        """检查两个标题的关键字符是否匹配"""
        if not title1 or not title2:
            return False

        # 提取关键字符（去除常见的无意义字符）
        def extract_key_chars(text: str) -> str:
            # 去除空格、标点符号等
            text = re.sub(r"[^\u4e00-\u9fff\w]", "", text)
            return text.lower()

        key1 = extract_key_chars(title1)
        key2 = extract_key_chars(title2)

        # 如果关键字符完全相同，返回True
        if key1 == key2:
            return True

        # 检查关键字符的相似度
        if len(key1) > 3 and len(key2) > 3:
            similarity = fuzz.ratio(key1, key2) / 100.0
            return similarity > 0.9  # 90%相似度认为匹配

        return False

    def _date_diff(self, date1: str, date2: str) -> int:
        """计算两个日期之间的天数差"""
        try:
            d1 = datetime.strptime(date1[:10], "%Y-%m-%d")
            d2 = datetime.strptime(date2[:10], "%Y-%m-%d")
            return abs((d2 - d1).days)
        except Exception as e:
            logger.error(f"计算日期差异时出错: {e}")
            return 999999  # 返回一个非常大的数字表示不匹配

    def _calculate_match_info(
        self, item: dict, title: str, ori_title: str = None, release_date: str = None
    ) -> dict:
        """一次性计算所有匹配信息，避免重复计算"""
        result = {
            "exact_match": False,
            "match_type": None,
            "score": 0.0,
            "best_zh_score": 0.0,
            "best_zh_title": "",
        }

        # 检查中文翻译匹配
        if title and "titleTranslate" in item and "zh-Hans" in item["titleTranslate"]:
            for zh_title in item["titleTranslate"]["zh-Hans"]:
                # 检查完全相等
                if title == zh_title:
                    result["exact_match"] = True
                    result["match_type"] = "zh-hans"
                    result["score"] = 1.0
                    return result

                # 计算相似度
                similarity = fuzz.ratio(zh_title, title) / 100.0
                if similarity > result["best_zh_score"]:
                    result["best_zh_score"] = similarity
                    result["best_zh_title"] = zh_title

                # 检查高度相似（相似度>0.9）
                if similarity > 0.9:
                    result["exact_match"] = True
                    result["match_type"] = "zh-hans"
                    result["score"] = similarity
                    return result

        # 检查原始标题匹配
        if ori_title and "title" in item:
            if ori_title == item["title"]:
                result["exact_match"] = True
                result["match_type"] = "title"
                result["score"] = 1.0
                return result

        if title and "title" in item and not ori_title:
            if title == item["title"]:
                result["exact_match"] = True
                result["match_type"] = "title"
                result["score"] = 1.0
                return result

        # 如果没有完全匹配，计算模糊匹配分数
        score = 0.0

        # 中文翻译匹配得分
        if result["best_zh_score"] > 0:
            # 检查是否包含关系
            if (
                title
                and "titleTranslate" in item
                and "zh-Hans" in item["titleTranslate"]
            ):
                for zh_title in item["titleTranslate"]["zh-Hans"]:
                    if title in zh_title or zh_title in title:
                        score += 0.15
                        break

            # 检查高度相似的中文标题（相似度>0.8）
            if result["best_zh_score"] > 0.8:
                score += 0.2

            # 检查关键字符匹配
            if self._check_key_characters(title, result["best_zh_title"]):
                score += 0.1

            # 中文翻译匹配权重60%
            score += result["best_zh_score"] * 0.6

        # 原标题匹配得分
        if ori_title and "title" in item:
            similarity = fuzz.ratio(item["title"], ori_title) / 100.0
            score += similarity * 0.3

            if ori_title in item["title"] or item["title"] in ori_title:
                score += 0.1

        # 用中文标题匹配原始标题
        if title and "title" in item and not ori_title:
            similarity = fuzz.ratio(item["title"], title) / 100.0
            score += similarity * 0.2

            if title in item["title"] or item["title"] in title:
                score += 0.1

        # 发布日期匹配得分
        if release_date and "begin" in item:
            if self._is_date_close(item["begin"], release_date, 30):
                score += 0.15
            elif self._is_date_close(item["begin"], release_date, 120):
                score += 0.05

        result["score"] = min(score, 1.0)
        return result

    def _calculate_match_score(
        self, item: dict, title: str, ori_title: str = None, release_date: str = None
    ) -> float:
        """计算条目与给定信息的匹配得分（保持向后兼容）"""
        match_info = self._calculate_match_info(item, title, ori_title, release_date)
        return match_info["score"]

    def _extract_bangumi_id(self, item: dict) -> str | None:
        """从番剧条目中提取 bangumi id"""
        if not item or "sites" not in item:
            return None

        for site in item.get("sites", []):
            if site.get("site") == "bangumi":
                site_id = site.get("id")
                if site_id:
                    return site_id

        return None

    # ----- 调试用搜索 -----

    def search_title(self, title: str) -> list[dict]:
        """
        搜索指定标题的所有可能匹配项，用于调试

        Args:
            title: 搜索的标题

        Returns:
            匹配的条目列表
        """
        results = []

        for item in self._parse_data():
            # 检查标题或标题的一部分是否匹配
            jp_title = item.get("title", "")
            zh_hans_titles = []

            if "titleTranslate" in item and "zh-Hans" in item["titleTranslate"]:
                zh_hans_titles = item["titleTranslate"]["zh-Hans"]

            match_found = False
            # 检查原始标题
            if title.lower() in jp_title.lower() or jp_title.lower() in title.lower():
                match_found = True

            # 检查中文翻译
            if not match_found:
                for zh_title in zh_hans_titles:
                    if (
                        title.lower() in zh_title.lower()
                        or zh_title.lower() in title.lower()
                    ):
                        match_found = True
                        break

            if match_found:
                bangumi_id = self._extract_bangumi_id(item)
                if bangumi_id:
                    results.append(
                        {
                            "title": jp_title,
                            "zh_hans": zh_hans_titles,
                            "begin": item.get("begin", ""),
                            "bangumi_id": bangumi_id,
                        }
                    )

        return results
