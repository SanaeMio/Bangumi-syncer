"""Bangumi Archive 离线查询层（第一期：数据库基础设施）

数据源：https://github.com/bangumi/Archive
双库互备：a/b 两个 SQLite 库互为可用，导入期间零停服。
默认关闭：通过配置 [bangumi-archive] enabled = true 启用。
"""

from __future__ import annotations

from ._archive import BangumiArchive, bangumi_archive

__all__ = ["BangumiArchive", "bangumi_archive"]
