"""pending_candidates 去重与批量确认测试

验证：
1. 同 key (title, season, user, source) 重复沉淀时 upsert 而非插入新行
2. 不同 key 各自独立插入
3. confirm_pending_candidate 后同 key 的其它 pending 行也被标记 confirmed
4. resolve_similar_pending_candidates 的 exclude_id 排除自身
"""

import json
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
            _db_conn(dbm).close()

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
            record = _record(dbm, id1)
            assert "333" in record["candidates_json"]
            assert "custom_mapping" in record["trace_json"]
        finally:
            _db_conn(dbm).close()

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
            _db_conn(dbm).close()

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
                _row_id(row_id), "confirmed", confirmed_subject_id="111"
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
            _db_conn(dbm).close()


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
            conn = _db_conn(dbm)
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
            _db_conn(dbm).close()

    def test_exclude_id_zero_means_all(self, tmp_path):
        """exclude_id=None 时更新所有匹配的 pending 行"""
        dbm = _make_db(tmp_path)
        try:
            conn = _db_conn(dbm)
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
            _db_conn(dbm).close()


class TestPendingCandidatesSyncRecordId:
    """sync_record_id 字段写入与回读测试

    验证候选沉淀时关联的 sync_records 行 id 被正确持久化，
    用于后续「候选确认即补发」回写原记录状态。
    """

    def test_insert_writes_sync_record_id(self, tmp_path):
        """首次沉淀时 sync_record_id 被写入"""
        dbm = _make_db(tmp_path)
        try:
            row_id = dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                trace={"steps": []},
                sync_record_id=42,
            )
            assert row_id is not None

            record = _record(dbm, row_id)
            assert record["sync_record_id"] == 42

            # 列表接口也返回该字段
            result = dbm.get_pending_candidates(status="pending")
            assert result["records"][0]["sync_record_id"] == 42
        finally:
            _db_conn(dbm).close()

    def test_insert_without_sync_record_id_defaults_null(self, tmp_path):
        """不传 sync_record_id 时默认 NULL"""
        dbm = _make_db(tmp_path)
        try:
            row_id = dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
            )
            record = _record(dbm, row_id)
            assert record["sync_record_id"] is None
        finally:
            _db_conn(dbm).close()

    def test_upsert_refreshes_sync_record_id(self, tmp_path):
        """同 key 重复沉淀时 sync_record_id 刷新为最新值"""
        dbm = _make_db(tmp_path)
        try:
            id1 = dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                sync_record_id=100,
            )
            # 同 key 再次沉淀，刷新 sync_record_id
            id2 = dbm.log_pending_candidate(
                request_title="测试番剧",
                request_season=1,
                user_name="user1",
                source="plex",
                candidates=_make_candidates(),
                sync_record_id=200,
            )
            assert id1 == id2

            record = _record(dbm, id1)
            assert record["sync_record_id"] == 200
        finally:
            _db_conn(dbm).close()


