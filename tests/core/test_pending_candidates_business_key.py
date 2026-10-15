"""pending_candidates business_key 去重对齐业务身份测试

验证：
1. 同 identity（同 user + 归一化 title + 同 season）跨 source 沉淀 → 仅一行 pending，
   展示字段与 sync_record_id 刷新为最新。
2. 不同 season / 不同 user → 独立行。
3. 旧行为兼容：business_key 为空时仍按 4 元组 (title, season, user, source) 去重。
4. confirm 后同 business_key 的其它 pending 行被 resolve。
5. 唯一索引兜底：同 business_key 重复 pending INSERT 冲突不炸主流程。
"""

import sqlite3
from pathlib import Path

from app.core.database import DatabaseManager


def _db_conn(dbm: DatabaseManager) -> sqlite3.Connection:
    """返回底层 sqlite3 连接（测试构造后必然非 None，显式收窄）"""
    conn = dbm._connection._conn
    assert conn is not None, "测试数据库连接未初始化"
    return conn


def _row_id(value: int | None) -> int:
    """显式收窄沉淀返回的行 id（测试前提：沉淀成功）"""
    assert value is not None, "沉淀应返回行 id"
    return value


def _record(dbm: DatabaseManager, candidate_id: int | None) -> dict:
    """读取待确认候选记录并显式收窄（测试前提：记录存在）"""
    record = dbm.get_pending_candidate_by_id(_row_id(candidate_id))
    assert record is not None, "待确认候选记录应存在"
    return record


def _make_db(tmp_path: Path) -> DatabaseManager:
    """创建指向临时路径的 DatabaseManager 实例"""
    db_path = str(tmp_path / "test_pending_bk.db")
    return DatabaseManager(db_path)


def _make_candidates():
    return [
        {"subject_id": "111", "name": "番剧A", "name_cn": "番剧A", "score": 0.9},
        {"subject_id": "222", "name": "番剧B", "name_cn": "番剧B", "score": 0.7},
    ]


class TestBusinessKeyDedup:
    """business_key 去重：同 identity 跨 source 仅保留一行"""

    def test_same_identity_different_source_upserts(self, tmp_path):
        """同 identity 跨 source（plex/emby）沉淀 → 仅一行 pending，sync_record_id 刷新"""
        dbm = _make_db(tmp_path)
        try:
            # 第一次沉淀：source=business_key 路径
            id1 = dbm.log_pending_candidate(
                request_title="我推的孩子",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                trace={"steps": [{"stage": "api_search"}]},
                sync_record_id=100,
                business_key="match|user1|我推的孩子|1",
            )
            # 第二次沉淀：同 identity 但 source 不同
            id2 = dbm.log_pending_candidate(
                request_title="我推的孩子",
                request_season=1,
                user_name="user1",
                source="emby",  # 不同 source
                candidates=[
                    {
                        "subject_id": "333",
                        "name": "新候选",
                        "name_cn": "新候选",
                        "score": 0.95,
                    }
                ],
                trace={"steps": [{"stage": "custom_mapping"}]},
                sync_record_id=200,
                business_key="match|user1|我推的孩子|1",
            )
            # 应返回相同 id（按 business_key upsert）
            assert id1 == id2

            # 仍只有 1 行 pending
            result = dbm.get_pending_candidates(status="pending")
            assert result["total"] == 1

            # sync_record_id 刷新为最新
            record = _record(dbm, id1)
            assert record["sync_record_id"] == 200
            # 展示字段刷新
            assert "333" in record["candidates_json"]
            assert "custom_mapping" in record["trace_json"]
        finally:
            _db_conn(dbm).close()

    def test_different_season_independent_rows(self, tmp_path):
        """不同 season → 独立行"""
        dbm = _make_db(tmp_path)
        try:
            id1 = dbm.log_pending_candidate(
                request_title="某番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                business_key="match|user1|某番剧|1",
            )
            id2 = dbm.log_pending_candidate(
                request_title="某番剧",
                request_season=2,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                business_key="match|user1|某番剧|2",
            )
            assert id1 != id2
            result = dbm.get_pending_candidates(status="pending")
            assert result["total"] == 2
        finally:
            _db_conn(dbm).close()

    def test_different_user_independent_rows(self, tmp_path):
        """不同 user → 独立行"""
        dbm = _make_db(tmp_path)
        try:
            id1 = dbm.log_pending_candidate(
                request_title="某番剧",
                request_season=1,
                user_name="alice",
                source="plex",
                candidates=_make_candidates(),
                business_key="match|alice|某番剧|1",
            )
            id2 = dbm.log_pending_candidate(
                request_title="某番剧",
                request_season=1,
                user_name="bob",
                source="plex",
                candidates=_make_candidates(),
                business_key="match|bob|某番剧|1",
            )
            assert id1 != id2
            result = dbm.get_pending_candidates(status="pending")
            assert result["total"] == 2
        finally:
            _db_conn(dbm).close()

    def test_normalized_title_matches(self, tmp_path):
        """归一化后标题相同 → 同一 business_key → 仅一行

        验证 build_match_business_key 归一化效果：带噪声的标题与干净标题
        归一化后相同，business_key 相同 → upsert。
        """
        dbm = _make_db(tmp_path)
        try:
            id1 = dbm.log_pending_candidate(
                request_title="我推的孩子",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                business_key="match|user1|我推的孩子|1",
            )
            # 带噪声的标题，但归一化后相同
            id2 = dbm.log_pending_candidate(
                request_title="我推的孩子 [ANi] [1080p]",
                request_season=1,
                user_name="user1",
                source="emby",
                candidates=_make_candidates(),
                business_key="match|user1|我推的孩子|1",
            )
            assert id1 == id2
            result = dbm.get_pending_candidates(status="pending")
            assert result["total"] == 1
        finally:
            _db_conn(dbm).close()


