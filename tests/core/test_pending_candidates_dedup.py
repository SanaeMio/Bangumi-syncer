"""pending_candidates 去重与批量确认测试

验证：
1. 同 key (title, season, user, source) 重复沉淀时 upsert 而非插入新行
2. 不同 key 各自独立插入
3. confirm_pending_candidate 后同 key 的其它 pending 行也被标记 confirmed
4. resolve_similar_pending_candidates 的 exclude_id 排除自身
"""

from pathlib import Path

from app.core.database import DatabaseManager


def _make_db(tmp_path: Path) -> DatabaseManager:
    """创建指向临时路径的 DatabaseManager 实例"""
    db_path = str(tmp_path / "test_pending.db")
    return DatabaseManager(db_path)


def _make_candidates():
    return [
        {"subject_id": "111", "name": "番剧A", "name_cn": "番剧A", "score": 0.9},
        {"subject_id": "222", "name": "番剧B", "name_cn": "番剧B", "score": 0.7},
    ]


class TestPendingCandidatesDedup:
    """log_pending_candidate 去重测试"""

    def test_inserts_new_on_first_call(self, tmp_path):
        """首次沉淀插入新行"""
        dbm = _make_db(tmp_path)
        try:
            row_id = dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                trace={"steps": []},
            )
            assert row_id is not None and row_id > 0

            result = dbm.get_pending_candidates(status="pending")
            assert result["total"] == 1
        finally:
            dbm._connection._conn.close()

    def test_upserts_on_duplicate_key(self, tmp_path):
        """同 key 重复沉淀时更新而非插入新行"""
        dbm = _make_db(tmp_path)
        try:
            # 第一次沉淀
            id1 = dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                trace={"steps": [{"stage": "api_search"}]},
            )
            # 第二次沉淀（同 key）
            id2 = dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=[
                    {
                        "subject_id": "333",
                        "name": "新候选",
                        "name_cn": "新候选",
                        "score": 0.95,
                    }
                ],
                trace={"steps": [{"stage": "custom_mapping"}]},
            )
            # 应返回相同 id（upsert 更新而非插入）
            assert id1 == id2

            # 仍只有 1 行
            result = dbm.get_pending_candidates(status="pending")
            assert result["total"] == 1

            # 候选已更新为新值
            record = dbm.get_pending_candidate_by_id(id1)
            assert "333" in record["candidates_json"]
            assert "custom_mapping" in record["trace_json"]
        finally:
            dbm._connection._conn.close()

    def test_inserts_different_keys_independently(self, tmp_path):
        """不同 key 各自独立插入"""
        dbm = _make_db(tmp_path)
        try:
            dbm.log_pending_candidate(
                request_title="番剧A",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
            )
            dbm.log_pending_candidate(
                request_title="番剧A",
                request_season=2,  # 不同 season → 不同 key
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
            )
            dbm.log_pending_candidate(
                request_title="番剧B",
                request_season=1,
                user_name="user2",  # 不同 user → 不同 key
                source="emby",
                candidates=_make_candidates(),
            )
            result = dbm.get_pending_candidates(status="pending")
            assert result["total"] == 3
        finally:
            dbm._connection._conn.close()

    def test_upsert_does_not_touch_resolved_rows(self, tmp_path):
        """已确认/拒绝的行不影响 upsert（部分唯一索引仅覆盖 pending）"""
        dbm = _make_db(tmp_path)
        try:
            # 沉淀并确认
            row_id = dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
            )
            dbm.update_pending_candidate_status(
                row_id, "confirmed", confirmed_subject_id="111"
            )

            # 同 key 再次沉淀（应插入新行，因为旧行已非 pending）
            new_id = dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
            )
            assert new_id != row_id

            # pending 行只有 1 行（新插入的）
            result = dbm.get_pending_candidates(status="pending")
            assert result["total"] == 1
        finally:
            dbm._connection._conn.close()


class TestResolveSimilarPendingCandidates:
    """resolve_similar_pending_candidates 批量更新测试"""

    def test_confirms_all_similar_pending(self, tmp_path):
        """确认后同 key 的其它 pending 行也被标记 confirmed"""
        dbm = _make_db(tmp_path)
        try:
            # 先沉淀 1 行（去重后只会 1 行 pending）
            dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
            )
            # 模拟去重前的历史数据：临时删除部分唯一索引后直接插入额外 pending 行
            conn = dbm._connection._conn
            conn.execute("DROP INDEX IF EXISTS idx_pending_candidates_dedup")
            conn.execute(
                "INSERT INTO pending_candidates (request_title, request_season, user_name, source, status, candidates_json, trace_json) "
                "VALUES ('测试番剧', 1, 'user1', 'plex', 'pending', '[]', '{}')"
            )
            conn.execute(
                "INSERT INTO pending_candidates (request_title, request_season, user_name, source, status, candidates_json, trace_json) "
                "VALUES ('测试番剧', 1, 'user1', 'plex', 'pending', '[]', '{}')"
            )
            conn.commit()

            # 确认有 3 条 pending
            assert dbm.get_pending_candidates(status="pending")["total"] == 3

            # 取第一条 id 进行 confirm
            pending_list = dbm.get_pending_candidates(status="pending")
            first_id = pending_list["records"][0]["id"]

            # 批量更新同 key 的其它 pending 行
            affected = dbm.resolve_similar_pending_candidates(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                status="confirmed",
                confirmed_subject_id="111",
                exclude_id=first_id,
            )
            assert affected == 2  # 排除自身后更新了 2 行

            # 剩余 pending 应为 0（自身稍后由 update_pending_candidate_status 处理）
            remaining = dbm.get_pending_candidates(status="pending")["total"]
            assert remaining == 1  # 只剩 first_id 还是 pending
        finally:
            dbm._connection._conn.close()

    def test_exclude_id_zero_means_all(self, tmp_path):
        """exclude_id=None 时更新所有匹配的 pending 行"""
        dbm = _make_db(tmp_path)
        try:
            conn = dbm._connection._conn
            conn.execute("DROP INDEX IF EXISTS idx_pending_candidates_dedup")
            for _ in range(3):
                conn.execute(
                    "INSERT INTO pending_candidates (request_title, request_season, user_name, source, status, candidates_json, trace_json) "
                    "VALUES ('番剧X', 1, 'u', 'plex', 'pending', '[]', '{}')"
                )
            conn.commit()

            affected = dbm.resolve_similar_pending_candidates(
                request_title="番剧X",
                request_season=1,
                user_name="u",
                source="plex",
                status="rejected",
            )
            assert affected == 3
            assert dbm.get_pending_candidates(status="pending")["total"] == 0
        finally:
            dbm._connection._conn.close()
