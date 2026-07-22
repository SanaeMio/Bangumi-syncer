"""BangumiData 索引构建 Mixin

职责：
- 构建 TMDB id → 番剧名映射（供 trakt 同步快速查找）
- 构建标题 → item 精确匹配索引（加速常用查找）
- 根据 TMDB id 与可选季度查询番剧名
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ...core.logging import logger

# 标题尾部季/部/篇/クール等序列标识符，用于检测条目是否附带季度信息以及多条目冲突时清理基标题。
# 覆盖：中文简繁体数字季期部、英文 Season/S/Part/Act/Phase、Unicode 罗马数字、日文クール。
_SEASON_SUFFIX = re.compile(
    r"\s*("
    # 第X季/第X期/第X部（简繁体中文数字 + 阿拉伯数字）
    r"[第]?\s*(?:\d+|[一二三四五六七八九十]+)\s*[季期部]"
    # Season X（大小写无关）
    r"|Season\s*\d+"
    # SX / Part X / Act X / Phase X
    r"|S\d+|Part\s*\d+|Act\s*\d+|Phase\s*\d+"
    # Unicode 罗马数字 ⅡⅢⅣⅤⅥⅦⅧⅨⅩ（连续出现视为一个整体）
    r"|[ⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+"
    # (第Xクール)
    r"|\([^)]*クール[^)]*\)"
    r")\s*$",
    re.IGNORECASE,
)


class IndexMixin:
    """索引构建与查询相关方法"""

    @staticmethod
    def _parse_begin(raw: str) -> datetime:
        """将 bangumi-data 的 begin 字段解析为 datetime，解析失败返回最大日期。"""
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return datetime.max.replace(tzinfo=timezone.utc)

    def _build_tmdb_mapping(self) -> None:
        """构建 TMDB id 到番剧名的映射。

        同一 TMDB 可能对应多个季度条目，同一 key 冲突时选择最优：
        - 优先选任意标题均不含季度标记的条目。
        - 均含标记时选元数据日期最早的条目。
        - 多条目时去除尾部季度标识符。
        """

        candidates: dict[str, list[dict[str, Any]]] = {}
        for item in self._parse_data():
            # 提取 TMDB id
            for site in item.get("sites", []):
                if site.get("site") == "tmdb":
                    tmdb_id = site.get("id")
                    title = item.get("title", "")
                    if not title.strip():
                        break
                    candidates.setdefault(tmdb_id, []).append(item)
                    break

        selected_count = 0
        multi_count = 0
        for tmdb_id, entries in candidates.items():
            if len(entries) == 1:
                item = entries[0]
                self._cache_tmdb_mapping[tmdb_id] = item.get("title", "")
                self._cache_tmdb_begin[tmdb_id] = item.get("begin", "") or ""
                selected_count += 1
            else:
                multi_count += 1
                self._cache_tmdb_mapping[tmdb_id] = self._pick_best_tmdb_title(
                    entries, strip_suffix=True
                )
                # begin 取选中条目的日期
                no_season = [e for e in entries if not self._has_season_indicator(e)]
                picks = no_season if no_season else entries
                picks.sort(key=lambda e: self._parse_begin(e.get("begin", "") or ""))
                self._cache_tmdb_begin[tmdb_id] = picks[0].get("begin", "") or ""

        logger.info(
            f"TMDB 映射构建完成，共 {len(candidates)} 个唯一 ID "
            f"({selected_count} 单节目, {multi_count} 多节目)"
        )

    @staticmethod
    def _item_all_titles(item: dict[str, Any]) -> list[str]:
        """提取条目的所有标题（原标题 + 简繁翻译），供季度检测。"""
        titles: list[str] = []
        raw = item.get("title", "")
        if raw.strip():
            titles.append(raw)
        tr = item.get("titleTranslate") or {}
        for lang in ("zh-Hans", "zh-Hant"):
            for t in tr.get(lang, []):
                if t and t.strip():
                    titles.append(t)
        return titles

    @classmethod
    def _has_season_indicator(cls, item: dict[str, Any]) -> bool:
        """检测条目的任一标题是否包含季度标记。"""
        for t in cls._item_all_titles(item):
            if _SEASON_SUFFIX.search(t):
                return True
        return False

    @classmethod
    def _pick_best_tmdb_title(
        cls, entries: list[dict[str, Any]], strip_suffix: bool = False
    ) -> str:
        """从同一 TMDB 的多条记录中选择最佳标题。

        优先级：任意标题均无季度标记 > 日期最早。
        因为同一季度不同 part 共享 TMDB，季标检测先于日期兜底。
        """

        no_season = [e for e in entries if not cls._has_season_indicator(e)]
        picks = no_season if no_season else entries
        picks.sort(key=lambda e: cls._parse_begin(e.get("begin", "") or ""))
        title = picks[0].get("title", "")

        if strip_suffix and len(entries) > 1:
            title = _SEASON_SUFFIX.sub("", title).strip()
        return title

    def _build_title_index(self) -> None:
        """构建标题→item 精确匹配索引，加速常用查找"""
        self._title_index.clear()
        for item in self._parse_data():
            # 原标题（通常是日文），过滤空白标题
            raw_title = item.get("title")
            if raw_title and raw_title.strip():
                self._title_index.setdefault(raw_title, []).append(item)
            # 中文翻译标题，过滤空白标题
            if "titleTranslate" in item and "zh-Hans" in item["titleTranslate"]:
                for zh_title in item["titleTranslate"]["zh-Hans"]:
                    if zh_title and zh_title.strip():
                        self._title_index.setdefault(zh_title, []).append(item)
        logger.info(f"标题索引构建完成，共 {len(self._title_index)} 个唯一标题")

    def get_title_by_tmdb_id(
        self, tmdb_id: str, season: int | None = None
    ) -> str | None:
        """根据 TMDB id 获取番剧名。

        Args:
            tmdb_id: TMDB id（形如 tv/94664 或 movie/10387）
            season: 季编号，提供时优先查 {tmdb_id}/season/{season}

        Returns:
            番剧名或 None
        """
        if season is not None:
            title = self._cache_tmdb_mapping.get(f"{tmdb_id}/season/{season}", None)
            if title:
                return title
        return self._cache_tmdb_mapping.get(tmdb_id, None)

    def get_begin_by_tmdb_id(self, tmdb_id: str, season: int | None = None) -> str:
        """根据 TMDB id 获取条目的开始日期。

        Args:
            tmdb_id: TMDB id（形如 tv/94664 或 movie/10387）
            season: 季编号，提供时优先查 {tmdb_id}/season/{season}

        Returns:
            开始日期字符串（空字符串表示未找到）
        """
        if not hasattr(self, "_cache_tmdb_begin"):
            return ""
        if season is not None:
            begin = self._cache_tmdb_begin.get(f"{tmdb_id}/season/{season}", "")
            if begin:
                return begin
        return self._cache_tmdb_begin.get(tmdb_id, "")