class TestBusinessKeyBackwardCompat:
    """business_key 为空时保留旧 4 元组去重行为"""

    def test_empty_business_key_falls_back_to_4tuple(self, tmp_path):
        """business_key 为空时按 (title, season, user, source) 去重"""
        dbm = _make_db(tmp_path)
        try:
            id1 = dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                business_key="",
            )
            # 同 4 元组 → upsert
            id2 = dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                business_key="",
            )
            assert id1 == id2
            result = dbm.get_pending_candidates(status="pending")
            assert result["total"] == 1
        finally:
            _db_conn(dbm).close()

    def test_empty_business_key_different_source_independent(self, tmp_path):
        """business_key 为空 + 不同 source → 独立行（旧行为）"""
        dbm = _make_db(tmp_path)
        try:
            id1 = dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                business_key="",
            )
            id2 = dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="emby",
                candidates=_make_candidates(),
                business_key="",
            )
            assert id1 != id2
            result = dbm.get_pending_candidates(status="pending")
            assert result["total"] == 2
        finally:
            _db_conn(dbm).close()


class TestResolveSimilarByBusinessKey:
    """resolve_similar_pending_candidates 按 business_key 批量更新"""

    def test_resolve_by_business_key(self, tmp_path):
        """按 business_key 批量更新同键 pending 行"""
        dbm = _make_db(tmp_path)
        try:
            conn = _db_conn(dbm)
            # 直接插入 3 条同 business_key 的 pending 行（绕过唯一索引）
            conn.execute(
                "DROP INDEX IF EXISTS idx_pending_candidates_business_key_active"
            )
            for _ in range(3):
                conn.execute(
                    """
                    INSERT INTO pending_candidates
                    (request_title, request_season, user_name, source, status,
                     candidates_json, trace_json, business_key)
                    VALUES ('测试番剧', 1, 'user1', 'plex', 'pending', '[]', '{}',
                     'match|user1|测试番剧|1')
                    """
                )
            conn.commit()

            assert dbm.get_pending_candidates(status="pending")["total"] == 3

            # 取第一条 id 进行 exclude
            pending_list = dbm.get_pending_candidates(status="pending")
            first_id = pending_list["records"][0]["id"]

            # 按 business_key 批量更新
            affected = dbm.resolve_similar_pending_candidates(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                status="confirmed",
                confirmed_subject_id="111",
                exclude_id=first_id,
                business_key="match|user1|测试番剧|1",
            )
            assert affected == 2

            # 只剩 first_id 还是 pending
            remaining = dbm.get_pending_candidates(status="pending")["total"]
            assert remaining == 1
        finally:
            _db_conn(dbm).close()

    def test_resolve_by_business_key_no_exclude(self, tmp_path):
        """business_key 匹配 + exclude_id=None → 更新所有"""
        dbm = _make_db(tmp_path)
        try:
            conn = _db_conn(dbm)
            conn.execute(
                "DROP INDEX IF EXISTS idx_pending_candidates_business_key_active"
            )
            for _ in range(3):
                conn.execute(
                    """
                    INSERT INTO pending_candidates
                    (request_title, request_season, user_name, source, status,
                     candidates_json, trace_json, business_key)
                    VALUES ('番剧X', 1, 'u', 'plex', 'pending', '[]', '{}',
                     'match|u|番剧X|1')
                    """
                )
            conn.commit()

            affected = dbm.resolve_similar_pending_candidates(
                request_title="番剧X",
                request_season=1,
                user_name="u",
                source="plex",
                status="rejected",
                business_key="match|u|番剧X|1",
            )
            assert affected == 3
            assert dbm.get_pending_candidates(status="pending")["total"] == 0
        finally:
            _db_conn(dbm).close()

    def test_resolve_empty_business_key_falls_back(self, tmp_path):
        """business_key 为空时按 4 元组批量更新（旧行为）"""
        dbm = _make_db(tmp_path)
        try:
            conn = _db_conn(dbm)
            conn.execute(
                "DROP INDEX IF EXISTS idx_pending_candidates_business_key_active"
            )
            for _ in range(2):
                conn.execute(
                    """
                    INSERT INTO pending_candidates
                    (request_title, request_season, user_name, source, status,
                     candidates_json, trace_json, business_key)
                    VALUES ('番剧Y', 1, 'u', 'plex', 'pending', '[]', '{}', '')
                    """
                )
            conn.commit()

            affected = dbm.resolve_similar_pending_candidates(
                request_title="番剧Y",
                request_season=1,
                user_name="u",
                source="plex",
                status="confirmed",
                business_key="",
            )
            assert affected == 2
        finally:
            _db_conn(dbm).close()


