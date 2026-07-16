"""
数据库管理模块（facade）

将原 ``database.py`` God Class 拆分为 ``database/`` 子包：
- :mod:`connection`   : :class:`DatabaseConnection`（连接、锁、schema 迁移）
- :mod:`sync_records` : :class:`SyncRecordsRepository`
- :mod:`trakt`        : :class:`TraktRepository`
- :mod:`feiniu`       : :class:`FeiniuRepository`
- :mod:`inbox`        : :class:`InboxRepository`

:class:`DatabaseManager` 作为 facade，持有 connection + 各 repository 实例，
将所有方法转发到对应 repository，保持完全向后兼容。
"""

# 重新导出 sqlite3 / logger，便于测试 ``patch("app.core.database.sqlite3.connect")``
# 与 ``patch("app.core.database.logger")`` 继续生效
import sqlite3
from typing import Any, Optional

from ..logging import logger as logger
from .connection import (
    FEINIU_MIN_UPDATE_WATERMARK_META_KEY as FEINIU_MIN_UPDATE_WATERMARK_META_KEY,
    INBOX_ERROR_BACKFILL_META_KEY as INBOX_ERROR_BACKFILL_META_KEY,
    DatabaseConnection,
)
from .feiniu import FeiniuRepository
from .inbox import InboxRepository
from .pending_candidates import PendingCandidatesRepository
from .sync_records import SyncRecordsRepository
from .trakt import TraktRepository


