import os
import re
import time
import warnings
from collections.abc import Generator
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

import ijson
import requests

from ..core.config import config_manager
from ..core.logging import logger

# 使用全局logger实例


def _request_with_retry(
    url, proxies=None, stream=False, max_retries=3, ssl_verify=True
):
    """带重试机制的HTTP请求方法"""
    # 如果禁用SSL验证，抑制urllib3的警告
    if not ssl_verify:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        from urllib3.exceptions import InsecureRequestWarning

        warnings.filterwarnings("ignore", category=InsecureRequestWarning)

    for attempt in range(max_retries + 1):
        try:
            response = requests.get(
                url, proxies=proxies, stream=stream, verify=ssl_verify
            )
            response.raise_for_status()

            # 检查是否需要重试的状态码
            if response.status_code in [429, 500, 502, 503, 504]:
                if attempt < max_retries:
                    delay = 2**attempt  # 指数退避: 2, 4, 8秒
                    logger.error(
                        f"HTTP {response.status_code} 错误，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                    )
                    time.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"HTTP {response.status_code} 错误，已达到最大重试次数 {max_retries}"
                    )

            return response

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.RequestException,
        ) as e:
            if attempt < max_retries:
                delay = 2**attempt  # 指数退避: 2, 4, 8秒
                logger.error(
                    f"请求异常: {str(e)}，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                )
                time.sleep(delay)
                continue
            else:
                logger.error(f"请求异常: {str(e)}，已达到最大重试次数 {max_retries}")
                raise e

    return response


class BangumiData:
    """处理 bangumi-data 数据的类"""

    def __init__(self):
        self.data_url = config_manager.get(
            "bangumi-data",
            "data_url",
            fallback="https://unpkg.com/bangumi-data@0.3/dist/data.json",
        )
        self.local_cache_path = config_manager.get(
            "bangumi-data", "local_cache_path", fallback="./bangumi_data_cache.json"
        )
        self.http_proxy = config_manager.get(
            "bangumi-data",
            "http_proxy",
            fallback=config_manager.get("dev", "script_proxy", fallback=""),
        )
        self.ssl_verify = config_manager.get("dev", "ssl_verify", fallback=True)
        self.use_cache = config_manager.get("bangumi-data", "use_cache", fallback=True)
        # 缓存有效期（天），默认7天
        self.cache_ttl_days = config_manager.get(
            "bangumi-data", "cache_ttl_days", fallback=7
        )
        self._cached_data = None
        self._cache_items = None
        # 内存缓存，避免重复解析文件
        self._data_cache = None
        self._cache_timestamp = None
        self._cache_hit_count = 0  # 缓存命中次数
        self._cache_miss_count = 0  # 缓存未命中次数
        # 是否启用更详细的日志，用于调试匹配问题
        self.verbose_logging = config_manager.get("dev", "debug", fallback=False)

        # 启动时检查缓存，如果缺少则下载
        self._check_and_download_cache_on_startup()

        # 启动时预加载数据到内存
        self._preload_data_to_memory()

    def _is_cache_valid(self) -> bool:
        """检查缓存是否有效（未过期）"""
        if not os.path.exists(self.local_cache_path):
            return False

        try:
            # 获取文件最后修改时间
            mtime = os.path.getmtime(self.local_cache_path)
            last_modified = datetime.fromtimestamp(mtime)
            now = datetime.now()

            # 如果缓存时间小于设定的TTL，则缓存有效
            return (now - last_modified) < timedelta(days=self.cache_ttl_days)
        except Exception as e:
            logger.error(f"检查缓存有效期时出错: {e}")
            return False

    def _download_data(self) -> bool:
        """从远程下载 bangumi-data 数据

        返回:
            bool: 下载是否成功
        """
        logger.debug(f"正在从 {self.data_url} 下载 bangumi-data...")

        proxies = {}
        if self.http_proxy:
            proxies = {"http": self.http_proxy, "https": self.http_proxy}

        try:
            response = _request_with_retry(
                self.data_url, proxies=proxies, stream=True, ssl_verify=self.ssl_verify
            )

            # 确保缓存目录存在
            cache_dir = os.path.dirname(self.local_cache_path)
            if cache_dir and not os.path.exists(cache_dir):
                os.makedirs(cache_dir, exist_ok=True)

            # 如果设置了使用缓存，则保存到本地
            if self.use_cache:
                with open(self.local_cache_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                logger.debug(f"bangumi-data 已缓存到 {self.local_cache_path}")

            return True
        except Exception as e:
            logger.error(f"下载 bangumi-data 失败: {e}")
            return False

    def _ensure_fresh_data(self) -> bool:
        """确保数据是最新的

        如果缓存不存在或已过期，则重新下载

        返回:
            bool: 是否成功确保数据最新
        """
        if not self.use_cache:
            return True

        if not self._is_cache_valid():
            logger.debug("缓存不存在或已过期，正在重新下载数据...")
            return self._download_data()

        return True

    def _parse_data(self) -> Generator[dict, None, None]:
        """解析数据，以生成器方式返回，使用内存缓存避免重复解析"""
        # 首先确保数据是最新的
        self._ensure_fresh_data()

        # 检查内存缓存是否有效
        if self._data_cache is not None:
            self._cache_hit_count += 1
            logger.debug(f"使用内存缓存数据 (命中次数: {self._cache_hit_count})")
            for item in self._data_cache:
                yield item
            return

        self._cache_miss_count += 1
        logger.debug(f"缓存未命中，重新解析数据 (未命中次数: {self._cache_miss_count})")

        # 如果没有缓存，先解析数据到内存
        logger.debug("解析数据到内存缓存")
        items = []

        if self.use_cache and os.path.exists(self.local_cache_path):
            # 从缓存文件中解析
            try:
                with open(self.local_cache_path, "rb") as f:
                    for item in ijson.items(f, "items.item"):
                        items.append(item)
            except Exception as e:
                logger.error(f"从缓存解析 bangumi-data 失败: {e}")

                # 如果缓存文件解析失败，尝试重新下载
                if self._download_data():
                    with open(self.local_cache_path, "rb") as f:
                        for item in ijson.items(f, "items.item"):
                            items.append(item)
        else:
            # 从网络直接解析
            try:
                proxies = {}
                if self.http_proxy:
                    proxies = {"http": self.http_proxy, "https": self.http_proxy}

                with _request_with_retry(
                    self.data_url,
                    proxies=proxies,
                    stream=True,
                    ssl_verify=self.ssl_verify,
                ) as response:
                    for item in ijson.items(response.raw, "items.item", use_float=True):
                        items.append(item)
            except Exception as e:
                logger.error(f"流式解析 bangumi-data 失败: {e}")
                # 如果网络请求失败，但有缓存文件，尝试使用缓存
                if os.path.exists(self.local_cache_path):
                    logger.debug(f"尝试使用缓存文件 {self.local_cache_path}")
                    with open(self.local_cache_path, "rb") as f:
                        for item in ijson.items(f, "items.item"):
                            items.append(item)

        # 更新内存缓存
        self._data_cache = items
        self._cache_timestamp = time.time()

        # 从内存缓存中yield数据
        for item in items:
            yield item

    def find_bangumi_id(
        self,
        title: str,
        ori_title: str = None,
        release_date: str = None,
        season: int = 1,
    ) -> Optional[tuple[str, str, bool]]:
        """
        根据标题和其他信息查找 bangumi id

        Args:
            title: 中文标题
            ori_title: 原版标题（通常是日文）
            release_date: 发布日期，格式为 YYYY-MM-DD
            season: 季度，默认为 1（第一季）

        Returns:
            找到匹配的 (bangumi_id, matched_title, date_matched) 或 None
            date_matched: 是否通过日期匹配找到的（用于判断季度ID的可信度）
        """
        logger.debug(
            f"正在查找番剧 ID: {title=}, {ori_title=}, {release_date=}, {season=}"
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
            title, ori_title, release_date, original_title, season
        )
        if result:
            return result
        return None

    def _find_bangumi_id_optimized(
        self,
        title: str,
        ori_title: str = None,
        release_date: str = None,
        original_title: str = None,
        season: int = 1,
    ) -> Optional[tuple[str, str, bool]]:
        """优化的番剧ID查找算法，避免重复计算相似度

        Returns:
            Optional[tuple[str, str, bool]]: (bangumi_id, matched_title, date_matched) 或 None
            date_matched: 是否通过日期匹配找到的（用于判断季度ID的可信度）
        """

        # 首先检查完全匹配
        logger.debug("开始尝试完全匹配...")
        exact_matches = []
        partial_matches = []

        # 优化：先进行快速预筛选
        processed_count = 0

        for item in self._parse_data():
            processed_count += 1

            # 快速预筛选：检查是否有中文翻译
            if title and (
                "titleTranslate" not in item or "zh-Hans" not in item["titleTranslate"]
            ):
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

        if self.verbose_logging:
            logger.debug(
                f"处理了 {processed_count} 个项目，找到 {len(exact_matches)} 个完全匹配，{len(partial_matches)} 个部分匹配"
            )

        # 处理完全匹配
        if len(exact_matches) > 0:
            # 按匹配类型排序，优先使用中文翻译匹配
            exact_matches.sort(key=lambda x: x[2])

            if release_date and len(exact_matches) > 1:
                # 如果有多个相同类型的匹配，使用日期最接近的
                match_type = exact_matches[0][2]
                matches_of_same_type = [m for m in exact_matches if m[2] == match_type]

                if len(matches_of_same_type) > 1:
                    logger.debug(
                        f"发现 {len(matches_of_same_type)} 个相同类型的匹配，使用日期进行进一步筛选"
                    )
                    closest_match = None
                    min_diff = float("inf")

                    for match_item, match_id, _ in matches_of_same_type:
                        if "begin" in match_item:
                            diff = self._date_diff(match_item["begin"], release_date)
                            logger.debug(
                                f"  候选: {match_item.get('title', '')} (ID: {match_id}), "
                                f"日期: {match_item['begin']}, 与目标日期差距: {diff}天"
                            )
                            if diff < min_diff:
                                min_diff = diff
                                closest_match = (match_item, match_id)

                    if closest_match:
                        logger.debug(
                            f"找到最佳日期匹配的番剧: {closest_match[0].get('title', '')}, "
                            f"bangumi_id: {closest_match[1]}, 日期差距: {min_diff}天"
                        )
                        # 获取匹配到的标题
                        matched_title = self._get_best_matched_title(closest_match[0])
                        # 如果是通过日期匹配找到的，且是非第一季，标记为可信的季度ID
                        date_matched = season > 1
                        if date_matched:
                            logger.info(
                                f"通过日期匹配找到特定季度 (season={season})，"
                                f"标记为可信的季度ID，跳过续集遍历"
                            )
                        return (closest_match[1], matched_title, date_matched)

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

        # 处理部分匹配
        if partial_matches:
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

        # 如果处理过标题，再用原始标题尝试一次
        if original_title and original_title != title:
            logger.debug(f"使用原始标题 {original_title} 再次尝试匹配")
            return self._find_bangumi_id_optimized(
                original_title, ori_title, release_date, None, season
            )

        logger.debug("未找到匹配的番剧 ID")
        return None

    def get_cache_stats(self) -> dict:
        """获取缓存统计信息

        返回:
            Dict: 包含缓存统计信息的字典
        """
        total_requests = self._cache_hit_count + self._cache_miss_count
        hit_rate = (
            (self._cache_hit_count / total_requests * 100) if total_requests > 0 else 0
        )

        return {
            "cache_hits": self._cache_hit_count,
            "cache_misses": self._cache_miss_count,
            "total_requests": total_requests,
            "hit_rate": hit_rate,
            "cache_size": len(self._data_cache) if self._data_cache else 0,
            "cache_age_minutes": (time.time() - self._cache_timestamp) / 60
            if self._cache_timestamp
            else 0,
        }

    def clear_cache(self):
        """清理内存缓存"""
        self._data_cache = None
        self._cache_timestamp = None
        logger.debug("内存缓存已清理")

    def force_update(self) -> bool:
        """强制更新 bangumi-data 数据

        返回:
            bool: 更新是否成功
        """
        logger.info("强制更新 bangumi-data 数据...")
        return self._download_data()

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
                    similarity = SequenceMatcher(None, zh_title, title).ratio()
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

    def _match_title(
        self, item: dict, title: str, ori_title: str = None
    ) -> Optional[str]:
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
        def extract_key_chars(text):
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
            similarity = SequenceMatcher(None, key1, key2).ratio()
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
                similarity = SequenceMatcher(None, zh_title, title).ratio()
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
            similarity = SequenceMatcher(None, item["title"], ori_title).ratio()
            score += similarity * 0.3

            if ori_title in item["title"] or item["title"] in ori_title:
                score += 0.1

        # 用中文标题匹配原始标题
        if title and "title" in item and not ori_title:
            similarity = SequenceMatcher(None, item["title"], title).ratio()
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

    def _extract_bangumi_id(self, item: dict) -> Optional[str]:
        """从番剧条目中提取 bangumi id"""
        if not item or "sites" not in item:
            return None

        for site in item.get("sites", []):
            if site.get("site") == "bangumi":
                site_id = site.get("id")
                if site_id:
                    return site_id

        return None

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

    def _check_and_download_cache_on_startup(self):
        """启动时检查缓存，如果缺少则下载

        这个方法在类初始化时被调用，确保缓存文件存在且有效
        """
        if not self.use_cache:
            logger.debug("缓存功能已禁用，跳过缓存检查")
            return

        if not os.path.exists(self.local_cache_path):
            logger.info(f"缓存文件不存在: {self.local_cache_path}，正在下载...")
            success = self._download_data()
            if success:
                logger.info("缓存文件下载成功")
            else:
                logger.warning("缓存文件下载失败，将在需要时尝试从网络获取数据")
        elif not self._is_cache_valid():
            logger.info(f"缓存文件已过期（超过 {self.cache_ttl_days} 天），正在更新...")
            success = self._download_data()
            if success:
                logger.info("缓存文件更新成功")
            else:
                logger.warning("缓存文件更新失败，将使用现有缓存文件")
        else:
            logger.debug("缓存文件存在且有效，无需下载")

    def _preload_data_to_memory(self):
        """初始化时预加载数据到内存"""
        try:
            logger.info("初始化时预加载 bangumi-data 到内存...")
            start_time = time.time()

            # 确保数据是最新的
            self._ensure_fresh_data()

            # 解析数据到内存
            items = []
            if self.use_cache and os.path.exists(self.local_cache_path):
                # 从缓存文件中解析
                try:
                    with open(self.local_cache_path, "rb") as f:
                        for item in ijson.items(f, "items.item"):
                            items.append(item)
                except Exception as e:
                    logger.error(f"预加载时从缓存解析 bangumi-data 失败: {e}")
                    return
            else:
                # 从网络直接解析
                try:
                    proxies = {}
                    if self.http_proxy:
                        proxies = {"http": self.http_proxy, "https": self.http_proxy}

                    with _request_with_retry(
                        self.data_url,
                        proxies=proxies,
                        stream=True,
                        ssl_verify=self.ssl_verify,
                    ) as response:
                        for item in ijson.items(
                            response.raw, "items.item", use_float=True
                        ):
                            items.append(item)
                except Exception as e:
                    logger.error(f"预加载时流式解析 bangumi-data 失败: {e}")
                    return

            # 更新内存缓存
            self._data_cache = items
            self._cache_timestamp = time.time()

            end_time = time.time()
            logger.info(
                f"预加载完成，共加载 {len(items)} 个项目，耗时 {end_time - start_time:.2f}秒"
            )

        except Exception as e:
            logger.error(f"预加载 bangumi-data 到内存失败: {e}")
            # 预加载失败不影响后续使用，会在第一次调用时重新加载
