"""BangumiApi 搜索与匹配（mixin）"""

import datetime
import os

from rapidfuzz import fuzz

from ...core.logging import logger


class SearchMixin:
    """搜索与匹配相关方法（供 BangumiApi 组合）"""

    def get_me(self):
        res = self.get("me")
        if 400 <= res.status_code < 500:
            # 发送API认证失败通知
            from ..notifier import send_notify

            send_notify(
                "api_auth_error",
                user_name=self.username,
                status_code=res.status_code,
                error_message="BangumiApi: 未授权, access_token不正确或未设置",
            )
            if os.name == "nt":
                os.startfile(f"{self.next_base}/demo/access-token")
            raise ValueError("BangumiApi: 未授权, access_token不正确或未设置")
        return res.json()

    def search(self, title, start_date, end_date, limit=5, list_only=True):
        # 使用实例缓存避免内存泄漏
        cache_key = (title, start_date, end_date, limit, list_only)
        if cache_key in self._cache["search"]:
            return self._cache["search"][cache_key]

        res = self._request_with_retry(
            "POST",
            self._req_not_auth,
            f"{self.host}/search/subjects",
            json={
                "keyword": title,
                "filter": {
                    "type": [2],
                    "air_date": [f">={start_date}", f"<{end_date}"],
                    "nsfw": True,
                },
            },
            params={"limit": limit},
        )
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

    def search_old(self, title, list_only=True):
        # 使用实例缓存避免内存泄漏
        cache_key = (title, list_only)
        if cache_key in self._cache["search_old"]:
            return self._cache["search_old"][cache_key]

        res = self._request_with_retry(
            "GET",
            self.req,
            f"{self.api_base}/search/subject/{title}",
            params={"type": 2},
        )
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(f"search_old API返回非字典类型: {type(res)}, 内容: {res}")
                res = {"results": 0, "list": []}
        except Exception as e:
            logger.error(f"search_old JSON解析失败: {e}")
            res = {"results": 0, "list": []}

        result = res.get("list", []) if list_only else res
        self._put_cache("search_old", cache_key, result)
        return result

    def get_subject(self, subject_id):
        # 使用实例缓存避免内存泄漏
        if subject_id in self._cache["get_subject"]:
            return self._cache["get_subject"][subject_id]

        res = self.get(f"subjects/{subject_id}")
        try:
            res = res.json()
            # 确保返回的是字典类型
            if not isinstance(res, dict):
                logger.error(f"get_subject API返回非字典类型: {type(res)}, 内容: {res}")
                res = {}
        except ValueError as e:
            logger.error(f"get_subject JSON解析失败: {e}")
            res = {}

        self._put_cache("get_subject", subject_id, res)
        return res

    def get_related_subjects(self, subject_id):
        # 使用实例缓存避免内存泄漏
        if subject_id in self._cache["get_related_subjects"]:
            return self._cache["get_related_subjects"][subject_id]

        res = self.get(f"subjects/{subject_id}/subjects")
        try:
            res = res.json()
            # get_related_subjects 可能返回列表或字典，都是正常的
            if not isinstance(res, (dict, list)):
                logger.error(
                    f"get_related_subjects API返回异常类型: {type(res)}, 内容: {res}"
                )
                res = []
        except Exception as e:
            logger.error(f"get_related_subjects JSON解析失败: {e}")
            res = []

        self._put_cache("get_related_subjects", subject_id, res)
        return res

    def bgm_search(self, title, ori_title, premiere_date: str, is_movie=False):
        bgm_data = None
        start_date_str = "无日期"
        end_date_str = "无日期"

        # 尝试使用 v0 接口进行带首播日期的精确搜索
        if premiere_date and len(premiere_date) >= 10:
            try:
                air_date = datetime.datetime.fromisoformat(premiere_date[:10])
                start_date = air_date - datetime.timedelta(days=2)
                end_date = air_date + datetime.timedelta(days=2)

                start_date_str = start_date.strftime("%Y-%m-%d")
                end_date_str = end_date.strftime("%Y-%m-%d")

                if ori_title:
                    bgm_data = self.search(
                        title=ori_title,
                        start_date=start_date_str,
                        end_date=end_date_str,
                    )
                bgm_data = bgm_data or self.search(
                    title=title, start_date=start_date_str, end_date=end_date_str
                )

                if not bgm_data and is_movie:
                    movie_search_title = ori_title or title
                    movie_end_date = air_date + datetime.timedelta(days=200)
                    end_date_str = movie_end_date.strftime("%Y-%m-%d")
                    bgm_data = self.search(
                        title=movie_search_title,
                        start_date=start_date_str,
                        end_date=end_date_str,
                    )
            except ValueError:
                logger.warning(
                    f"首播日期格式解析失败: {premiere_date}，降级至无日期模式搜索"
                )

        # 若精确搜索无结果或相似度低于阈值，使用旧版接口进行无日期名称搜索
        if not bgm_data or (
            bgm_data
            and len(bgm_data) > 0
            and self.title_diff_ratio(
                title=title, ori_title=ori_title, bgm_data=bgm_data[0]
            )
            < 0.5
        ):
            # 过滤无效的空标题
            search_titles = [t for t in (ori_title, title) if t and t.strip()]

            for t in search_titles:
                bgm_data_old = self.search_old(title=t)

                if bgm_data_old and len(bgm_data_old) > 0:
                    # 旧版接口返回数据不含 infobox 别名信息，需拉取完整条目进行准确相似度计算
                    subject_id = bgm_data_old[0]["id"]
                    full_info = self.get_subject(subject_id)

                    if (
                        full_info
                        and self.title_diff_ratio(title, ori_title, bgm_data=full_info)
                        > 0.5
                    ):
                        # 包装为统一的列表格式返回
                        bgm_data = [full_info]
                        break
            else:
                bgm_data = None

        if not bgm_data or len(bgm_data) == 0:
            return None

        logger.debug(
            f"搜索日期区间: {start_date_str} 至 {end_date_str} | 结果: {bgm_data[0].get('name')}"
        )
        return bgm_data

    @staticmethod
    def title_diff_ratio(title, ori_title, bgm_data):
        ori_title = ori_title or title
        candidates = []

        # 提取基础候选项：原名与中文名
        if bgm_data.get("name"):
            candidates.append(bgm_data["name"])
        if bgm_data.get("name_cn"):
            candidates.append(bgm_data["name_cn"])

        # 提取 infobox 中的别名，兼容多种历史数据格式
        infobox = bgm_data.get("infobox", [])
        if isinstance(infobox, list):
            for info in infobox:
                if info.get("key") == "别名":
                    alias_value = info.get("value")
                    if isinstance(alias_value, list):
                        for alias_item in alias_value:
                            if isinstance(alias_item, dict) and "v" in alias_item:
                                candidates.append(alias_item["v"])
                            elif isinstance(alias_item, str):
                                candidates.append(alias_item)
                    elif isinstance(alias_value, str):
                        candidates.append(alias_value)
                    break

        # 计算所有候选项的相似度，取最大值
        max_ratio = 0.0
        for candidate in candidates:
            if not candidate:
                continue

            ratio_title = fuzz.ratio(candidate, title) / 100.0
            ratio_ori = fuzz.ratio(candidate, ori_title) / 100.0
            max_ratio = max(max_ratio, ratio_title, ratio_ori)

            # 若发现完全匹配，提前返回
            if max_ratio >= 1.0:
                return 1.0

        return max_ratio
