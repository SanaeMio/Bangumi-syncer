"""pending_sync_queue 待同步队列测试

验证：
1. enqueue 入队与去重（同 key upsert 而非插入新行）
2. fetch_pending 按 created_at 升序拉取
3. mark_synced / increment_attempts / mark_abandoned 状态流转
4. get_queue 分页与状态过滤
5. 不同 key 独立插入
6. 已 synced 的行不再被 upsert 命中（部分唯一索引仅覆盖 pending）
"""

from pathlib import Path

from app.core.database import DatabaseManager


def _make_db(tmp_path: Path) -> DatabaseManager:
    """创建指向临时路径的 DatabaseManager 实例"""
    db_path = str(tmp_path / "test_pending_sync.db")
    return DatabaseManager(db_path)


def _make_payload(title: str = "测试番剧", episode: int = 1):
    return {
        "title": title,
        "season": 1,
        "episode": episode,
        "media_type": "episode",
        "release_date": "2024-01-01",
        "user_name": "user1",
        "source": "plex",
    }


class TestPendingSyncQueueEnqueue:
    """enqueue 入队与去重测试"""

    def test_inserts_new_on_first_call(self, tmp_path):
        """首次入队插入新行"""
        dbm = _make_db(tmp_path)
        try:
            row_id = dbm.enqueue_pending_sync(
                user_name="user1",
                title="测试番剧",
                season=1,
                episode=1,
                subject_id="123",
                episode_id="456",
                source="plex",
                media_type="episode",
                payload=_make_payload(),
                reason="api_unreachable",
            )
            assert row_id is not None and row_id > 0

            stats = dbm.get_pending_sync_stats()
            assert stats["pending"] == 1
        finally:
            dbm._connection._conn.close()

    def test_upserts_on_duplicate_key(self, tmp_path):
        """同 key (user+subject+episode+source) 重复入队时更新而非插入新行"""
        dbm = _make_db(tmp_path)
        try:
            id1 = dbm.enqueue_pending_sync(
                user_name="user1",
                title="测试番剧",
                season=1,
                episode=1,
                subject_id="123",
                episode_id="456",
                source="plex",
                media_type="episode",
                payload=_make_payload(),
                reason="api_unreachable",
                last_error="connect_error",
            )
            # 同 key 再次入队
            id2 = dbm.enqueue_pending_sync(
                user_name="user1",
                title="测试番剧-新",
                season=1,
                episode=1,
                subject_id="123",
                episode_id="456",
                source="plex",
                media_type="episode",
                payload=_make_payload(title="测试番剧-新"),
                reason="http_503",
                last_error="service unavailable",
            )
            # 应返回相同 id
            assert id1 == id2

            stats = dbm.get_pending_sync_stats()
            assert stats["pending"] == 1

            # 内容应已更新
            record = dbm.get_pending_sync_record_by_id(id1)
            assert "测试番剧-新" in record["payload_json"]
            assert record["last_error"] == "service unavailable"
            # attempts 被重置为 0
            assert record["attempts"] == 0
        finally:
            dbm._connection._conn.close()

    def test_inserts_different_keys_independently(self, tmp_path):
        """不同 key 各自独立插入"""
        dbm = _make_db(tmp_path)
        try:
            dbm.enqueue_pending_sync(
                user_name="user1",
                title="番剧A",
                season=1,
                episode=1,
                subject_id="111",
                episode_id="e1",
                source="plex",
                media_type="episode",
                payload=_make_payload("番剧A"),
            )
            # 不同 subject_id
            dbm.enqueue_pending_sync(
                user_name="user1",
                title="番剧B",
                season=1,
                episode=1,
                subject_id="222",
                episode_id="e2",
                source="plex",
                media_type="episode",
                payload=_make_payload("番剧B"),
            )
            # 不同 user
            dbm.enqueue_pending_sync(
                user_name="user2",
                title="番剧A",
                season=1,
                episode=1,
                subject_id="111",
                episode_id="e1",
                source="plex",
                media_type="episode",
                payload=_make_payload("番剧A"),
            )
            stats = dbm.get_pending_sync_stats()
            assert stats["pending"] == 3
        finally:
            dbm._connection._conn.close()

    def test_upsert_does_not_touch_synced_rows(self, tmp_path):
        """已 synced 的行不影响 upsert（部分唯一索引仅覆盖 pending）"""
        dbm = _make_db(tmp_path)
        try:
            row_id = dbm.enqueue_pending_sync(
                user_name="user1",
                title="测试番剧",
                season=1,
                episode=1,
                subject_id="123",
                episode_id="456",
                source="plex",
                media_type="episode",
                payload=_make_payload(),
            )
            dbm.mark_pending_sync_synced(row_id)

            # 同 key 再次入队（应插入新行，因为旧行已 synced）
            new_id = dbm.enqueue_pending_sync(
                user_name="user1",
                title="测试番剧",
                season=1,
                episode=1,
                subject_id="123",
                episode_id="456",
                source="plex",
                media_type="episode",
                payload=_make_payload(),
            )
            assert new_id != row_id
            assert dbm.count_pending_sync() == 1
        finally:
            dbm._connection._conn.close()