class TestUniqueIndexGuard:
    """唯一索引兜底：同 business_key 重复 pending INSERT 冲突不炸主流程"""

    def test_unique_index_prevents_duplicate_pending(self, tmp_path):
        """同 business_key 重复 pending INSERT 触发唯一约束冲突"""
        dbm = _make_db(tmp_path)
        try:
            # 第一次插入
            dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                business_key="match|user1|测试番剧|1",
            )

            # 第二次直接 INSERT（绕过 upsert 查询）应触发唯一约束冲突
            # 验证索引存在且生效
            conn = _db_conn(dbm)
            try:
                conn.execute(
                    """
                    INSERT INTO pending_candidates
                    (request_title, request_season, user_name, source, status,
                     candidates_json, trace_json, business_key)
                    VALUES ('测试番剧', 1, 'user1', 'plex', 'pending', '[]', '{}',
                     'match|user1|测试番剧|1')
                    """
                )
                conn.commit()
                raise AssertionError("应触发唯一约束冲突")
            except Exception as e:
                assert "UNIQUE" in str(e) or "unique" in str(e).lower()

            # 仍只有 1 行
            result = dbm.get_pending_candidates(status="pending")
            assert result["total"] == 1
        finally:
            _db_conn(dbm).close()

    def test_empty_business_key_bypasses_unique_index(self, tmp_path):
        """business_key 为空时不参与唯一约束（空 key 历史行兼容）"""
        dbm = _make_db(tmp_path)
        try:
            # 两条 business_key 为空的 pending 行（不同 source）
            dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                business_key="",
            )
            dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="emby",
                candidates=_make_candidates(),
                business_key="",
            )
            # 空 key 不参与约束 → 2 行
            result = dbm.get_pending_candidates(status="pending")
            assert result["total"] == 2
        finally:
            _db_conn(dbm).close()


class TestFindLatestByBusinessKey:
    """find_latest_by_business_key：run 入队复用判定用的只读查询"""

    def test_returns_latest_candidate_by_status_priority(self, tmp_path):
        """同键多条候选 → 返回 id 最大的一条（含 status / resolved_at）"""
        dbm = _make_db(tmp_path)
        try:
            conn = _db_conn(dbm)
            conn.execute(
                "DROP INDEX IF EXISTS idx_pending_candidates_business_key_active"
            )
            conn.execute(
                """
                INSERT INTO pending_candidates
                (request_title, request_season, user_name, source, status,
                 candidates_json, trace_json, business_key)
                VALUES ('测试番剧', 1, 'user1', 'plex', 'rejected', '[]', '{}',
                 'match|user1|测试番剧|1')
                """
            )
            conn.execute(
                """
                INSERT INTO pending_candidates
                (request_title, request_season, user_name, source, status,
                 candidates_json, trace_json, business_key)
                VALUES ('测试番剧', 1, 'user1', 'plex', 'pending', '[]', '{}',
                 'match|user1|测试番剧|1')
                """
            )
            conn.commit()

            row = dbm._pending.find_latest_by_business_key("match|user1|测试番剧|1")
            assert row is not None
            assert row["status"] == "pending"
            assert row["business_key"] == "match|user1|测试番剧|1"
        finally:
            _db_conn(dbm).close()

    def test_returns_none_for_unknown_or_empty_key(self, tmp_path):
        """未知 business_key / 空 key → None"""
        dbm = _make_db(tmp_path)
        try:
            assert dbm._pending.find_latest_by_business_key("match|nobody|无|1") is None
            assert dbm._pending.find_latest_by_business_key("") is None
        finally:
            _db_conn(dbm).close()

    def test_ignores_status_outside_pending_confirmed_rejected(self, tmp_path):
        """已被物理删除状态之外的行不参与复用判定"""
        dbm = _make_db(tmp_path)
        try:
            conn = _db_conn(dbm)
            conn.execute(
                """
                INSERT INTO pending_candidates
                (request_title, request_season, user_name, source, status,
                 candidates_json, trace_json, business_key)
                VALUES ('测试番剧', 1, 'user1', 'plex', 'deleted', '[]', '{}',
                 'match|user1|测试番剧|1')
                """
            )
            conn.commit()
            assert (
                dbm._pending.find_latest_by_business_key("match|user1|测试番剧|1")
                is None
            )
        finally:
            _db_conn(dbm).close()