class DatabaseManager:
    """数据库管理器（facade）

    持有 :class:`DatabaseConnection` 与各 Repository 实例，
    转发调用以保持与原 ``database.py`` 完全向后兼容。
    """

    def __init__(self, db_path: Optional[str] = None):
        self._connection = DatabaseConnection(db_path)
        # 保持 db_path 作为实例属性（原 DatabaseManager 行为），
        # 便于测试 ``patch("app.core.database.database_manager.db_path", ...)``
        self.db_path = self._connection.db_path
        # 按依赖顺序创建各 repository：
        # - feiniu 无跨仓库依赖
        # - inbox 依赖 feiniu（backfill 需读取 feiniu_meta）
        # - sync 依赖 inbox（update_sync_record_status 需标记通知已读）
        # - trakt 无跨仓库依赖
        self._feiniu = FeiniuRepository(self._connection)
        self._inbox = InboxRepository(self._connection, self._feiniu)
        self._sync = SyncRecordsRepository(self._connection, self._inbox)
        self._trakt = TraktRepository(self._connection)
        self._pending = PendingCandidatesRepository(self._connection)
        # 原 ``_init_database`` 末尾的 backfill 调用移到此处：
        # 需要先创建 inbox_repository（及其 feiniu 依赖）才能执行回填
        self._inbox.backfill_historical_error_notifications()

    # ------------------------------------------------------------------
    # 连接相关属性（保持与原 DatabaseManager 内部结构兼容）
    # ------------------------------------------------------------------

    @property
    def _conn(self):
        """底层 sqlite3 连接（与原 ``self._conn`` 语义一致，可被测试置 None 触发重连）"""
        return self._connection._conn

    @_conn.setter
    def _conn(self, value):
        self._connection._conn = value

    # ------------------------------------------------------------------
    # DatabaseConnection 转发
    # ------------------------------------------------------------------

    def close(self) -> None:
        """关闭数据库连接"""
        self._connection.close()

    def _get_connection(self) -> sqlite3.Connection:
        """获取持久化数据库连接（线程安全），自动重连"""
        return self._connection._get_connection()

    def _execute_with_lock(self, fn):
        """在锁保护下执行数据库操作"""
        return self._connection._execute_with_lock(fn)

    def _ensure_sync_records_media_type(self, cursor) -> None:
        """旧库迁移：为 sync_records 增加 media_type（历史数据为 episode）。"""
        self._connection._ensure_sync_records_media_type(cursor)

    def _ensure_sync_records_bgm_title(self, cursor) -> None:
        """旧库迁移：为 sync_records 增加 bgm_title（Bangumi 平台标题）。"""
        self._connection._ensure_sync_records_bgm_title(cursor)

    def _ensure_trakt_config_sync_filter(self, cursor) -> None:
        """旧库迁移：为 trakt_config 增加 sync_filter_enabled（默认开启）。"""
        self._connection._ensure_trakt_config_sync_filter(cursor)

    def _init_database(self) -> None:
        """初始化数据库"""
        self._connection._init_database()

    # ------------------------------------------------------------------
    # SyncRecordsRepository 转发
    # ------------------------------------------------------------------

    def log_sync_record(
        self,
        user_name: str,
        title: str,
        ori_title: Optional[str],
        season: int,
        episode: int,
        subject_id: Optional[str] = None,
        episode_id: Optional[str] = None,
        status: str = "success",
        message: str = "",
        source: str = "custom",
        media_type: str = "episode",
        bgm_title: str = "",
        match_method: str = "",
        match_score: Optional[float] = None,
        match_platform: str = "",
        match_trace: Optional[dict] = None,
    ) -> Optional[int]:
        """记录同步日志到数据库，返回新记录 id（失败时 None）

        匹配追踪相关字段会一并写入 sync_records 表的 match_* 列，
        并将完整 trace 序列化为 JSON 存入 match_trace 列。
        """
        return self._sync.log_sync_record(
            user_name=user_name,
            title=title,
            ori_title=ori_title,
            season=season,
            episode=episode,
            subject_id=subject_id,
            episode_id=episode_id,
            status=status,
            message=message,
            source=source,
            media_type=media_type,
            bgm_title=bgm_title,
            match_method=match_method,
            match_score=match_score,
            match_platform=match_platform,
            match_trace=match_trace,
        )

    def get_sync_records(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        user_name: Optional[str] = None,
        source: Optional[str] = None,
        source_prefix: Optional[str] = None,
        skip_count: bool = False,
    ) -> dict[str, Any]:
        """获取同步记录"""
        return self._sync.get_sync_records(
            limit=limit,
            offset=offset,
            status=status,
            user_name=user_name,
            source=source,
            source_prefix=source_prefix,
            skip_count=skip_count,
        )

    def get_sync_record_by_id(self, record_id: int) -> Optional[dict[str, Any]]:
        """根据ID获取单个同步记录"""
        return self._sync.get_sync_record_by_id(record_id)

    def get_match_records(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        match_method: Optional[str] = None,
        match_platform: Optional[str] = None,
    ) -> dict[str, Any]:
        """获取匹配记录列表（含匹配追踪字段）"""
        return self._sync.get_match_records(
            limit=limit,
            offset=offset,
            status=status,
            match_method=match_method,
            match_platform=match_platform,
        )

    def update_sync_record_status(
        self, record_id: int, status: str, message: str = ""
    ) -> bool:
        """更新同步记录的状态"""
        return self._sync.update_sync_record_status(record_id, status, message)

    # ------------------------------------------------------------------
    # PendingCandidatesRepository 转发
    # ------------------------------------------------------------------

    def log_pending_candidate(
        self,
        request_title: str,
        request_ori_title: str = "",
        request_season: int = 1,
        request_episode: int = 0,
        user_name: str = "",
        source: str = "",
        candidates: Optional[list] = None,
        trace: Optional[dict] = None,
    ) -> Optional[int]:
        """沉淀一条待确认候选"""
        return self._pending.log_pending_candidate(
            request_title=request_title,
            request_ori_title=request_ori_title,
            request_season=request_season,
            request_episode=request_episode,
            user_name=user_name,
            source=source,
            candidates=candidates,
            trace=trace,
        )

    def get_pending_candidates(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> dict[str, Any]:
        """获取待确认候选列表"""
        return self._pending.get_pending_candidates(
            limit=limit, offset=offset, status=status
        )

    def get_pending_candidate_by_id(
        self, candidate_id: int
    ) -> Optional[dict[str, Any]]:
        """获取单条待确认候选详情"""
        return self._pending.get_pending_candidate_by_id(candidate_id)

    def update_pending_candidate_status(
        self,
        candidate_id: int,
        status: str,
        confirmed_subject_id: str = "",
    ) -> bool:
        """更新待确认候选状态"""
        return self._pending.update_pending_candidate_status(
            candidate_id, status, confirmed_subject_id
        )

    def delete_pending_candidate(self, candidate_id: int) -> bool:
        """删除待确认候选"""
        return self._pending.delete_pending_candidate(candidate_id)

    def resolve_similar_pending_candidates(
        self,
        request_title: str,
        request_season: int,
        user_name: str,
        source: str,
        status: str,
        confirmed_subject_id: str = "",
        exclude_id: Optional[int] = None,
    ) -> int:
        """批量更新同 key 的 pending 候选状态，返回受影响行数"""
        return self._pending.resolve_similar_pending_candidates(
            request_title=request_title,
            request_season=request_season,
            user_name=user_name,
            source=source,
            status=status,
            confirmed_subject_id=confirmed_subject_id,
            exclude_id=exclude_id,
        )

    def get_sync_stats(self) -> dict[str, Any]:
        """获取同步统计信息"""
        return self._sync.get_sync_stats()

    def get_heatmap_stats(self) -> list[dict[str, Any]]:
        """获取热力图数据（过去365天每天同步数），带5分钟缓存"""
        return self._sync.get_heatmap_stats()

    def cleanup_old_records(self, retention_days: int) -> int:
        """清理超过保留天数的同步记录，返回删除行数。"""
        return self._sync.cleanup_old_records(retention_days)

    # ------------------------------------------------------------------
    # TraktRepository 转发
    # ------------------------------------------------------------------

    def save_trakt_config(self, config: dict) -> bool:
        """保存或更新 Trakt 配置"""
        return self._trakt.save_trakt_config(config)

    def get_trakt_config(self, user_id: str) -> Optional[dict]:
        """获取用户的 Trakt 配置"""
        return self._trakt.get_trakt_config(user_id)

    def delete_trakt_config(self, user_id: str) -> bool:
        """删除用户的 Trakt 配置"""
        return self._trakt.delete_trakt_config(user_id)

    def save_trakt_sync_history(self, history: dict) -> bool:
        """保存 Trakt 同步历史记录"""
        return self._trakt.save_trakt_sync_history(history)

    def get_trakt_sync_history(
        self, user_id: str, limit: int = 100, offset: int = 0
    ) -> dict:
        """获取用户的 Trakt 同步历史"""
        return self._trakt.get_trakt_sync_history(user_id, limit, offset)

    def get_last_sync_time(self, user_id: str) -> Optional[int]:
        """获取用户最后同步时间"""
        return self._trakt.get_last_sync_time(user_id)

    def get_trakt_configs_with_sync_enabled(self) -> list[dict]:
        """获取所有启用同步的 Trakt 配置"""
        return self._trakt.get_trakt_configs_with_sync_enabled()

    def get_trakt_synced_set(self, user_id: str) -> set[tuple[str, int]]:
        """批量获取已同步的 Trakt 条目集合，用于 O(1) 去重查找"""
        return self._trakt.get_trakt_synced_set(user_id)

    # ------------------------------------------------------------------
    # FeiniuRepository 转发
    # ------------------------------------------------------------------

    def save_feiniu_sync_history(
        self,
        fn_user_guid: str,
        item_guid: str,
        update_time_snapshot: Optional[int] = None,
    ) -> bool:
        """记录已提交的飞牛条目同步（去重用）"""
        return self._feiniu.save_feiniu_sync_history(
            fn_user_guid, item_guid, update_time_snapshot
        )

    def get_feiniu_synced_set(self, user_guids: list[str]) -> set[tuple[str, str]]:
        """批量获取已同步的飞牛条目集合，用于 O(1) 去重查找"""
        return self._feiniu.get_feiniu_synced_set(user_guids)

    def get_feiniu_meta(self, key: str) -> Optional[str]:
        return self._feiniu.get_feiniu_meta(key)

    def set_feiniu_meta(self, key: str, value: str) -> bool:
        return self._feiniu.set_feiniu_meta(key, value)

    def delete_feiniu_meta(self, key: str) -> bool:
        return self._feiniu.delete_feiniu_meta(key)

    def clear_feiniu_min_update_watermark(self) -> None:
        """清除「启用后仅同步新进度」水位（飞牛关闭时调用）"""
        self._feiniu.clear_feiniu_min_update_watermark()

    def set_feiniu_min_update_watermark_now(self) -> int:
        """将同步起点设为当前时刻（Web 勾选启用并保存时调用，不追溯历史）"""
        return self._feiniu.set_feiniu_min_update_watermark_now()

    def get_or_create_feiniu_min_update_watermark_ms(self) -> int:
        """返回飞牛库 update_time 下限（毫秒）。首次调用时写入当前时刻，不追溯历史。"""
        return self._feiniu.get_or_create_feiniu_min_update_watermark_ms()

    # ------------------------------------------------------------------
    # InboxRepository 转发
    # ------------------------------------------------------------------

    def list_in_app_notifications(
        self,
        limit: int = 50,
        unread_only: bool = False,
    ) -> list[dict[str, Any]]:
        """获取本地收件箱通知列表（新在前）"""
        return self._inbox.list_in_app_notifications(limit, unread_only)

    def get_in_app_notification_by_id(
        self, notification_id: int
    ) -> Optional[dict[str, Any]]:
        return self._inbox.get_in_app_notification_by_id(notification_id)

    def mark_notification_read(self, notification_id: int) -> bool:
        return self._inbox.mark_notification_read(notification_id)

    def mark_notifications_read_by_ref_id(self, ref_id: int) -> int:
        """同步记录恢复成功时，将关联通知标为已读。"""
        return self._inbox.mark_notifications_read_by_ref_id(ref_id)

    def mark_notification_group_read(self, notification_id: int) -> int:
        """将同一番剧聚合组内的未读通知全部标为已读。"""
        return self._inbox.mark_notification_group_read(notification_id)

    def mark_all_notifications_read(self) -> int:
        return self._inbox.mark_all_notifications_read()

    def count_unread_notifications(self) -> int:
        return self._inbox.count_unread_notifications()

    def get_read_announcement_ids(self) -> set[str]:
        return self._inbox.get_read_announcement_ids()

    def mark_announcement_read(self, announcement_id: str) -> bool:
        return self._inbox.mark_announcement_read(announcement_id)

    def mark_all_announcements_read(self, announcement_ids: list[str]) -> int:
        return self._inbox.mark_all_announcements_read(announcement_ids)

    def backfill_historical_error_notifications(self) -> int:
        """将历史 error 同步记录回填为已读通知（仅执行一次）。"""
        return self._inbox.backfill_historical_error_notifications()


# 全局数据库实例（懒加载：首次访问 database_manager 时才创建实例并打开数据库）
_database_manager: Optional[DatabaseManager] = None


def __getattr__(name: str):
    """模块级懒加载，避免 import 时即打开 SQLite 连接。"""
    global _database_manager
    if name == "database_manager":
        if _database_manager is None:
            _database_manager = DatabaseManager()
        return _database_manager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
