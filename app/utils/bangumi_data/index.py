"""BangumiData 索引构建 Mixin

职责：
- 构建 TMDB id → 番剧名映射（供 trakt 同步快速查找）
- 构建标题 → item 精确匹配索引（加速常用查找）
- 根据 TMDB id 查询番剧名
"""

from __future__ import annotations

from ...core.logging import logger


class IndexMixin:
    """索引构建与查询相关方法"""

    def _build_tmdb_mapping(self):
        """构建 TMDB id 到番剧名的映射"""

        for item in self._parse_data():
            # 提取 TMDB id
            for site in item.get("sites", []):
                if site.get("site") == "tmdb":
                    tmdb_id = site.get("id")
                    self._cache_tmdb_mapping[tmdb_id] = item.get("title", "")
                    break

    def _build_title_index(self):
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

    def get_title_by_tmdb_id(self, tmdb_id: str) -> str | None:
        """根据 TMDB id 获取番剧名

        Args:
            tmdb_id: TMDB id

        Returns:
            番剧名或 None
        """
        return self._cache_tmdb_mapping.get(tmdb_id, None)