class TestLlmFieldProjection:
    """llm_subject_id / llm_reason 由 candidates_json 投影（旧数据回退列值）。

    唯一真相源为 candidates_json；DB 两列为兼容保留，读取时仅作回退。
    """

    def _insert(
        self,
        dbm,
        candidates_json,
        llm_subject_id="",
        llm_reason="",
        sync_record_id=None,
        business_key="",
    ):
        conn = _db_conn(dbm)
        cur = conn.execute(
            """
            INSERT INTO pending_candidates
            (created_at, request_title, request_season, user_name, source, status,
             candidates_json, trace_json, llm_subject_id, llm_reason,
             sync_record_id, business_key)
            VALUES (datetime('now'), '投影标题', 1, 'u', 'plex', 'pending',
                    ?, '{}', ?, ?, ?, ?)
            """,
            (
                candidates_json,
                llm_subject_id,
                llm_reason,
                sync_record_id,
                business_key,
            ),
        )
        conn.commit()
        return cur.lastrowid

    def test_projects_last_llm_entry(self, tmp_path):
        """多条 llm 条目 → 取最后一条的 subject_id / reason。"""
        dbm = _make_db(tmp_path)
        try:
            cid = self._insert(
                dbm,
                json.dumps(
                    [
                        {"subject_id": "111", "name": "规则", "score": 0.9},
                        {
                            "subject_id": "222",
                            "source": "llm_assist",
                            "reason": "旧理由",
                        },
                        {
                            "subject_id": "333",
                            "source": "llm_assist",
                            "reason": "新理由",
                        },
                    ],
                    ensure_ascii=False,
                ),
            )
            rec = _record(dbm, cid)
            assert rec["llm_subject_id"] == "333"
            assert rec["llm_reason"] == "新理由"
        finally:
            _db_conn(dbm).close()

    def test_falls_back_to_column_without_llm_entry(self, tmp_path):
        """candidates_json 无 llm 条目 → 保留列值（旧数据兼容）。"""
        dbm = _make_db(tmp_path)
        try:
            cid = self._insert(
                dbm,
                json.dumps(
                    [{"subject_id": "111", "name": "规则", "score": 0.9}],
                    ensure_ascii=False,
                ),
                llm_subject_id="999",
                llm_reason="列内旧理由",
            )
            rec = _record(dbm, cid)
            assert rec["llm_subject_id"] == "999"
            assert rec["llm_reason"] == "列内旧理由"
        finally:
            _db_conn(dbm).close()

    def test_falls_back_to_column_when_llm_entry_missing_reason(self, tmp_path):
        """llm 条目缺 reason → reason 回退列值，subject_id 仍取 JSON。"""
        dbm = _make_db(tmp_path)
        try:
            cid = self._insert(
                dbm,
                json.dumps([{"subject_id": "444", "source": "llm_assist"}]),
                llm_subject_id="999",
                llm_reason="列内旧理由",
            )
            rec = _record(dbm, cid)
            assert rec["llm_subject_id"] == "444"
            assert rec["llm_reason"] == "列内旧理由"
        finally:
            _db_conn(dbm).close()

    def test_invalid_candidates_json_keeps_columns(self, tmp_path):
        """非法 JSON → 原样保留列值（不抛错）。"""
        dbm = _make_db(tmp_path)
        try:
            cid = self._insert(
                dbm, "not-json", llm_subject_id="888", llm_reason="列内旧理由"
            )
            rec = _record(dbm, cid)
            assert rec["llm_subject_id"] == "888"
            assert rec["llm_reason"] == "列内旧理由"
        finally:
            _db_conn(dbm).close()

    def test_projects_in_list_path(self, tmp_path):
        """列表读取路径同样投影。"""
        dbm = _make_db(tmp_path)
        try:
            self._insert(
                dbm,
                json.dumps(
                    [
                        {
                            "subject_id": "555",
                            "source": "llm_assist",
                            "reason": "列表理由",
                        }
                    ],
                    ensure_ascii=False,
                ),
            )
            result = dbm.get_pending_candidates(status="pending")
            assert result["records"][0]["llm_subject_id"] == "555"
            assert result["records"][0]["llm_reason"] == "列表理由"
        finally:
            _db_conn(dbm).close()

    def test_projects_in_find_latest_by_business_key(self, tmp_path):
        """find_latest_by_business_key（SELECT * 路径）同样投影。"""
        dbm = _make_db(tmp_path)
        try:
            self._insert(
                dbm,
                json.dumps(
                    [
                        {
                            "subject_id": "666",
                            "source": "llm_assist",
                            "reason": "bk理由",
                        }
                    ],
                    ensure_ascii=False,
                ),
                business_key="match|u|投影标题|1",
            )
            row = dbm._pending.find_latest_by_business_key("match|u|投影标题|1")
            assert row is not None
            assert row["llm_subject_id"] == "666"
            assert row["llm_reason"] == "bk理由"
        finally:
            _db_conn(dbm).close()

    def test_projects_non_str_subject_and_reason_without_raising(self, tmp_path):
        """混合脏类型（list subject_id / int reason / 非 dict 条目）→ 投影不抛。

        实际实现：``subject_id``/``reason`` 非 None/"" 时统一 ``str()`` 兜底，
        因此 list → "[111]"、int → "3"，并覆盖旧列值（非回退）。
        """
        dbm = _make_db(tmp_path)
        try:
            cid = self._insert(
                dbm,
                json.dumps(
                    [
                        "非 dict 条目",
                        {"subject_id": [111], "source": "llm_assist", "reason": 3},
                        42,
                    ],
                    ensure_ascii=False,
                ),
                llm_subject_id="999",
                llm_reason="列内旧理由",
            )
            rec = _record(dbm, cid)
            assert rec["llm_subject_id"] == "[111]"
            assert rec["llm_reason"] == "3"
        finally:
            _db_conn(dbm).close()

    def test_projects_dict_subject_id_as_string_repr(self, tmp_path):
        """dict 类型 subject_id → ``str(dict)`` 兜底；reason=0 也应投影（非空值语义）。"""
        dbm = _make_db(tmp_path)
        try:
            cid = self._insert(
                dbm,
                json.dumps(
                    [
                        {
                            "subject_id": {"id": 1},
                            "source": "llm_assist",
                            "reason": 0,
                        }
                    ]
                ),
                llm_subject_id="999",
                llm_reason="列内旧理由",
            )
            rec = _record(dbm, cid)
            assert rec["llm_subject_id"] == "{'id': 1}"
            assert rec["llm_reason"] == "0"
        finally:
            _db_conn(dbm).close()
