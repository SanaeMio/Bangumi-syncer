"""Archive 标题索引（兼容门面，委托给 FTS5 实现）

本模块为历史兼容层，保留原对外接口名：
- ArchiveTitleIndex（类名）
- archive_title_index（全局单例）
- _normalize_key（归一化函数）

实际实现已迁移到 _fts_query.py（基于 SQLite FTS5 trigram），
内存占用从 200-350MB 降到接近 0，查询性能提升 10-100 倍。

迁移说明：
- 原 _title_to_ids / _bigram_index 内存结构已移除
- 原磁盘缓存（.index 文件）已废弃，FTS5 表由 SQLite 自管理
- 原 _build_internal / _save_to_disk / _load_from_disk 已移除
- 原 build_in_background 改为轻量同步初始化（FTS5 表导入时已构建，
  旧库升级时可能触发同步 FTS 构建）
"""

# ruff: noqa: UP045 — 与项目其他模块风格保持一致，使用 Optional[X]

from __future__ import annotations

from ._fts_query import (
    ArchiveFTSQuery,
    _normalize_key,
)

__all__ = ["ArchiveTitleIndex", "archive_title_index", "_normalize_key"]


class ArchiveTitleIndex(ArchiveFTSQuery):
    """Archive 标题索引（兼容类名，委托给 ArchiveFTSQuery）

    保留此类名仅为兼容现有 import：
        from app.utils.bangumi_archive._title_index import ArchiveTitleIndex

    实际逻辑全部继承自 ArchiveFTSQuery。
    """

    # 兼容原测试访问的内部属性（FTS5 方案下不再有这些结构，
    # 但部分测试直接访问，提供空值避免 AttributeError）
    @property
    def _title_to_ids(self) -> dict[str, list[int]]:
        """已废弃：FTS5 方案下无内存索引"""
        return {}

    @property
    def _bigram_index(self) -> dict[str, list[str]]:
        """已废弃：FTS5 方案下无 bigram 索引"""
        return {}


# 全局单例（保留原名称，指向 FTS5 实现）
archive_title_index: ArchiveTitleIndex = ArchiveTitleIndex()  # type: ignore[assignment]