class TestPendingSyncQueueFetch:
    """fetch_pending 拉取测试"""

    def test_fetch_returns_pending_only(self, tmp_path):
        """只返回 pending 状态"""
        dbm = _make_db(tmp_path)
        try:
            id1 = dbm.enqueue_pending_sync(
                user_name="user1",
                title="番剧A",
                season=1,
                episode=1,
                subject_id="111",
                episode_id="e1",
                source="plex",
                media_type="episode",
                payload=_make_payload("番剧A"),
            )
            dbm.enqueue_pending_sync(
                user_name="user1",
                title="番剧B",
                season=1,
                episode=1,
                subject_id="222",
                episode_id="e2",
                source="plex",
                media_type="episode",
                payload=_make_payload("番剧B"),
            )
            # 把第一条标记为 synced
            dbm.mark_pending_sync_synced(id1)

            records = dbm.fetch_pending_sync(limit=10)
            assert len(records) == 1
            assert records[0]["subject_id"] == "222"
        finally:
            dbm._connection._conn.close()

    def test_fetch_filters_by_max_attempts(self, tmp_path):
        """max_attempts 过滤超过重试次数的任务"""
        dbm = _make_db(tmp_path)
        try:
            id1 = dbm.enqueue_pending_sync(
                user_name="user1",
                title="番剧A",
                season=1,
                episode=1,
                subject_id="111",
                episode_id="e1",
                source="plex",
                media_type="episode",
                payload=_make_payload("番剧A"),
            )
            dbm.enqueue_pending_sync(
                user_name="user1",
                title="番剧B",
                season=1,
                episode=1,
                subject_id="222",
                episode_id="e2",
                source="plex",
                media_type="episode",
                payload=_make_payload("番剧B"),
            )
            # 第一条累加 3 次 attempts
            for _ in range(3):
                dbm.increment_pending_sync_attempts(id1, "test error")

            # max_attempts=3 应只返回 attempts<3 的（即第二条）
            records = dbm.fetch_pending_sync(limit=10, max_attempts=3)
            assert len(records) == 1
            assert records[0]["subject_id"] == "222"
        finally:
            dbm._connection._conn.close()


class TestPendingSyncQueueStateTransitions:
    """状态流转测试"""

    def test_mark_synced(self, tmp_path):
        dbm = _make_db(tmp_path)
        try:
            row_id = dbm.enqueue_pending_sync(
                user_name="user1",
                title="测试番剧",
                season=1,
                episode=1,
                subject_id="123",
                episode_id="456",
                source="plex",
                media_type="episode",
                payload=_make_payload(),
            )
            assert dbm.mark_pending_sync_synced(row_id) is True

            stats = dbm.get_pending_sync_stats()
            assert stats["pending"] == 0
            assert stats["synced"] == 1
        finally:
            dbm._connection._conn.close()

    def test_increment_attempts(self, tmp_path):
        dbm = _make_db(tmp_path)
        try:
            row_id = dbm.enqueue_pending_sync(
                user_name="user1",
                title="测试番剧",
                season=1,
                episode=1,
                subject_id="123",
                episode_id="456",
                source="plex",
                media_type="episode",
                payload=_make_payload(),
            )
            assert dbm.increment_pending_sync_attempts(row_id, "fail 1") is True
            assert dbm.increment_pending_sync_attempts(row_id, "fail 2") is True

            record = dbm.get_pending_sync_record_by_id(row_id)
            assert record["attempts"] == 2
            assert record["last_error"] == "fail 2"
        finally:
            dbm._connection._conn.close()

    def test_mark_abandoned(self, tmp_path):
        dbm = _make_db(tmp_path)
        try:
            row_id = dbm.enqueue_pending_sync(
                user_name="user1",
                title="测试番剧",
                season=1,
                episode=1,
                subject_id="123",
                episode_id="456",
                source="plex",
                media_type="episode",
                payload=_make_payload(),
            )
            assert dbm.mark_pending_sync_abandoned(row_id, "exceeded max") is True

            stats = dbm.get_pending_sync_stats()
            assert stats["pending"] == 0
            assert stats["abandoned"] == 1
        finally:
            dbm._connection._conn.close()

    def test_delete_record(self, tmp_path):
        dbm = _make_db(tmp_path)
        try:
            row_id = dbm.enqueue_pending_sync(
                user_name="user1",
                title="测试番剧",
                season=1,
                episode=1,
                subject_id="123",
                episode_id="456",
                source="plex",
                media_type="episode",
                payload=_make_payload(),
            )
            assert dbm.delete_pending_sync_record(row_id) is True
            assert dbm.get_pending_sync_record_by_id(row_id) is None
            assert dbm.count_pending_sync() == 0
        finally:
            dbm._connection._conn.close()


class TestPendingSyncQueueList:
    """get_queue 分页与过滤测试"""

    def test_pagination(self, tmp_path):
        dbm = _make_db(tmp_path)
        try:
            for i in range(5):
                dbm.enqueue_pending_sync(
                    user_name="user1",
                    title=f"番剧{i}",
                    season=1,
                    episode=i + 1,
                    subject_id=f"100{i}",
                    episode_id=f"e{i}",
                    source="plex",
                    media_type="episode",
                    payload=_make_payload(f"番剧{i}"),
                )
            # 把第一条标记为 synced
            result = dbm.get_pending_sync_queue(limit=2, offset=0, status=None)
            first_id = result["records"][0]["id"]
            dbm.mark_pending_sync_synced(first_id)

            # 第一页
            page1 = dbm.get_pending_sync_queue(limit=2, offset=0)
            assert page1["total"] == 5
            assert len(page1["records"]) == 2

            # 第二页
            page2 = dbm.get_pending_sync_queue(limit=2, offset=2)
            assert len(page2["records"]) == 2

            # 按 status 过滤
            synced_only = dbm.get_pending_sync_queue(
                limit=10, offset=0, status="synced"
            )
            assert synced_only["total"] == 1
            assert synced_only["records"][0]["status"] == "synced"

            pending_only = dbm.get_pending_sync_queue(
                limit=10, offset=0, status="pending"
            )
            assert pending_only["total"] == 4
        finally:
            dbm._connection._conn.close()
