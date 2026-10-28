"""匹配场景服务 llm_assist 测试（无候选全链路 + 兜底 + 注入防护 + 事务）。

使用全局 database_manager（conftest 已重定向到临时 DB）验证落库与状态机；
LLM 调用、bgm、校验、通知均以 mock 注入，保证单测稳定与行为精确断言。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from contextlib import suppress
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.database import database_manager, set_database_manager
from app.services.agent.execution.tools import (
    ToolDefinition,
    ToolRegistry,
)
from app.services.agent.loop import RunResult
from app.services.agent.observe import recorder
from app.services.agent.observe.recorder import TraceRecorder
from app.services.agent.observe.trace import (
    ReplayResponse,
    ReplayResult,
    ReplayToolCall,
)
from app.services.agent.run import runtime
from app.services.llm.models import (
    ChatResponse,
    Message,
    StreamChunk,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from app.services.matching import llm_assist
from app.services.matching.identity import build_match_business_key
from app.services.matching.llm_assist import build_seed_messages
from app.services.sync_service.orchestrator import (
    MATCH_ASSIST_MAX_TOTAL_ATTEMPTS,
    MATCH_ASSIST_REUSE_WINDOW_DAYS,
)


@pytest.fixture(autouse=True)
def _align_db_singleton():
    """对齐 get_database_manager() 与测试实例（conftest 仅设模块属性）。"""
    set_database_manager(database_manager)
    yield
    set_database_manager(None)


def _db_conn() -> sqlite3.Connection:
    """返回底层 sqlite3 连接并显式收窄（测试初始化后必然非 None）。"""
    conn = database_manager._connection._conn
    assert conn is not None, "测试数据库连接未初始化"
    return conn


def _run_row(run_id: str) -> dict:
    """读取 agent_runs 行并显式收窄（测试前提：run 已落库）。"""
    row = database_manager.agent_runs.get_run(run_id)
    assert row is not None, f"agent_run {run_id} 未落库"
    return row


def _make_sync_record(with_candidates=False, sync_record_id=1):
    if with_candidates:
        match_trace = {
            "steps": [
                {
                    "stage": "api_search",
                    "status": "hit",
                    "candidates": [
                        {
                            "subject_id": "111",
                            "name": "Rule A",
                            "name_cn": "规则A",
                            "score": 0.8,
                        },
                        {
                            "subject_id": "222",
                            "name": "Rule B",
                            "name_cn": "规则B",
                            "score": 0.5,
                        },
                    ],
                }
            ]
        }
    else:
        match_trace = {
            "steps": [{"stage": "api_search", "status": "miss", "candidates": []}]
        }
    # 标题随 sync_record_id 变化，避免 pending_candidates 唯一索引在共享 DB 上冲突
    return {
        "id": sync_record_id,
        "title": f"标题{sync_record_id}",
        "ori_title": "花咲くいろは",
        "season": 1,
        "episode": 0,
        "media_type": "episode",
        "release_date": "2012",
        "user_name": "alice",
        "source": "plex",
        "match_trace": match_trace,
    }


def _search_response(content=""):
    return ChatResponse(
        content=content,
        stop_reason="tool_use",
        blocks=[
            ToolUseBlock(id="t1", name="search_bangumi", input={"title": "花开伊吕波"})
        ],
    )


def _submit_response(subject_id="123", reason="跨季匹配"):
    return ChatResponse(
        content="",
        stop_reason="tool_use",
        blocks=[
            ToolUseBlock(
                id="s1",
                name="submit_suggestion",
                input={"subject_id": subject_id, "reason": reason},
            )
        ],
    )


def _read_candidate(sync_record_id):
    """经仓储读取 pending_candidates 行（llm 两列为 candidates_json 投影值）。"""
    return database_manager._pending.get_pending_candidate_by_sync_record_id(
        sync_record_id
    )


def _read_raw_llm_columns(sync_record_id):
    """直读 DB 的 llm 两列（验证写入侧不再写列，仅写 candidates_json）。"""
    conn = _db_conn()
    row = conn.execute(
        "SELECT llm_subject_id, llm_reason FROM pending_candidates "
        "WHERE sync_record_id=? ORDER BY id DESC LIMIT 1",
        (sync_record_id,),
    ).fetchone()
    if not row:
        return None
    return {"llm_subject_id": row[0], "llm_reason": row[1]}


def _read_candidate_business_key(sync_record_id):
    """直读 pending_candidates.business_key（验证写入侧业务键口径）。"""
    conn = _db_conn()
    row = conn.execute(
        "SELECT business_key FROM pending_candidates "
        "WHERE sync_record_id=? ORDER BY id DESC LIMIT 1",
        (sync_record_id,),
    ).fetchone()
    return row[0] if row else None


def _read_full_candidate(candidate_id):
    """读取候选行全部关注列（用于断言是否被复活改写）。"""
    conn = _db_conn()
    row = conn.execute(
        "SELECT id, status, confirmed_subject_id, resolved_at, candidates_json, "
        "llm_subject_id, llm_reason FROM pending_candidates WHERE id=?",
        (candidate_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "status": row[1],
        "confirmed_subject_id": row[2],
        "resolved_at": row[3],
        "candidates_json": row[4],
        "llm_subject_id": row[5],
        "llm_reason": row[6],
    }


def _read_candidate_via_independent_connection(sync_record_id):
    """用**独立 sqlite3 连接**读取候选行，验证落库已提交（不依赖同连接未提交视图）。

    返回 ``(id, status, candidates_json)``；无行返回 ``None``。
    """
    import sqlite3

    db_path = str(database_manager._connection.db_path)
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT id, status, candidates_json FROM pending_candidates "
            "WHERE sync_record_id=? ORDER BY id DESC LIMIT 1",
            (sync_record_id,),
        ).fetchone()
    finally:
        conn.close()


def _read_run_via_independent_connection(run_id):
    """用**独立 sqlite3 连接**读取 agent_runs 行（status, stop_reason）。"""
    import sqlite3

    db_path = str(database_manager._connection.db_path)
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT status, stop_reason FROM agent_runs WHERE run_id=?", (run_id,)
        ).fetchone()
    finally:
        conn.close()


def _assert_candidate_written(sync_record_id, subject_id="123", reason="跨季匹配"):
    row = _read_candidate(sync_record_id)
    assert row is not None, "pending_candidates 行应已写入"
    assert row["llm_subject_id"] == subject_id, "llm_subject_id 应为 JSON 投影值"
    assert row["llm_reason"] == reason, "llm_reason 应为 JSON 投影值"
    assert row["status"] == "pending"
    candidates = json.loads(row["candidates_json"]) if row["candidates_json"] else []
    assert any(str(c.get("subject_id")) == subject_id for c in candidates)
    llm_entries = [c for c in candidates if c.get("source") == "llm_assist"]
    assert llm_entries and llm_entries[-1].get("reason") == reason, (
        "candidates_json 应承载 llm 建议的 reason"
    )
    return row


# ---------------------------------------------------------------------------
# 有候选 + submit_suggestion → 更新既有 pending_candidates 行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_submit_suggestion_updates_existing_candidate(monkeypatch):
    run_id = "run-m5"
    sr_id = 1
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    # 既有规则沉淀行（有候选）
    database_manager.log_pending_candidate(
        request_title="花开伊吕波剧场版",
        request_ori_title="花咲くいろは",
        request_season=1,
        request_episode=0,
        user_name="alice",
        source="plex",
        candidates=[
            {"subject_id": "111", "name": "Rule A", "name_cn": "规则A", "score": 0.8}
        ],
        sync_record_id=sr_id,
    )
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    bgm = _make_bgm()
    ns = _make_notify()

    chat = _chat_side_effect([_search_response(), _submit_response()])

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, bgm),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        notification_service=ns,
        span_recorder=None,
    )

    assert status == "succeeded"
    run_row = _run_row(run_id)
    assert run_row["status"] == "succeeded"
    assert run_row["stop_reason"] == "submit_suggestion"

    row = _assert_candidate_written(sr_id)
    # 既有候选保留 + 追加 LLM 推荐
    candidates = json.loads(row["candidates_json"])
    assert any(str(c.get("subject_id")) == "111" for c in candidates)
    assert any(str(c.get("subject_id")) == "123" for c in candidates)

    # 通知 best-effort 触发，且带 is_llm_suggestion
    ns.notify.assert_called_once()
    _, kwargs = ns.notify.call_args
    assert kwargs.get("is_llm_suggestion") is True
    assert kwargs.get("llm_reason") == "跨季匹配"


# ---------------------------------------------------------------------------
# 无候选 → 新建行（candidates_json=[] + llm 两列）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_submit_suggestion_creates_new_row_when_no_candidate(monkeypatch):
    run_id = "run-m5b"
    sr_id = 2
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=False, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    bgm = _make_bgm()
    ns = _make_notify()

    chat = _chat_side_effect(
        [_search_response(), _submit_response("456", "无候选补充")]
    )

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, bgm),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        notification_service=ns,
        span_recorder=None,
    )

    assert status == "succeeded"
    row = _assert_candidate_written(sr_id, subject_id="456", reason="无候选补充")
    # 无候选场景：candidates_json 仅含 LLM 推荐（无规则候选）
    candidates = json.loads(row["candidates_json"])
    assert len(candidates) == 1
    assert str(candidates[0]["subject_id"]) == "456"


# ---------------------------------------------------------------------------
# LLM 新建候选必须写入 business_key，闭合「写→查→复用」去重契约
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_submit_suggestion_new_row_writes_business_key(monkeypatch):
    """无候选新建行时 business_key 必须与 enqueue 同口径（user/title/season）。"""
    run_id = "run-bk-new"
    sr_id = 901
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=False, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    chat = _chat_side_effect([_submit_response("456", "无候选补充")])

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _make_bgm()),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        span_recorder=None,
    )

    assert status == "succeeded"
    expected_bk = build_match_business_key(sr["user_name"], sr["title"], sr["season"])
    assert _read_candidate_business_key(sr_id) == expected_bk, (
        "LLM 新建候选行的 business_key 必须与 enqueue_match_run 同口径"
    )


@pytest.mark.asyncio
async def test_llm_candidate_reuse_hit_via_business_key_closed_loop(monkeypatch):
    """端到端：run 落库候选后，同一 business_key 经入队决策命中 reuse_holding。"""
    run_id = "run-bk-loop"
    sr_id = 902
    sr = _make_sync_record(with_candidates=False, sync_record_id=sr_id)
    bk = build_match_business_key(sr["user_name"], sr["title"], sr["season"])
    # 与生产同口径：agent_run 入队时即携带 business_key
    database_manager.agent_runs.create_pending(run_id, "match", sr_id, business_key=bk)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    chat = _chat_side_effect([_submit_response("456", "闭环")])

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _make_bgm()),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        span_recorder=None,
    )
    assert status == "succeeded"

    # 再以同一业务键入队：最新 run=succeeded + 候选 pending → reuse_holding（复用原 run）
    decision = database_manager.agent_runs.enqueue_match_run(
        run_id="run-bk-loop-retry",
        business_key=bk,
        sync_record_id=None,
        reuse_window_days=MATCH_ASSIST_REUSE_WINDOW_DAYS,
        max_total_attempts=MATCH_ASSIST_MAX_TOTAL_ATTEMPTS,
        accepted_mapping_valid=True,
    )
    assert decision["decision"] == "reuse_holding", (
        "同一 business_key 的 LLM 候选应可被复用判定命中"
    )
    assert decision["run_id"] == run_id


@pytest.mark.asyncio
async def test_run_submit_suggestion_backfills_business_key_on_existing_row(
    monkeypatch,
):
    """更新既有（历史无 business_key）行时补写业务键，供后续复用命中。"""
    run_id = "run-bk-backfill"
    sr_id = 903
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    # 历史行：log_pending_candidate 未传 business_key（默认空串）
    database_manager.log_pending_candidate(
        request_title=f"legacy-{sr_id}",
        request_season=1,
        user_name="alice",
        source="plex",
        candidates=[{"subject_id": "111", "name": "A", "score": 0.8}],
        sync_record_id=sr_id,
    )
    assert _read_candidate_business_key(sr_id) == ""
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    chat = _chat_side_effect([_submit_response("123", "补写业务键")])

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _make_bgm()),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        span_recorder=None,
    )

    assert status == "succeeded"
    expected_bk = build_match_business_key(sr["user_name"], sr["title"], sr["season"])
    assert _read_candidate_business_key(sr_id) == expected_bk


# ---------------------------------------------------------------------------
# candidates_json 为唯一写入源——仓储读取投影，DB 两列不再写入
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_submit_suggestion_projects_llm_fields_and_leaves_columns_empty(
    monkeypatch,
):
    """写入 LLM 建议后：仓储读取得到投影值，DB llm 两列保持空（不双写）。"""
    run_id = "run-proj-write"
    sr_id = 21
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=False, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    chat = _chat_side_effect([_submit_response("777", "投影理由")])

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _make_bgm()),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        span_recorder=None,
    )

    assert status == "succeeded"
    # 仓储读取：两列由 candidates_json 投影
    rec = _read_candidate(sr_id)
    assert rec["llm_subject_id"] == "777"
    assert rec["llm_reason"] == "投影理由"
    # 写入侧不再写 DB 两列
    raw = _read_raw_llm_columns(sr_id)
    assert raw is not None
    assert raw["llm_subject_id"] == "", "llm_subject_id 列不应再被写入"
    assert raw["llm_reason"] == "", "llm_reason 列不应再被写入"
    # reason 随 llm 候选条目落 candidates_json
    cands = json.loads(rec["candidates_json"])
    llm_entries = [c for c in cands if c.get("source") == "llm_assist"]
    assert len(llm_entries) == 1
    assert llm_entries[-1].get("reason") == "投影理由"


@pytest.mark.asyncio
async def test_run_submit_suggestion_marks_existing_candidate_on_duplicate_subject(
    monkeypatch,
):
    """LLM 推荐与既有候选同 subject_id → 不重复追加，但 JSON 条目标注 llm 标记，
    保证投影仍能读到本次建议（candidates_json 为唯一真相源）。"""
    run_id = "run-dup-subject"
    sr_id = 22
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    database_manager.log_pending_candidate(
        request_title=f"dup-{sr_id}",
        request_season=1,
        user_name="alice",
        source="plex",
        candidates=[{"subject_id": "123", "name": "Rule A", "score": 0.9}],
        sync_record_id=sr_id,
    )
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    chat = _chat_side_effect([_submit_response("123", "同候选加强")])

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _make_bgm()),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        span_recorder=None,
    )

    assert status == "succeeded"
    rec = _read_candidate(sr_id)
    assert rec["llm_subject_id"] == "123"
    assert rec["llm_reason"] == "同候选加强"
    cands = json.loads(rec["candidates_json"])
    same = [c for c in cands if str(c.get("subject_id")) == "123"]
    assert len(same) == 1, "同 subject_id 不应重复追加候选"
    assert same[0]["source"] == "llm_assist"
    assert same[0]["reason"] == "同候选加强"


# ---------------------------------------------------------------------------
# 恢复续跑/迟到提交与用户确认的竞态——候选不得「复活」
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved_status", ["confirmed", "rejected"])
async def test_run_submit_skips_reviving_resolved_candidate(
    monkeypatch, resolved_status
):
    """既有候选已被用户处理（confirmed/rejected）→ 不复活为 pending：
    run 标 cancelled(user_resolved)、通知不发送、候选行完全不变。"""
    run_id = f"run-race-{resolved_status}"
    sr_id = 30 if resolved_status == "confirmed" else 31
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    cid = database_manager.log_pending_candidate(
        request_title=f"race-{sr_id}",
        request_season=1,
        user_name="alice",
        source="plex",
        candidates=[{"subject_id": "111", "name": "A", "score": 0.8}],
        sync_record_id=sr_id,
    )
    assert cid
    assert database_manager.update_pending_candidate_status(cid, resolved_status, "111")
    before = _read_full_candidate(cid)
    assert before is not None and before["status"] == resolved_status

    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)
    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    bgm = _make_bgm()
    ns = _make_notify()
    chat = _chat_side_effect([_submit_response("123", "r")])

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, bgm),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        notification_service=ns,
        span_recorder=None,
    )

    # 跳过信号：非 succeeded
    assert status == ""
    run_row = _run_row(run_id)
    assert run_row["status"] == "cancelled"
    assert run_row["stop_reason"] == "user_resolved"
    assert run_row["ended_at"] > 0
    # 候选行完全不变（status / 确认字段 / candidates_json / llm 列）
    assert _read_full_candidate(cid) == before, "已处理候选行不得被复活改写"
    ns.notify.assert_not_called()


def test_persist_llm_candidate_concurrent_resolution_marks_cancelled(monkeypatch):
    """SELECT 后候选被并发处理（UPDATE rowcount=0）→ 不复活，run cancelled，返回 None。"""
    run_id = "run-concurrent-resolve"
    sr_id = 32
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    cid = database_manager.log_pending_candidate(
        request_title=f"rule-{sr_id}",
        request_season=1,
        user_name="alice",
        source="plex",
        candidates=[{"subject_id": "111", "name": "A", "score": 0.8}],
        sync_record_id=sr_id,
    )
    assert cid
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    real_conn = database_manager._connection._conn

    class _ZeroCursor:
        rowcount = 0

    class _Conn:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, params=()):
            # 模拟 SELECT 与 UPDATE 之间候选被用户处理：带守卫 UPDATE 影响 0 行
            if "UPDATE pending_candidates" in sql:
                return _ZeroCursor()
            return self._real.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(database_manager._connection, "_conn", _Conn(real_conn))

    returned = llm_assist._persist_llm_candidate(
        database_manager,
        run_id=run_id,
        sync_record_id=sr_id,
        sync_record=sr,
        business_key=build_match_business_key(
            sr["user_name"], sr["title"], sr["season"]
        ),
        subject_id="123",
        reason="r",
        stop_reason="submit_suggestion",
    )

    assert returned is None, "并发处理时不应返回候选 id（跳过信号）"
    run_row = _run_row(run_id)
    assert run_row["status"] == "cancelled"
    assert run_row["stop_reason"] == "user_resolved"
    # 候选行未被改写（原 pending 行保持原状）
    unchanged = _read_full_candidate(cid)
    assert unchanged["status"] == "pending"
    assert unchanged["llm_subject_id"] == ""
    assert unchanged["llm_reason"] == ""


def test_persist_content_cas_skips_when_json_concurrently_modified(monkeypatch):
    """SELECT 后候选 JSON 被并发修改（内容型 CAS 失配）→ 不覆盖并发值。

    status 保持 pending（隔离「状态守卫」语义），仅由另一连接改写 candidates_json：
    - 无内容 CAS 时：UPDATE 命中 1 行 → 覆盖并发写入 → 双写 + 双通知（缺陷）
    - 有内容 CAS 时：COALESCE 比对旧值失配 → rowcount=0 → run cancelled、
      返回 False、不通知、并发写入的 JSON 保持原样
    """
    import sqlite3

    run_id = "run-content-cas"
    sr_id = 35
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    cid = database_manager.log_pending_candidate(
        request_title=f"cas-{sr_id}",
        request_season=1,
        user_name="alice",
        source="plex",
        candidates=[{"subject_id": "111", "name": "A", "score": 0.8}],
        sync_record_id=sr_id,
    )
    assert cid
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    # 并发方改写后的内容（含新增条目，必然与 SELECT 时读到的 JSON 不同）
    concurrent_json = json.dumps(
        [
            {"subject_id": "111", "name": "A", "score": 0.8},
            {"subject_id": "555", "name": "并发新增", "score": 0.6},
        ],
        ensure_ascii=False,
    )
    db_path = str(database_manager._connection.db_path)
    real_conn = database_manager._connection._conn

    class _Conn:
        """包装真实连接：在首次 pending_candidates UPDATE 前用独立连接并发改写该行。"""

        def __init__(self, real):
            self._real = real
            self._injected = False

        def execute(self, sql, params=()):
            if not self._injected and "UPDATE pending_candidates" in sql:
                self._injected = True
                # 独立 sqlite3 连接 = 模拟另一进程；此时主连接尚未开启写事务（仅 SELECT）
                other = sqlite3.connect(db_path)
                try:
                    other.execute(
                        "UPDATE pending_candidates SET candidates_json=? WHERE id=?",
                        (concurrent_json, cid),
                    )
                    other.commit()
                finally:
                    other.close()
            return self._real.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(database_manager._connection, "_conn", _Conn(real_conn))

    ns = _make_notify()
    result = llm_assist._persist_and_notify(
        database_manager,
        run_id,
        sync_record=sr,
        sync_record_id=sr_id,
        bgm=None,
        subject_id="123",
        reason="r",
        stop_reason="submit_suggestion",
        total_tokens=0,
        notification_service=ns,
    )

    assert result is False, "内容 CAS 失配应返回 False（跳过通知）"
    run_row = _run_row(run_id)
    assert run_row["status"] == "cancelled"
    assert run_row["stop_reason"] == "user_resolved"
    # 并发写入的 candidates_json 未被本 run 覆盖
    assert _read_full_candidate(cid)["candidates_json"] == concurrent_json, (
        "内容型 CAS 失配时不得覆盖并发方写入的 candidates_json"
    )
    ns.notify.assert_not_called()


@pytest.mark.parametrize("raw_value", [None, ""], ids=["null", "empty"])
def test_persist_content_cas_matches_null_or_empty_json(raw_value):
    """COALESCE 语义——candidates_json 为 NULL/空串时 CAS 仍能命中并正常落库。"""
    sr_id = 36 if raw_value is None else 37
    run_id = f"run-cas-raw-{sr_id}"
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    cid = database_manager.log_pending_candidate(
        request_title=f"cas-raw-{sr_id}",
        request_season=1,
        user_name="alice",
        source="plex",
        candidates=[{"subject_id": "111", "name": "A", "score": 0.8}],
        sync_record_id=sr_id,
    )
    assert cid
    # 预置为 NULL / 空串（历史脏数据 / 手工沉淀）
    conn = _db_conn()
    conn.execute(
        "UPDATE pending_candidates SET candidates_json=? WHERE id=?", (raw_value, cid)
    )
    conn.commit()

    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)
    returned = llm_assist._persist_llm_candidate(
        database_manager,
        run_id=run_id,
        sync_record_id=sr_id,
        sync_record=sr,
        business_key=build_match_business_key(
            sr["user_name"], sr["title"], sr["season"]
        ),
        subject_id="123",
        reason="r",
        stop_reason="submit_suggestion",
    )

    assert returned == cid, "COALESCE 应把 NULL/空串归一并命中内容 CAS"
    run_row = _run_row(run_id)
    assert run_row["status"] == "succeeded"
    cands = json.loads(_read_full_candidate(cid)["candidates_json"])
    assert any(str(c.get("subject_id")) == "123" for c in cands), (
        "NULL/空串行应能追加 LLM 候选"
    )


def test_persist_llm_candidate_succeeded_guard_skips_when_run_terminal(log_records):
    """succeeded UPDATE 带源状态守卫：run 已被并发终态化 → 不翻回 succeeded。

    竞态：候选写入前 run 已被别的路径标 cancelled。succeeded UPDATE 命中 0 行时，
    必须记 warning、返回 None（跳过通知），且候选写入保留。
    """
    run_id = "run-guard-succeeded"
    sr_id = 33
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    # 预置为终态 cancelled（模拟并发路径先把 run 收口）
    assert (
        database_manager.agent_runs.mark_cancelled(run_id, stop_reason="user_resolved")
        is True
    )
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    returned = llm_assist._persist_llm_candidate(
        database_manager,
        run_id=run_id,
        sync_record_id=sr_id,
        sync_record=sr,
        business_key=build_match_business_key(
            sr["user_name"], sr["title"], sr["season"]
        ),
        subject_id="123",
        reason="r",
        stop_reason="submit_suggestion",
    )

    assert returned is None, "run 已被并发终态化时应返回 None（跳过通知）"
    run_row = _run_row(run_id)
    assert run_row["status"] == "cancelled", "终态不得被 succeeded 守卫漏过而翻回"
    assert run_row["stop_reason"] == "user_resolved"
    # 必须留痕：warning 含 run_id 与当前状态
    warnings = [line for level, line in log_records if level == "WARNING"]
    assert any(run_id in line and "守卫命中 0 行" in line for line in warnings), (
        f"succeeded 守卫命中 0 行必须记 warning（含 run_id），实际 {warnings}"
    )
    assert any("当前状态=cancelled" in line for line in warnings), (
        f"warning 应包含当前状态便于排查，实际 {warnings}"
    )


def test_persist_and_notify_persist_error_marks_failed_no_raise(monkeypatch):
    """落库异常不再裸抛：run 终态 failed/persist_error，last_error 落库，返回 False 且不通知。"""
    import sqlite3

    run_id = "run-persist-error"
    sr_id = 34
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    real_conn = database_manager._connection._conn

    class _Conn:
        def __init__(self, real):
            self._real = real

        def execute(self, *args, **kwargs):
            # 落库事务内抛 I/O 错误（模拟磁盘故障）
            if args and "INSERT INTO pending_candidates" in args[0]:
                raise sqlite3.OperationalError("disk I/O error")
            return self._real.execute(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(database_manager._connection, "_conn", _Conn(real_conn))

    ns = _make_notify()
    # 不得向上抛：落库异常由 _persist_and_notify 内部消化并终态化
    result = llm_assist._persist_and_notify(
        database_manager,
        run_id,
        sync_record=sr,
        sync_record_id=sr_id,
        bgm=None,
        subject_id="123",
        reason="r",
        stop_reason="submit_suggestion",
        total_tokens=0,
        notification_service=ns,
    )

    assert result is False, "落库失败应返回 False（跳过通知）"
    run_row = _run_row(run_id)
    assert run_row["status"] == "failed"
    assert run_row["stop_reason"] == "persist_error"
    assert "disk I/O error" in (run_row["last_error"] or "")
    ns.notify.assert_not_called()


def test_persist_and_notify_transient_lock_error_keeps_run_retryable(monkeypatch):
    """落库遇瞬时 ``database is locked``：保留 run 活性态重试，不终态化、不通知。

    锁抖动会随重试消失，若直接 ``mark_failed`` 将永久丢弃用户建议；
    正确处理是累计 attempts（达上限由仓储自动置 failed），run 仍可被恢复扫描拾取。
    """
    import sqlite3

    run_id = "run-persist-locked"
    sr_id = 38
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    assert database_manager.agent_runs.atomic_claim(run_id) is True  # → processing
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    real_conn = database_manager._connection._conn

    class _Conn:
        def __init__(self, real):
            self._real = real

        def execute(self, *args, **kwargs):
            if args and "INSERT INTO pending_candidates" in args[0]:
                raise sqlite3.OperationalError("database is locked")
            return self._real.execute(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(database_manager._connection, "_conn", _Conn(real_conn))

    ns = _make_notify()
    result = llm_assist._persist_and_notify(
        database_manager,
        run_id,
        sync_record=sr,
        sync_record_id=sr_id,
        bgm=None,
        subject_id="123",
        reason="r",
        stop_reason="submit_suggestion",
        total_tokens=0,
        notification_service=ns,
    )

    assert result is False, "瞬时落库失败应返回 False（本次跳过通知）"
    run_row = _run_row(run_id)
    assert run_row["status"] == "processing", "瞬时锁抖动不得终态化（应保持活性待重试）"
    assert run_row["attempts"] == 1, "瞬时失败必须累计 attempts 供上限判定"
    assert run_row["stop_reason"] in ("", None)
    assert not run_row["ended_at"], "未终态化则无 ended_at"
    ns.notify.assert_not_called()


def test_persist_and_notify_non_transient_error_still_marks_failed(monkeypatch):
    """非瞬时落库异常（如 IntegrityError）：维持 failed/persist_error 终态语义。"""
    import sqlite3

    run_id = "run-persist-integrity"
    sr_id = 39
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    assert database_manager.agent_runs.atomic_claim(run_id) is True
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    real_conn = database_manager._connection._conn

    class _Conn:
        def __init__(self, real):
            self._real = real

        def execute(self, *args, **kwargs):
            if args and "INSERT INTO pending_candidates" in args[0]:
                raise sqlite3.IntegrityError("boom")
            return self._real.execute(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(database_manager._connection, "_conn", _Conn(real_conn))

    ns = _make_notify()
    result = llm_assist._persist_and_notify(
        database_manager,
        run_id,
        sync_record=sr,
        sync_record_id=sr_id,
        bgm=None,
        subject_id="123",
        reason="r",
        stop_reason="submit_suggestion",
        total_tokens=0,
        notification_service=ns,
    )

    assert result is False
    run_row = _run_row(run_id)
    assert run_row["status"] == "failed"
    assert run_row["stop_reason"] == "persist_error"
    assert "boom" in (run_row["last_error"] or "")
    ns.notify.assert_not_called()


# ---------------------------------------------------------------------------
# 无候选全链路（LLM 搜索补充 → 建议 → 落库）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_no_candidate_full_link_search_then_submit(monkeypatch):
    run_id = "run-m24"
    sr_id = 3
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=False, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    bgm = _make_bgm(search_result=[{"id": 789, "name": "花咲くいろは HOME SWEET HOME"}])
    ns = _make_notify()

    chat = _chat_side_effect([_search_response(), _submit_response("789", "搜索补充")])

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, bgm),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        notification_service=ns,
        span_recorder=None,
    )

    assert status == "succeeded"
    row = _assert_candidate_written(sr_id, subject_id="789", reason="搜索补充")
    candidates = json.loads(row["candidates_json"])
    assert any(str(c.get("subject_id")) == "789" for c in candidates)


# ---------------------------------------------------------------------------
# submit_suggestion subject_id 非法 → succeeded + last_error（无建议也是 succeeded）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_submit_invalid_subject_id_succeeded_without_suggestion(monkeypatch):
    run_id = "run-m15"
    sr_id = 4
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    # 既有候选行（应保持不变）
    database_manager.log_pending_candidate(
        request_title=f"rule-{sr_id}",
        request_season=1,
        user_name="alice",
        source="plex",
        candidates=[{"subject_id": "111", "name": "A", "score": 0.8}],
        sync_record_id=sr_id,
    )
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    monkeypatch.setattr(
        llm_assist, "_validate_subject_id", lambda sid: (False, "subject_id 非法")
    )
    bgm = _make_bgm()
    ns = _make_notify()

    chat = _chat_side_effect([_submit_response("999999", "非法")])

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, bgm),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        notification_service=ns,
        span_recorder=None,
    )

    assert status == "succeeded"
    run_row = _run_row(run_id)
    assert run_row["status"] == "succeeded"
    assert run_row["stop_reason"] == "submit_suggestion"
    assert "非法" in (run_row["last_error"] or "")

    # 不落库：既有行 llm 两列仍为空
    row = _read_candidate(sr_id)
    assert row["llm_subject_id"] == ""
    assert row["llm_reason"] == ""
    ns.notify.assert_not_called()


# ---------------------------------------------------------------------------
# 工具执行失败（bgm.search 抛错）→ 循环继续 → 最终 succeeded(exhausted)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_tool_execution_failure_leads_to_succeeded(monkeypatch):
    run_id = "run-m25"
    sr_id = 5
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    # 既有候选行（不应被改写）
    database_manager.log_pending_candidate(
        request_title=f"rule-{sr_id}",
        request_season=1,
        user_name="alice",
        source="plex",
        candidates=[{"subject_id": "111", "name": "A", "score": 0.8}],
        sync_record_id=sr_id,
    )
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))

    class _Boom:
        def search(self, **kwargs):
            raise RuntimeError("network down")

        def get_subject(self, sid):
            return {}

        def get_related_subjects(self, sid):
            return []

        async def search_async(self, **kwargs):
            raise RuntimeError("network down")

        async def get_subject_async(self, sid):
            return {}

        async def get_related_subjects_async(self, sid):
            return []

    ns = _make_notify()
    # 每轮都调 search（失败），共 max_iterations(medium=5) 轮 → exhausted
    chat = _chat_side_effect([_search_response() for _ in range(5)])

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _Boom()),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        notification_service=ns,
        span_recorder=None,
    )

    assert status == "succeeded"
    run_row = _run_row(run_id)
    assert run_row["status"] == "succeeded"
    assert run_row["stop_reason"] == "exhausted"
    # 既有候选行未被 LLM 改写
    row = _read_candidate(sr_id)
    assert row["llm_subject_id"] == ""
    ns.notify.assert_not_called()


# ---------------------------------------------------------------------------
# 耗尽兜底：exhausted + 文本 JSON → 成功落库；文本无 JSON → succeeded(exhausted)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_exhausted_with_json_fallback_succeeds(monkeypatch):
    run_id = "run-exh-ok"
    sr_id = 6
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=False, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    bgm = _make_bgm()
    ns = _make_notify()

    # 每轮返回非终止工具调用（跑满 medium=5 轮）；收尾调用末轮 content 含 JSON
    chat = _chat_side_effect(
        [
            _search_response('{"subject_id": "321", "reason": "兜底"}'),
            _search_response('{"subject_id": "321", "reason": "兜底"}'),
            _search_response('{"subject_id": "321", "reason": "兜底"}'),
            _search_response('{"subject_id": "321", "reason": "兜底"}'),
            _search_response('{"subject_id": "321", "reason": "兜底"}'),
            # 第 6 次为兜底收尾调用（返回纯文本 JSON，不调用工具）
            ChatResponse(
                content='{"subject_id": "321", "reason": "兜底"}',
                stop_reason="end_turn",
                blocks=[],
            ),
        ]
    )

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, bgm),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        notification_service=ns,
        span_recorder=None,
    )

    assert status == "succeeded"
    _assert_candidate_written(sr_id, subject_id="321", reason="兜底")
    run_row = _run_row(run_id)
    assert run_row["stop_reason"] == "exhausted"


@pytest.mark.asyncio
async def test_run_exhausted_without_json_succeeded(monkeypatch):
    run_id = "run-exh-bad"
    sr_id = 7
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=False, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    bgm = _make_bgm()
    ns = _make_notify()

    # 每轮返回非终止工具调用（跑满 medium=5 轮）；收尾调用末轮 content 无 JSON
    chat = _chat_side_effect(
        [
            _search_response("无意义的文本"),
            _search_response("无意义的文本"),
            _search_response("无意义的文本"),
            _search_response("无意义的文本"),
            _search_response("无意义的文本"),
            # 第 6 次为兜底收尾调用（无 JSON）
            ChatResponse(content="无意义的文本", stop_reason="end_turn", blocks=[]),
        ]
    )

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, bgm),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        notification_service=ns,
        span_recorder=None,
    )

    assert status == "succeeded"
    run_row = _run_row(run_id)
    assert run_row["stop_reason"] == "exhausted"


# ---------------------------------------------------------------------------
# 注入防护：system 含不可信声明、user 被分隔符隔离
# ---------------------------------------------------------------------------


def test_build_seed_messages_injection_guard_and_isolation():
    sr = _make_sync_record(with_candidates=True, sync_record_id=1)
    candidates = llm_assist._extract_candidates(sr)
    messages = build_seed_messages(sr, candidates, llm_assist.DEFAULT_SYSTEM_TEMPLATE)

    assert len(messages) == 2
    system = messages[0]
    user = messages[1]
    assert system.role == "system"
    assert "不可信" in system.content
    assert user.role == "user"
    # 用户输入被 --- 分隔符隔离
    assert isinstance(user.content, str)
    assert user.content.count("---") >= 2
    # 用户提供的标题出现在隔离区内
    assert "标题1" in user.content
    # 候选摘要出现在 user 区（来自 trace）
    assert "规则A" in user.content


# ---------------------------------------------------------------------------
# 事务原子性：_persist_llm_candidate 内第二条 UPDATE 抛错时整体回滚
# ---------------------------------------------------------------------------


def test_persist_llm_candidate_atomic_rollback_on_failure(monkeypatch):
    """_persist_llm_candidate 内 agent_runs UPDATE 抛错 → 整体回滚，run 不 succeeded、候选 llm 列不被改写。"""
    import sqlite3

    run_id = "run-atomic"
    sr_id = 8
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    database_manager.log_pending_candidate(
        request_title=f"rule-{sr_id}",
        request_season=1,
        user_name="alice",
        source="plex",
        candidates=[{"subject_id": "111", "name": "A", "score": 0.8}],
        sync_record_id=sr_id,
    )
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    # 在事务内的第二条语句（agent_runs UPDATE）抛错，验证整体回滚。
    # sqlite3.Connection 为内置类型不可直接 monkeypatch，改用连接包装器，
    # 并经 monkeypatch 自动恢复，避免污染后续测试连接。
    real_conn = database_manager._connection._conn

    class _Conn:
        def __init__(self, real):
            self._real = real

        def execute(self, *args, **kwargs):
            if args and "UPDATE agent_runs" in args[0]:
                raise sqlite3.OperationalError("boom mid-transaction")
            return self._real.execute(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(database_manager._connection, "_conn", _Conn(real_conn))

    with pytest.raises(sqlite3.OperationalError):
        llm_assist._persist_llm_candidate(
            database_manager,
            run_id=run_id,
            sync_record_id=sr_id,
            sync_record=sr,
            business_key=build_match_business_key(
                sr["user_name"], sr["title"], sr["season"]
            ),
            subject_id="123",
            reason="r",
            stop_reason="submit_suggestion",
        )

    # 回滚验证：agent_runs 未 succeeded，pending_candidates 未被改写
    run_row = _run_row(run_id)
    assert run_row["status"] == "pending"  # 原始状态，未 succeeded
    row = _read_candidate(sr_id)
    assert row["llm_subject_id"] == ""  # 候选写入被回滚


# ---------------------------------------------------------------------------
# 事务提交：落库必须显式 commit，独立连接可见（不依赖同连接未提交视图）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_candidate_committed_and_visible_to_independent_connection(
    monkeypatch,
):
    """run 落库后必须已 commit——独立 sqlite3 连接应能看到候选行与
    agent_runs 终态，而非停留在主连接未提交事务中。"""
    run_id = "run-commit-visible"
    sr_id = 1001
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=False, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _make_bgm()),
        thinking_level="medium",
        stream_fn=_scripted_stream(
            _chat_side_effect([_submit_response("888", "提交可见")])
        ),
        notification_service=None,
        span_recorder=None,
    )

    assert status == "succeeded"

    # 独立连接读取候选行：若事务未提交（悬挂），此查询读不到
    row = _read_candidate_via_independent_connection(sr_id)
    assert row is not None, "落库完成后候选行必须对独立连接可见（已 commit）"
    assert row[1] == "pending", f"候选行 status 应为 pending，实际 {row[1]}"
    cands = json.loads(row[2])
    assert any(str(c.get("subject_id")) == "888" for c in cands), (
        "独立连接应读到本次 LLM 候选"
    )

    # agent_runs 终态同样必须已提交可见
    run_row = _read_run_via_independent_connection(run_id)
    assert run_row is not None, "agent_runs 行必须对独立连接可见"
    assert run_row[0] == "succeeded", f"run status 应为 succeeded，实际 {run_row[0]}"
    assert run_row[1] == "submit_suggestion"


def test_persist_llm_candidate_race_skip_commits_cancelled_state(monkeypatch):
    """竞态跳过路径（返回 None）同样必须 commit——run 的 cancelled
    终态要能被独立连接看到，不得依赖后续写操作代提交。"""
    run_id = "run-race-skip-commit"
    sr_id = 1002
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    # 既有候选已被用户处理（confirmed）→ 事务内 apply_cancelled 写 cancelled 后
    # 返回 None（跳过信号）；该取消写入同样必须显式提交。
    cid = database_manager.log_pending_candidate(
        request_title=f"race-skip-{sr_id}",
        request_season=1,
        user_name="alice",
        source="plex",
        candidates=[{"subject_id": "111", "name": "A", "score": 0.8}],
        sync_record_id=sr_id,
    )
    assert cid
    assert database_manager.update_pending_candidate_status(cid, "confirmed", "111")
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    returned = llm_assist._persist_llm_candidate(
        database_manager,
        run_id=run_id,
        sync_record_id=sr_id,
        sync_record=sr,
        business_key=build_match_business_key(
            sr["user_name"], sr["title"], sr["season"]
        ),
        subject_id="123",
        reason="r",
        stop_reason="submit_suggestion",
    )

    assert returned is None, "守卫命中 0 行应返回 None（跳过通知）"
    # 独立连接可见 cancelled 终态（证明本轮事务已提交）
    run_row = _read_run_via_independent_connection(run_id)
    assert run_row is not None
    assert run_row[0] == "cancelled"
    assert run_row[1] == "user_resolved"


# ---------------------------------------------------------------------------
# 并发通知覆盖：两个独立 run 并发收尾，各自恰好一次、无丢失/无重复
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_runs_persist_and_notify_each_exactly_once(monkeypatch):
    """两个独立 run（不同 run_id / sync_record）经 asyncio.gather
    并发执行收尾落库路径。

    ``_handle_result_async`` 在线程池执行 ``_persist_and_notify``，真实触发
    ``DatabaseConnection._lock`` 串行化。断言：
    - 两个 run 均成功落库且终态正确；
    - 通知各自恰好一次（总计 2，无丢失、无交叉重复）；
    - 并发过程无 ``database is locked`` 等异常。
    """
    run_a, sr_a, sid_a = "run-conc-a", 1003, "1111"
    run_b, sr_b, sid_b = "run-conc-b", 1004, "2222"
    database_manager.agent_runs.create_pending(run_a, "match", sr_a)
    database_manager.agent_runs.create_pending(run_b, "match", sr_b)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    ns = _make_notify()

    results = await asyncio.gather(
        llm_assist.get_scenario_runtime().run(
            run_a,
            ctx=_ctx(_make_sync_record(sync_record_id=sr_a), _make_bgm()),
            thinking_level="medium",
            stream_fn=_scripted_stream(
                _chat_side_effect([_submit_response(sid_a, "并发A")])
            ),
            notification_service=ns,
            span_recorder=None,
        ),
        llm_assist.get_scenario_runtime().run(
            run_b,
            ctx=_ctx(_make_sync_record(sync_record_id=sr_b), _make_bgm()),
            thinking_level="medium",
            stream_fn=_scripted_stream(
                _chat_side_effect([_submit_response(sid_b, "并发B")])
            ),
            notification_service=ns,
            span_recorder=None,
        ),
        return_exceptions=True,
    )

    # 无并发异常（含 sqlite3.OperationalError: database is locked）
    errors = [r for r in results if isinstance(r, BaseException)]
    assert not errors, f"并发 run 不应抛异常（含 database is locked），实际 {errors}"
    assert results == ["succeeded", "succeeded"], (
        f"两个 run 均应 succeeded，实际 {results}"
    )

    # 两个 run 均落库且终态正确（独立连接可见各 sync_record 行）
    for sr_id, sid in ((sr_a, sid_a), (sr_b, sid_b)):
        row = _read_candidate_via_independent_connection(sr_id)
        assert row is not None, f"sync_record_id={sr_id} 的候选行应已提交"
        assert row[1] == "pending"
        cands = json.loads(row[2])
        assert any(str(c.get("subject_id")) == sid for c in cands)

    # 通知：总计恰好 2，每个 subject 恰好一次（无丢失 / 无交叉重复）
    assert ns.notify.call_count == 2, (
        f"两个 run 应各通知一次（总计 2），实际 {ns.notify.call_count}"
    )
    notified_subjects = sorted(
        call.kwargs.get("top_candidate_id") for call in ns.notify.call_args_list
    )
    assert notified_subjects == sorted([sid_a, sid_b]), (
        f"通知 subject 应为 [{sid_a}, {sid_b}] 各一次，实际 {notified_subjects}"
    )
    reasons = sorted(call.kwargs.get("llm_reason") for call in ns.notify.call_args_list)
    assert reasons == sorted(["并发A", "并发B"]), (
        f"通知 reason 应各自匹配，实际 {reasons}"
    )


# ---------------------------------------------------------------------------
# 并发抢占：atomic_claim 失败 → 返回 skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_atomic_claim_failure_returns_skipped(monkeypatch):
    run_id = "run-skip"
    sr_id = 9
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    # 先手动置为 processing，使 atomic_claim 失败
    database_manager.agent_runs.atomic_claim(run_id)
    # 现在已是 processing，再次 claim 会失败；但 run 内部会 claim 一次。
    # 为模拟并发抢占，直接把状态改回 pending 之外的不可 claim 状态：
    database_manager.agent_runs.update_run_status(run_id, "succeeded")

    bgm = _make_bgm()
    chat = _chat_side_effect([_submit_response()])
    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(_make_sync_record(sync_record_id=sr_id), bgm),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        span_recorder=None,
    )
    assert status == "skipped"


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _chat_side_effect(responses):
    state = {"idx": 0}

    async def _chat(messages, *, tools=None, tool_choice=None):
        idx = state["idx"]
        state["idx"] += 1
        if idx < len(responses):
            return responses[idx]
        # 超出则返回 end_turn 兜底
        return ChatResponse(content="done", stop_reason="end_turn")

    return _chat


def _make_bgm(search_result=None):
    class _Bgm:
        """双协议替身：同步方法供收尾链（如 _prefetch_bgm_name），async 供工具 handler。"""

        def search(self, **kwargs):
            return search_result if search_result is not None else [{"id": 1}]

        def get_subject(self, sid):
            return {"name": f"subject-{sid}", "name_cn": f"条目-{sid}"}

        def get_related_subjects(self, sid):
            return []

        async def search_async(self, **kwargs):
            return self.search(**kwargs)

        async def get_subject_async(self, sid):
            return self.get_subject(sid)

        async def get_related_subjects_async(self, sid):
            return self.get_related_subjects(sid)

    return _Bgm()


def _ctx(sync_record: dict, bgm: Any = None) -> Any:
    """构造本次 run 的场景上下文（测试替身）。

    bgm 构造已下沉到场景侧 ``llm_assist.build_bgm``（通用层不再碰凭据），
    故测试在**构造期间**把 ``build_bgm`` 替换为固定值即可控制 ctx 内的客户端。
    """
    with patch.object(llm_assist, "build_bgm", return_value=bgm):
        return llm_assist.make_match_ctx(sync_record)


def _make_notify():
    from unittest.mock import MagicMock

    ns = MagicMock()
    ns.notify.return_value = True
    return ns


def _wire_stream_client(client, response_fn):
    """把 mock client 的 ``stream_chat`` 适配为 ``response_fn`` 展开的事件流。

    ``stream_chat`` 是 LLMClient 的唯一调用入口。测试用 ``response_fn``
    （返回 ``ChatResponse`` 的 AsyncMock）定义每轮响应，本辅助函数据其展开为
    **无停点**事件流（与 eval 回放同款），使循环走轮级执行；``response_fn``
    仍被真实调用（便于断言 thinking_level 等参数透传）。
    """
    inner = response_fn

    async def _stream(messages, *, tools=None, tool_choice=None, **kwargs):
        resp = await inner(messages, tools=tools, tool_choice=tool_choice, **kwargs)
        blocks = list(resp.blocks)
        if not blocks and resp.content:
            blocks = [TextBlock(text=resp.content)]
        for b in blocks:
            if isinstance(b, TextBlock):
                yield StreamChunk(type="text_delta", text=b.text)
            elif isinstance(b, ThinkingBlock):
                yield StreamChunk(
                    type="thinking_delta",
                    thinking=b.thinking,
                    signature=b.signature or "",
                )
            elif isinstance(b, ToolUseBlock):
                yield StreamChunk(
                    type="tool_use_start", tool_use_id=b.id, tool_name=b.name
                )
                yield StreamChunk(
                    type="tool_use_delta",
                    tool_use_id=b.id,
                    partial_json=json.dumps(b.input, ensure_ascii=False),
                )
        if resp.usage is not None:
            yield StreamChunk(type="usage", usage=resp.usage)
        yield StreamChunk(type="stop", stop_reason=resp.stop_reason)

    client.stream_chat = _stream
    return client


def _scripted_stream(script):
    """把 chat 风格脚本函数适配为流式 ``StreamFn``（供流式注入路径使用）。

    每轮透传 ``(messages, tools, tool_choice)`` 调用 ``script`` 取得
    ``ChatResponse``，展开为与生产 provider 等价的事件流：

    - ``ThinkingBlock`` → ``thinking_delta``（含 signature 增量）
    - ``TextBlock`` → ``text_delta``；无任何块但有 ``content`` 时补一个文本块
    - ``ToolUseBlock`` → ``tool_use_start`` + ``tool_use_delta`` + ``tool_use_stop``
    - ``resp.usage`` → ``usage``；结尾 ``stop`` 携带 ``stop_reason`` / ``model``

    ``script`` 抛出的异常（如 ``LLMCallError``）原样透传，不吞不兜底；
    ``script`` 返回 ``None``（脚本耗尽）时显式报错，避免静默伪造响应。
    """

    async def _stream(messages, *, tools=None, tool_choice=None):
        resp = await script(messages, tools=tools, tool_choice=tool_choice)
        if resp is None:
            raise RuntimeError("_scripted_stream 脚本耗尽：未返回 ChatResponse")
        blocks = list(resp.blocks)
        if not blocks and resp.content:
            blocks = [TextBlock(text=resp.content)]
        for block in blocks:
            if isinstance(block, TextBlock):
                yield StreamChunk(type="text_delta", text=block.text)
            elif isinstance(block, ThinkingBlock):
                yield StreamChunk(
                    type="thinking_delta",
                    thinking=block.thinking,
                    signature=block.signature or "",
                )
            elif isinstance(block, ToolUseBlock):
                yield StreamChunk(
                    type="tool_use_start",
                    tool_use_id=block.id,
                    tool_name=block.name,
                )
                yield StreamChunk(
                    type="tool_use_delta",
                    tool_use_id=block.id,
                    partial_json=json.dumps(block.input, ensure_ascii=False),
                )
                yield StreamChunk(type="tool_use_stop", tool_use_id=block.id)
        if resp.usage is not None:
            yield StreamChunk(type="usage", usage=resp.usage)
        yield StreamChunk(type="stop", stop_reason=resp.stop_reason, model=resp.model)

    return _stream


def _drain_chat_span(recorder, stream_fn, messages=None):
    """以 ``recorder.chat_span()`` 消费一轮脚本化流（同步包装，等价 loop 的单聚合用法）。

    ``resp`` 在 ``async with`` **块外**读取：``__aexit__`` 才做唯一一次 finalize。
    """
    msgs = messages if messages is not None else [Message(role="user", content="hi")]

    async def _run():
        async with recorder.chat_span() as span:
            async for chunk in stream_fn(msgs, tools=[]):
                span.feed(chunk)
        return span.resp

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# register_match_tools 重复注册必须覆盖 handler 闭包（重新绑定 bgm），
#     且覆盖时不得产生“重复注册”warning（quiet=True）


def test_register_match_tools_overwrite_no_warning(caplog):
    import logging

    from app.services.agent.execution.tools import ToolRegistry

    registry = ToolRegistry()
    bgm = _make_bgm()

    # 连续两次注册到同一 registry：第二次应覆盖 handler，且不刷 warning
    with caplog.at_level(logging.WARNING):
        llm_assist.register_match_tools(registry, bgm)
        llm_assist.register_match_tools(registry, bgm)

    # 不应出现任何“重复注册”warning
    dup = [r for r in caplog.records if "重复注册" in r.message]
    assert not dup, f"重复注册 warning 不应出现: {dup}"

    # 工具仍全部可用（首次注册即已落位）
    assert registry.get("submit_suggestion") is not None
    assert registry.get("search_bangumi") is not None
    assert registry.get("get_subject_detail") is not None
    assert registry.get("check_subject") is not None
    assert registry.get("get_related_subjects") is not None


# ---------------------------------------------------------------------------
# 工具 handler 原生协程化（bgm 异步客户端；替代 run_in_executor 线程池）
# ---------------------------------------------------------------------------


def test_match_tools_query_handlers_are_coroutine_functions():
    """3 个网络查询工具 handler 为原生协程；check_subject 保持本地同步。"""
    from app.services.agent.execution.tools import ToolRegistry

    registry = ToolRegistry()
    llm_assist.register_match_tools(registry, _make_bgm())

    for name in ("search_bangumi", "get_subject_detail", "get_related_subjects"):
        defn = registry.get(name)
        assert defn is not None
        assert asyncio.iscoroutinefunction(defn.handler), name

    check = registry.get("check_subject")
    assert check is not None
    assert not asyncio.iscoroutinefunction(check.handler)


class _AsyncOnlyBgm:
    """仅提供 async 方法的 bgm 替身：误走同步路径会 AttributeError。"""

    def __init__(self, *, search_result=None) -> None:
        self.calls: list[tuple] = []
        self._search_result = (
            search_result if search_result is not None else [{"id": 1}]
        )

    async def search_async(self, **kwargs):
        self.calls.append(("search", kwargs))
        return self._search_result

    async def get_subject_async(self, sid):
        self.calls.append(("get_subject", sid))
        return {"name": f"subject-{sid}"}

    async def get_related_subjects_async(self, sid):
        self.calls.append(("get_related", sid))
        return []


@pytest.mark.asyncio
async def test_search_tool_awaits_bgm_search_async():
    """search_bangumi 经 search_async（awaited），不走同步客户端。"""
    from app.services.agent.execution.tools import ToolRegistry

    bgm = _AsyncOnlyBgm(search_result=[{"id": 42}])
    registry = ToolRegistry()
    llm_assist.register_match_tools(registry, bgm)

    result = await registry.execute("search_bangumi", {"title": "x"})

    assert result == [{"id": 42}]
    assert bgm.calls and bgm.calls[0][0] == "search"
    assert bgm.calls[0][1]["title"] == "x"


@pytest.mark.asyncio
async def test_subject_detail_and_related_tools_await_async_bgm():
    """get_subject_detail / get_related_subjects 经对应 async 方法。"""
    from app.services.agent.execution.tools import ToolRegistry

    bgm = _AsyncOnlyBgm()
    registry = ToolRegistry()
    llm_assist.register_match_tools(registry, bgm)

    detail = await registry.execute("get_subject_detail", {"subject_id": "7"})
    related = await registry.execute("get_related_subjects", {"subject_id": "7"})

    assert detail == {"name": "subject-7"}
    assert related == []
    assert ("get_subject", 7) in bgm.calls
    assert ("get_related", 7) in bgm.calls


# ---------------------------------------------------------------------------
# match 场景 5 个工具显式标注幂等属性（契约语义，不再依赖缺省推导）


def test_register_match_tools_idempotent_flags():
    """4 个查询工具幂等（True）+ submit_suggestion 非幂等（False）。"""
    registry = ToolRegistry()
    llm_assist.register_match_tools(registry, _make_bgm())

    assert registry.is_idempotent("search_bangumi") is True
    assert registry.is_idempotent("get_subject_detail") is True
    assert registry.is_idempotent("check_subject") is True
    assert registry.is_idempotent("get_related_subjects") is True
    assert registry.is_idempotent("submit_suggestion") is False


def test_register_match_tools_idempotent_consistent_with_access():
    """显式标注与缺省推导一致：4 个 read 工具 idempotent=True；terminal idempotent=False。"""
    registry = ToolRegistry()
    llm_assist.register_match_tools(registry, _make_bgm())

    for name in (
        "search_bangumi",
        "get_subject_detail",
        "check_subject",
        "get_related_subjects",
    ):
        defn = registry.get(name)
        assert defn is not None
        assert defn.idempotent is True, name

    submit = registry.get("submit_suggestion")
    assert submit is not None
    assert submit.idempotent is False


@pytest.mark.asyncio
async def test_execute_batch_match_tools_idempotent_segment_and_terminal_capture():
    """match 5 工具批量：4 幂等查询并行成段，submit_suggestion 捕获为 terminal。"""
    from app.services.agent.execution.tools import TerminalCapture

    class _ConcurrencyProbeBgm:
        """记录 async 查询的并发峰值，替代易抖动的墙钟断言。"""

        def __init__(self) -> None:
            self.active = 0
            self.peak = 0

        async def _probe(self, result):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.05)
            finally:
                self.active -= 1
            return result

        async def search_async(self, **kwargs):
            return await self._probe([{"id": 1}])

        async def get_subject_async(self, sid):
            return await self._probe({"name": f"subject-{sid}"})

        async def get_related_subjects_async(self, sid):
            return await self._probe([])

    probe = _ConcurrencyProbeBgm()
    registry = ToolRegistry()
    llm_assist.register_match_tools(registry, probe)

    calls = [
        ToolUseBlock(id="s", name="search_bangumi", input={"title": "x"}),
        ToolUseBlock(id="g", name="get_subject_detail", input={"subject_id": "1"}),
        ToolUseBlock(id="c", name="check_subject", input={"subject_id": "1"}),
        ToolUseBlock(id="r", name="get_related_subjects", input={"subject_id": "1"}),
        ToolUseBlock(id="sub", name="submit_suggestion", input={"reason": "done"}),
    ]
    results = await registry.execute_batch(calls)

    # 3 个网络查询必须并发执行（串行时峰值为 1）
    assert probe.peak == 3
    # 槽位一一对应：4 个查询成功 + 1 个 terminal 捕获（ordered 唯一视图）
    slots = {oid: item for oid, item in results.ordered}
    for key in ("s", "g", "c", "r"):
        block = slots[key]
        assert isinstance(block, ToolResultBlock)
        assert block.is_error is False
    terminal = slots["sub"]
    assert isinstance(terminal, TerminalCapture)
    assert terminal.name == "submit_suggestion"
    assert terminal.args == {"reason": "done"}


# ---------------------------------------------------------------------------
# register_match_tools 重复注册必须覆盖 handler 闭包（重新绑定 bgm），
# 否则多用户跨 run 复用首次注册的错误 token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_match_tools_rebinds_handlers_to_latest_bgm():
    from app.services.agent.execution.tools import ToolRegistry

    registry = ToolRegistry()
    bgm1 = _make_bgm(search_result=[{"id": 1, "name": "first"}])
    bgm2 = _make_bgm(search_result=[{"id": 2, "name": "second"}])

    llm_assist.register_match_tools(registry, bgm1)
    llm_assist.register_match_tools(registry, bgm2)

    # search handler 闭包应指向第二个 bgm（协程 handler → await）
    search = registry.get("search_bangumi")
    assert search is not None
    assert await search.handler({"title": "x"}) == [{"id": 2, "name": "second"}]


@pytest.mark.asyncio
async def test_two_runs_with_different_bgm_second_run_uses_second_bgm():
    """两次 run（各自 per-run registry）→ 第二次的 search handler 调用第二个 bgm。"""
    used: list[str] = []

    def _bgm(tag: str):
        class _B:
            def search(self, **kwargs):
                used.append(tag)
                return [{"id": 1}]

            def get_subject(self, sid):
                return {"name": f"subject-{sid}", "name_cn": f"条目-{sid}"}

            def get_related_subjects(self, sid):
                return []

            async def search_async(self, **kwargs):
                used.append(tag)
                return [{"id": 1}]

            async def get_subject_async(self, sid):
                return {"name": f"subject-{sid}", "name_cn": f"条目-{sid}"}

            async def get_related_subjects_async(self, sid):
                return []

        return _B()

    bgm1, bgm2 = _bgm("bgm1"), _bgm("bgm2")

    run_a, sr_a = "run-g1-a", 60
    database_manager.agent_runs.create_pending(run_a, "match", sr_a)
    await llm_assist.get_scenario_runtime().run(
        run_a,
        ctx=_ctx(_make_sync_record(sync_record_id=sr_a), bgm1),
        thinking_level="medium",
        stream_fn=_scripted_stream(_chat_side_effect([_search_response()])),
        span_recorder=None,
    )
    assert used == ["bgm1"], "首个 run 应使用第一个 bgm"

    run_b, sr_b = "run-g1-b", 61
    database_manager.agent_runs.create_pending(run_b, "match", sr_b)
    await llm_assist.get_scenario_runtime().run(
        run_b,
        ctx=_ctx(_make_sync_record(sync_record_id=sr_b), bgm2),
        thinking_level="medium",
        stream_fn=_scripted_stream(_chat_side_effect([_search_response()])),
        span_recorder=None,
    )
    assert used == ["bgm1", "bgm2"], (
        f"第二个 run 必须使用第二个 bgm（实际 {used}）——handler 闭包不得钉死首次注册"
    )


# ---------------------------------------------------------------------------
# 场景运行入口将 config_override（llm_match_max_iterations）透传给
# get_max_iterations（优先级：配置覆盖 > 策略 > 默认）；空值传 None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_passes_config_override_to_get_max_iterations(monkeypatch):
    from unittest.mock import MagicMock

    from app.services.agent.loop import RunResult

    captured = {}

    def _gmi(task_type, thinking_level, config_override=None):
        captured["task_type"] = task_type
        captured["thinking_level"] = thinking_level
        captured["config_override"] = config_override
        return 1

    monkeypatch.setattr(llm_assist, "get_max_iterations", _gmi)

    cm = MagicMock()
    cm.get_sync_llm_match_config.return_value = {
        "llm_match_max_iterations": "10",
        "llm_match_thinking_level": "high",
    }
    monkeypatch.setattr(llm_assist, "config_manager", cm)

    async def _fake_loop(**kwargs):
        return RunResult(stop_reason="end_turn")

    monkeypatch.setattr("app.services.agent.run.runtime.run_loop", _fake_loop)

    run_id = "run-f5"
    sr_id = 50
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(sync_record_id=sr_id)
    await llm_assist.get_scenario_runtime().run(
        run_id, ctx=_ctx(sr, _make_bgm()), thinking_level="high"
    )

    assert captured["task_type"] == "match"
    assert captured["thinking_level"] == "high"
    assert captured["config_override"] == 10


@pytest.mark.asyncio
async def test_run_empty_config_override_passes_none(monkeypatch):
    from unittest.mock import MagicMock

    from app.services.agent.loop import RunResult

    captured = {}

    def _gmi(task_type, thinking_level, config_override=None):
        captured["config_override"] = config_override
        return 1

    monkeypatch.setattr(llm_assist, "get_max_iterations", _gmi)

    cm = MagicMock()
    # 空字符串（默认）→ 应传 None，交由策略/默认兜底
    cm.get_sync_llm_match_config.return_value = {
        "llm_match_max_iterations": "",
        "llm_match_thinking_level": "medium",
    }
    monkeypatch.setattr(llm_assist, "config_manager", cm)

    async def _fake_loop(**kwargs):
        return RunResult(stop_reason="end_turn")

    monkeypatch.setattr("app.services.agent.run.runtime.run_loop", _fake_loop)

    run_id = "run-f5b"
    sr_id = 51
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(sync_record_id=sr_id)
    await llm_assist.get_scenario_runtime().run(
        run_id, ctx=_ctx(sr, _make_bgm()), thinking_level="medium"
    )

    assert captured["config_override"] is None


# ---------------------------------------------------------------------------
# config_override 非正数（0 / 负值）→ 告警并回退 None（由策略默认接管），
# 否则 max_iterations<=0 会让循环空跑并把 run 滞留在 processing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,tag",
    [("0", "zero"), ("-1", "neg1"), ("-10", "neg10"), (" 0 ", "padded-zero")],
)
@pytest.mark.asyncio
async def test_run_non_positive_config_override_falls_back_to_none(
    monkeypatch, raw, tag
):
    from unittest.mock import MagicMock

    from app.services.agent.loop import RunResult

    captured = {}

    def _gmi(task_type, thinking_level, config_override=None):
        captured["config_override"] = config_override
        return 3

    monkeypatch.setattr(llm_assist, "get_max_iterations", _gmi)

    cm = MagicMock()
    cm.get_sync_llm_match_config.return_value = {
        "llm_match_max_iterations": raw,
        "llm_match_thinking_level": "medium",
    }
    monkeypatch.setattr(llm_assist, "config_manager", cm)

    log = MagicMock()
    monkeypatch.setattr(llm_assist, "logger", log)

    async def _fake_loop(**kwargs):
        return RunResult(stop_reason="end_turn")

    monkeypatch.setattr("app.services.agent.run.runtime.run_loop", _fake_loop)

    run_id = f"run-g3-{tag}"
    sr_id = 70
    assert database_manager.agent_runs.create_pending(run_id, "match", sr_id), (
        "前置：agent_run 应创建成功（否则 run 会因抢占失败提前返回 skipped）"
    )
    sr = _make_sync_record(sync_record_id=sr_id)
    status = await llm_assist.get_scenario_runtime().run(
        run_id, ctx=_ctx(sr, _make_bgm()), thinking_level="medium"
    )
    assert status != "skipped"

    assert captured["config_override"] is None, (
        f"非正数配置 {raw!r} 应回退 None，交由策略默认"
    )
    assert log.warning.called, "非正数配置应打印告警"


@pytest.mark.asyncio
async def test_run_invalid_config_override_logs_warning(monkeypatch):
    """非法字符串（无法转 int）→ 回退 None 且告警。"""
    from unittest.mock import MagicMock

    from app.services.agent.loop import RunResult

    captured = {}

    def _gmi(task_type, thinking_level, config_override=None):
        captured["config_override"] = config_override
        return 3

    monkeypatch.setattr(llm_assist, "get_max_iterations", _gmi)

    cm = MagicMock()
    cm.get_sync_llm_match_config.return_value = {
        "llm_match_max_iterations": "abc",
        "llm_match_thinking_level": "medium",
    }
    monkeypatch.setattr(llm_assist, "config_manager", cm)
    log = MagicMock()
    monkeypatch.setattr(llm_assist, "logger", log)

    async def _fake_loop(**kwargs):
        return RunResult(stop_reason="end_turn")

    monkeypatch.setattr("app.services.agent.run.runtime.run_loop", _fake_loop)

    run_id = "run-g3-invalid"
    sr_id = 71
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(_make_sync_record(sync_record_id=sr_id), _make_bgm()),
        thinking_level="medium",
    )

    assert captured["config_override"] is None
    assert log.warning.called


# ---------------------------------------------------------------------------
# resolve_max_iterations_override：场景侧薄委托 agent 骨架单一实现
# （解析逻辑唯一实现于 app.services.agent.budget，此处只验证委托与场景前缀）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_resolve_max_iterations_override_blank_returns_none_silently(blank):
    from unittest.mock import MagicMock

    log = MagicMock()
    assert llm_assist.resolve_max_iterations_override(blank, log) is None
    log.warning.assert_not_called()


def test_resolve_max_iterations_override_invalid_uses_llm_assist_prefix():
    """非法值告警由 budget 单一实现产出，但前缀绑定场景 [llm_assist]。"""
    from unittest.mock import MagicMock

    log = MagicMock()
    assert llm_assist.resolve_max_iterations_override("abc", log) is None
    log.warning.assert_called_once()
    message = log.warning.call_args[0][0]
    assert message.startswith("[llm_assist]")
    assert "[budget]" not in message


def test_resolve_max_iterations_override_positive_returns_value():
    from unittest.mock import MagicMock

    log = MagicMock()
    assert llm_assist.resolve_max_iterations_override("5", log) == 5
    log.warning.assert_not_called()


# ---------------------------------------------------------------------------
# 事务内不应发起 HTTP（bgm.get_subject 在事务外预取一次）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_does_not_call_bgm_in_transaction(monkeypatch):
    from unittest.mock import MagicMock

    bgm = MagicMock()
    bgm.get_subject.return_value = {"name": "N", "name_cn": "NC"}

    run_id = "run-f8"
    sr_id = 52
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)

    real_conn = database_manager._connection._get_connection()
    captured = {}

    def _exec_with_lock(fn):
        before = bgm.get_subject.call_count
        # 运行事务回调（经仓储 _run_write 的 _write 包装，含 commit）：
        # 必须不在此处发起 HTTP 调用
        result = fn(real_conn)
        captured["in_tx_calls"] = bgm.get_subject.call_count - before
        return result

    # 落库事务已下沉仓储，经 DatabaseConnection._execute_with_lock 取锁；
    # 包装该层以观测事务回调内是否发起 HTTP。
    monkeypatch.setattr(
        database_manager._connection, "_execute_with_lock", _exec_with_lock
    )

    llm_assist._persist_and_notify(
        database_manager,
        run_id,
        sync_record=_make_sync_record(sync_record_id=sr_id),
        sync_record_id=sr_id,
        bgm=bgm,
        subject_id="5",
        reason="r",
        stop_reason="submit_suggestion",
        total_tokens=0,
        notification_service=None,
    )

    # 事务回调内不得发起 bgm.get_subject（HTTP）
    assert captured["in_tx_calls"] == 0, "事务内不应发起 HTTP(bgm.get_subject)"
    # 事务外应预取恰好一次标题
    assert bgm.get_subject.call_count == 1, "应在事务外预取一次标题"


# ---------------------------------------------------------------------------
# 思考开关透传：llm_match_thinking_level 应同时透传到 client.stream_chat 调用
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_default_stream_fn_passes_thinking_level_medium(monkeypatch):
    """场景1：run(thinking_level="medium") 使用默认 stream_fn 时，client 收到 thinking_level='medium'。"""
    from unittest.mock import AsyncMock, MagicMock, patch

    # Mock LLMClient：response_fn 返回 end_turn 使循环立即结束；stream_chat 由其展开
    mock_client = MagicMock()
    response_fn = AsyncMock(
        return_value=ChatResponse(content="", stop_reason="end_turn")
    )
    _wire_stream_client(mock_client, response_fn)

    # get_llm_client 由 llm_assist 模块头部导入，patch 其模块命名空间
    # 不 mock run_loop：让真实循环执行，验证默认 stream_fn 确实调用 client
    with patch(
        "app.services.matching.llm_assist.get_llm_client", return_value=mock_client
    ):
        run_id = "run-think-medium"
        sr_id = 80
        database_manager.agent_runs.create_pending(run_id, "match", sr_id)
        await llm_assist.get_scenario_runtime().run(
            run_id,
            ctx=_ctx(_make_sync_record(sync_record_id=sr_id), _make_bgm()),
            thinking_level="medium",
        )

    # response_fn 应经 stream_chat 被调用，且收到 thinking_level="medium"
    assert response_fn.called, "默认 stream_fn 应调用 client.stream_chat"
    _, kwargs = response_fn.call_args
    assert kwargs.get("thinking_level") == "medium", (
        f"client.stream_chat 应收到 thinking_level='medium'，实际 kwargs={kwargs}"
    )


@pytest.mark.asyncio
async def test_run_thinking_level_high_controls_max_iterations(monkeypatch):
    """场景2：run(thinking_level="high") → run_loop 收到 max_iterations=10。"""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.agent.loop import RunResult

    mock_client = MagicMock()
    mock_client.stream_chat = AsyncMock(
        return_value=ChatResponse(content="", stop_reason="end_turn")
    )

    # 捕获 run_loop 收到的实参
    captured = {}

    async def _fake_run_loop(**kwargs):
        captured.update(kwargs)
        return RunResult(stop_reason="end_turn")

    monkeypatch.setattr("app.services.agent.run.runtime.run_loop", _fake_run_loop)

    with patch(
        "app.services.matching.llm_assist.get_llm_client", return_value=mock_client
    ):
        run_id = "run-think-high"
        sr_id = 81
        database_manager.agent_runs.create_pending(run_id, "match", sr_id)
        await llm_assist.get_scenario_runtime().run(
            run_id,
            ctx=_ctx(_make_sync_record(sync_record_id=sr_id), _make_bgm()),
            thinking_level="high",
        )

    # 验证 max_iterations 直接传入 run_loop：high → 10
    assert captured.get("max_iterations") == 10, (
        f"run_loop 应收到 max_iterations=10，实际 captured={captured}"
    )


@pytest.mark.asyncio
async def test_run_default_stream_fn_passes_thinking_level_off(monkeypatch):
    """场景3：run(thinking_level="off") → client.stream_chat 收到 thinking_level='off'。"""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_client = MagicMock()
    response_fn = AsyncMock(
        return_value=ChatResponse(content="", stop_reason="end_turn")
    )
    _wire_stream_client(mock_client, response_fn)

    with patch(
        "app.services.matching.llm_assist.get_llm_client", return_value=mock_client
    ):
        run_id = "run-think-off"
        sr_id = 82
        database_manager.agent_runs.create_pending(run_id, "match", sr_id)
        await llm_assist.get_scenario_runtime().run(
            run_id,
            ctx=_ctx(_make_sync_record(sync_record_id=sr_id), _make_bgm()),
            thinking_level="off",
        )

    assert response_fn.called, "默认 stream_fn 应调用 client.stream_chat"
    _, kwargs = response_fn.call_args
    assert kwargs.get("thinking_level") == "off", (
        f"client.stream_chat 应收到 thinking_level='off'，实际 kwargs={kwargs}"
    )


@pytest.mark.asyncio
async def test_run_custom_stream_fn_injection_unaffected(monkeypatch):
    """场景4：显式传入 stream_fn 时，不调用 get_llm_client，stream_fn 不被改签名。"""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_client = MagicMock()
    mock_client.stream_chat = AsyncMock()

    custom_called = {}

    async def custom_stream_fn(messages, *, tools=None, tool_choice=None):
        custom_called["invoked"] = True
        custom_called["tools"] = tools
        custom_called["tool_choice"] = tool_choice
        yield StreamChunk(type="stop", stop_reason="end_turn")

    with patch(
        "app.services.matching.llm_assist.get_llm_client", return_value=mock_client
    ):
        run_id = "run-custom-streamfn"
        sr_id = 83
        database_manager.agent_runs.create_pending(run_id, "match", sr_id)
        await llm_assist.get_scenario_runtime().run(
            run_id,
            ctx=_ctx(_make_sync_record(sync_record_id=sr_id), _make_bgm()),
            thinking_level="medium",
            stream_fn=custom_stream_fn,
        )

    # 自定义 stream_fn 应被直接调用
    assert custom_called.get("invoked") is True, "自定义 stream_fn 应被调用"
    # get_llm_client 不应被触发（默认 stream_fn 未构建）
    assert not mock_client.stream_chat.called, (
        "注入自定义 stream_fn 时不应触发 get_llm_client().stream_chat"
    )
    # 签名保持不变：tools / tool_choice 以关键字参数传入
    assert "tools" in custom_called
    assert "tool_choice" in custom_called


# ---------------------------------------------------------------------------
# TraceRecorder 接线：seed 行 / chat 包装 / tool span / budget 钩子
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_full_link_trace_recorder_seed_chat_tool_budget(monkeypatch):
    """正常 run 全链路——seed 行存在、chat span model/tokens 专用列有值、
    tool_execute span 由 executor 包裹产生、replay_delta 格式与现状一致。"""
    run_id = "run-tr-full"
    sr_id = 90
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=False, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    bgm = _make_bgm()

    # 捕获 trace 模块调用
    captured = {
        "start": [],
        "end": [],
    }
    original_start = recorder.start_span
    original_end = recorder.end_span

    def fake_start(run_id, name, iteration, sequence, parent_id=""):
        span_id = original_start(run_id, name, iteration, sequence, parent_id)
        captured["start"].append(
            {
                "name": name,
                "iteration": iteration,
                "sequence": sequence,
                "span_id": span_id,
            }
        )
        return span_id

    def fake_end(span_id, **kwargs):
        captured["end"].append({"span_id": span_id, **kwargs})
        return original_end(span_id, **kwargs)

    monkeypatch.setattr("app.services.agent.observe.recorder.start_span", fake_start)
    monkeypatch.setattr("app.services.agent.observe.recorder.end_span", fake_end)

    chat = _chat_side_effect([_search_response(), _submit_response("789", "搜索补充")])

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, bgm),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        span_recorder=None,
    )

    assert status == "succeeded"

    # seed 行存在
    seed_starts = [s for s in captured["start"] if s["name"] == "seed"]
    assert len(seed_starts) == 1, "应存在 1 条 seed span"
    seed_ends = [
        e for e in captured["end"] if e["span_id"] == seed_starts[0]["span_id"]
    ]
    assert len(seed_ends) == 1
    assert "seed_messages" in seed_ends[0]["replay_delta"]

    # chat span 存在（llm_chat）
    chat_starts = [s for s in captured["start"] if s["name"] == "llm_chat"]
    assert len(chat_starts) >= 1, "应存在 llm_chat span"
    chat_ends = [
        e
        for e in captured["end"]
        if e["span_id"] in [s["span_id"] for s in chat_starts]
    ]
    for ce in chat_ends:
        # replay_delta 含 response 结构
        delta = ce.get("replay_delta", {})
        assert "response" in delta, delta
        resp = delta["response"]
        assert set(resp.keys()) >= {"stop_reason", "content", "tool_calls"}

    # tool_execute span 存在（由 executor 包裹产生）
    tool_starts = [s for s in captured["start"] if s["name"] == "tool_execute"]
    assert len(tool_starts) >= 1, "应存在 tool_execute span"

    # budget 行：独立 span（name="budget"），delta 为 budget_message 形态
    budget_starts = [s for s in captured["start"] if s["name"] == "budget"]
    assert len(budget_starts) >= 1, "预算应写独立 budget 行"
    budget_ends = [
        e
        for e in captured["end"]
        if e["span_id"] in [s["span_id"] for s in budget_starts]
    ]
    assert budget_ends, "独立 budget 行应 end_span 落库"
    assert all(e["replay_delta"]["kind"] == "budget_message" for e in budget_ends), (
        budget_ends
    )


@pytest.mark.asyncio
async def test_run_recorder_none_path_semantic_preserved(monkeypatch):
    """span_recorder=None 路径语义保持——run 结果正确、loop 领域语义不破。"""
    run_id = "run-tr-none"
    sr_id = 91
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=False, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    bgm = _make_bgm()

    chat = _chat_side_effect([_submit_response("111", "直接建议")])

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, bgm),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        span_recorder=None,
    )

    assert status == "succeeded"
    run_row = _run_row(run_id)
    assert run_row["status"] == "succeeded"
    assert run_row["stop_reason"] == "submit_suggestion"


def test_trace_recorder_chat_span_tracks_iteration():
    """chat span 每轮 start/end span，iteration 自增。"""
    from unittest.mock import patch

    starts = []
    ends = []

    def fake_start(run_id, name, iteration, sequence, parent_id=""):
        starts.append({"name": name, "iteration": iteration, "sequence": sequence})
        return f"span-{name}-{iteration}-{sequence}"

    def fake_end(span_id, **kwargs):
        ends.append({"span_id": span_id, **kwargs})

    recorder = TraceRecorder("run-test", start_iteration=0)

    async def dummy_script(messages, *, tools=None, tool_choice=None):
        return ChatResponse(
            content="",
            stop_reason="end_turn",
            blocks=[],
            model="test-model",
        )

    # patch llm_assist 模块上的引用（闭包通过模块全局名字查找）
    with (
        patch("app.services.agent.observe.recorder.start_span", side_effect=fake_start),
        patch("app.services.agent.observe.recorder.end_span", side_effect=fake_end),
    ):
        _drain_chat_span(recorder, _scripted_stream(dummy_script))

    # 应有一条 llm_chat start + end
    assert len(starts) == 1
    assert starts[0]["name"] == "llm_chat"
    assert starts[0]["iteration"] == 0
    assert len(ends) == 1
    # model 写专用列（不再塞 payload_json）
    assert ends[0]["model"] == "test-model"
    assert ends[0]["status"] == "ok"
    assert "response" in ends[0]["replay_delta"]


def test_trace_recorder_tool_start_end_idempotent():
    """end_tool 幂等——同 span_id 二次调用不崩溃。"""
    from unittest.mock import patch

    from app.services.llm.models import ToolResultBlock

    starts = []
    ends = []

    def fake_start(run_id, name, iteration, sequence, parent_id=""):
        sid = f"tool-span-{len(starts)}"
        starts.append(sid)
        return sid

    def fake_end(span_id, **kwargs):
        ends.append(span_id)

    recorder = TraceRecorder("run-test", start_iteration=0)
    tc = ToolUseBlock(id="t1", name="search_bangumi", input={"title": "x"})

    # 模拟 chat 已发生（设置 chat_span_id）
    recorder._chat_span_id = "chat-span-0"

    with (
        patch("app.services.agent.observe.recorder.start_span", side_effect=fake_start),
        patch("app.services.agent.observe.recorder.end_span", side_effect=fake_end),
    ):
        span_id = recorder.start_tool(tc, sequence=0)
        assert span_id is not None
        assert len(starts) == 1

        # 第一次 end
        recorder.end_tool(
            span_id,
            result=ToolResultBlock(tool_use_id="t1", content="ok", is_error=False),
        )
        assert len(ends) == 1

        # 第二次 end（幂等）——不应崩溃
        recorder.end_tool(
            span_id,
            result=ToolResultBlock(tool_use_id="t1", content="ok", is_error=False),
        )
        assert len(ends) == 1, "幂等：第二次 end_tool 不应产生新 span 记录"


def test_trace_recorder_budget_writes_independent_row():
    """budget 钩子写独立 budget 行（一行一形态）；sequence = 本轮 max+1。"""
    from unittest.mock import patch

    starts: list[dict] = []
    ends: list[dict] = []

    def fake_start(run_id, name, iteration, sequence, parent_id=""):
        starts.append({"name": name, "iteration": iteration, "sequence": sequence})
        return f"span-{name}-{iteration}-{sequence}"

    def fake_end(span_id, **kwargs):
        ends.append({"span_id": span_id, **kwargs})

    recorder = TraceRecorder("run-test", start_iteration=0)

    with (
        patch("app.services.agent.observe.recorder.start_span", side_effect=fake_start),
        patch("app.services.agent.observe.recorder.end_span", side_effect=fake_end),
    ):
        # 同轮已有工具行：max sequence = 2
        recorder.start_tool(
            ToolUseBlock(id="t1", name="search_bangumi", input={}), sequence=2
        )
        recorder.record_budget("[剩余轮次：2]")

    budget_starts = [s for s in starts if s["name"] == "budget"]
    assert len(budget_starts) == 1, "预算应写独立 budget 行"
    assert budget_starts[0]["iteration"] == 0
    # 排序保证：预算行位于本轮全部工具行之后（max+1）
    assert budget_starts[0]["sequence"] == 3, budget_starts
    # delta 形态为 budget_message
    assert len(ends) == 1
    assert ends[0]["replay_delta"]["kind"] == "budget_message"
    assert ends[0]["replay_delta"]["budget_message"] == "[剩余轮次：2]"


# ---------------------------------------------------------------------------
# chat span 与 tool span 同轮 iteration 一致
# ---------------------------------------------------------------------------


def test_chat_span_and_tool_same_iteration():
    """同一轮内 chat span 与 tool span 的 iteration 必须一致；连续两轮时第二轮 iteration=1。"""
    from unittest.mock import patch

    starts = []
    ends = []

    def fake_start(run_id, name, iteration, sequence, parent_id=""):
        starts.append({"name": name, "iteration": iteration, "sequence": sequence})
        return f"span-{name}-{iteration}-{sequence}"

    def fake_end(span_id, **kwargs):
        ends.append({"span_id": span_id, **kwargs})

    recorder = TraceRecorder("run-test", start_iteration=0)

    # 脚本返回含 tool_calls 的响应 → 模拟工具执行后调用 start_tool
    async def dummy_script(messages, *, tools=None, tool_choice=None):
        return ChatResponse(
            content="",
            stop_reason="tool_use",
            blocks=[ToolUseBlock(id="t1", name="search_bangumi", input={"title": "x"})],
            model="test-model",
        )

    with (
        patch("app.services.agent.observe.recorder.start_span", side_effect=fake_start),
        patch("app.services.agent.observe.recorder.end_span", side_effect=fake_end),
    ):
        # 第一轮
        _drain_chat_span(recorder, _scripted_stream(dummy_script))
        # 模拟工具执行（与 chat 同轮）
        tc = ToolUseBlock(id="t1", name="search_bangumi", input={"title": "x"})
        recorder.start_tool(tc, sequence=0)

        # 第二轮
        _drain_chat_span(recorder, _scripted_stream(dummy_script))
        tc2 = ToolUseBlock(
            id="t2", name="get_subject_detail", input={"subject_id": "1"}
        )
        recorder.start_tool(tc2, sequence=0)

    # 提取 chat 和 tool 的 iteration
    chat_starts = [s for s in starts if s["name"] == "llm_chat"]
    tool_starts = [s for s in starts if s["name"] == "tool_execute"]

    assert len(chat_starts) == 2, f"应有 2 条 chat span，实际 {len(chat_starts)}"
    assert len(tool_starts) == 2, f"应有 2 条 tool span，实际 {len(tool_starts)}"

    # 第一轮：chat 与 tool 同 iteration
    assert chat_starts[0]["iteration"] == 0, (
        f"第一轮 chat iteration 应为 0，实际 {chat_starts[0]['iteration']}"
    )
    assert tool_starts[0]["iteration"] == 0, (
        f"第一轮 tool iteration 应与 chat 一致为 0，实际 {tool_starts[0]['iteration']}"
    )

    # 第二轮：chat 与 tool 同 iteration = 1
    assert chat_starts[1]["iteration"] == 1, (
        f"第二轮 chat iteration 应为 1，实际 {chat_starts[1]['iteration']}"
    )
    assert tool_starts[1]["iteration"] == 1, (
        f"第二轮 tool iteration 应与 chat 一致为 1，实际 {tool_starts[1]['iteration']}"
    )


# ---------------------------------------------------------------------------
# chat span 异常不得被 UnboundLocalError 遮蔽
# ---------------------------------------------------------------------------


def test_chat_span_exception_propagates_original():
    """脚本抛 ValueError('boom') → 捕获的必须是 ValueError('boom')，不是 UnboundLocalError。"""
    from unittest.mock import patch

    ends = []

    def fake_start(run_id, name, iteration, sequence, parent_id=""):
        return f"span-{name}-{iteration}-{sequence}"

    def fake_end(span_id, **kwargs):
        ends.append({"span_id": span_id, **kwargs})

    recorder = TraceRecorder("run-test", start_iteration=0)

    async def exploding_script(messages, *, tools=None, tool_choice=None):
        raise ValueError("boom")

    with (
        patch("app.services.agent.observe.recorder.start_span", side_effect=fake_start),
        patch("app.services.agent.observe.recorder.end_span", side_effect=fake_end),
    ):
        with pytest.raises(ValueError, match="boom"):
            _drain_chat_span(recorder, _scripted_stream(exploding_script))

    # 不应写 ok span（允许写 error span，但 status 不得为 "ok"）
    ok_ends = [e for e in ends if e.get("status") == "ok"]
    assert len(ok_ends) == 0, f"异常时不应写 ok span，实际写了 {len(ok_ends)} 条"


# ---------------------------------------------------------------------------
# _build_default_stream_fn 必填 thinking_level
# ---------------------------------------------------------------------------


def test_build_default_stream_fn_requires_thinking_level():
    """_build_default_stream_fn 不传 thinking_level 应抛 TypeError。"""
    fn = cast("Any", llm_assist._build_default_stream_fn)
    with pytest.raises(TypeError):
        fn()


# ---------------------------------------------------------------------------
# _persist_llm_candidate ended_at 必须为 epoch 整数（与 mark_succeeded 一致）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TraceRecorder 增加必填 start_iteration 构造参数
# ---------------------------------------------------------------------------


def test_trace_recorder_requires_start_iteration():
    """TraceRecorder 构造函数要求必填 start_iteration（不得有默认值）。"""
    import inspect

    sig = inspect.signature(TraceRecorder.__init__)
    param = sig.parameters.get("start_iteration")
    assert param is not None, "TraceRecorder.__init__ 应有 start_iteration 参数"
    assert param.default is inspect.Parameter.empty, (
        "start_iteration 不得有默认值（必须显式传入）"
    )


def test_trace_recorder_start_iteration_affects_first_chat_span_iteration():
    """start_iteration=N → 首轮 chat span iteration=N。"""
    from unittest.mock import patch

    starts = []

    def fake_start(run_id, name, iteration, sequence, parent_id=""):
        starts.append({"name": name, "iteration": iteration, "sequence": sequence})
        return f"span-{name}-{iteration}-{sequence}"

    recorder = TraceRecorder("run-test", start_iteration=5)

    async def dummy_script(messages, *, tools=None, tool_choice=None):
        return ChatResponse(content="", stop_reason="end_turn", blocks=[], model="m")

    with patch(
        "app.services.agent.observe.recorder.start_span", side_effect=fake_start
    ):
        _drain_chat_span(recorder, _scripted_stream(dummy_script))

    chat_starts = [s for s in starts if s["name"] == "llm_chat"]
    assert len(chat_starts) == 1
    assert chat_starts[0]["iteration"] == 5, (
        f"首轮 chat iteration 应等于 start_iteration=5，实际 {chat_starts[0]['iteration']}"
    )


# ---------------------------------------------------------------------------
# TraceRecorder.begin_replayed_round 锚定到指定轮次
# ---------------------------------------------------------------------------


def test_trace_recorder_begin_replayed_round_sets_current_iteration():
    """begin_replayed_round(N) 后 start_tool 的 iteration=N。"""
    from unittest.mock import patch

    from app.services.llm.models import ToolUseBlock

    starts = []

    def fake_start(run_id, name, iteration, sequence, parent_id=""):
        starts.append({"name": name, "iteration": iteration, "sequence": sequence})
        return f"span-{name}-{iteration}-{sequence}"

    recorder = TraceRecorder("run-test", start_iteration=3)
    recorder.begin_replayed_round(7)

    tc = ToolUseBlock(id="t1", name="search_bangumi", input={"title": "x"})
    with patch(
        "app.services.agent.observe.recorder.start_span", side_effect=fake_start
    ):
        recorder.start_tool(tc, sequence=0)

    tool_starts = [s for s in starts if s["name"] == "tool_execute"]
    assert len(tool_starts) == 1
    assert tool_starts[0]["iteration"] == 7, (
        f"begin_replayed_round(7) 后 tool iteration 应为 7，实际 {tool_starts[0]['iteration']}"
    )


# ---------------------------------------------------------------------------
# LLMCallError 处理（run 捕获分流 + _handle_result 新增分支）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_llm_call_error_retryable_true_increments_attempts(
    monkeypatch,
):
    """LLMCallError(retryable=True) → increment_attempts，返回 processing（attempts<3）。"""
    from app.services.llm.client import LLMCallError

    run_id = "run-e-retryable"
    sr_id = 500
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(sync_record_id=sr_id)

    async def _boom(messages, *, tools=None, tool_choice=None):
        raise LLMCallError("429 rate limited", retryable=True)

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _make_bgm()),
        thinking_level="medium",
        stream_fn=_scripted_stream(_boom),
        span_recorder=None,
    )

    assert status == "processing"
    run_row = _run_row(run_id)
    assert run_row["attempts"] == 1


@pytest.mark.asyncio
async def test_run_llm_call_error_retryable_false_immediately_failed(monkeypatch):
    """LLMCallError(retryable=False) → 立即 mark_failed(stop_reason='llm_error')，返回 failed。"""
    from app.services.llm.client import LLMCallError

    run_id = "run-e-terminal"
    sr_id = 501
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(sync_record_id=sr_id)

    async def _boom(messages, *, tools=None, tool_choice=None):
        raise LLMCallError("401 Unauthorized", retryable=False)

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _make_bgm()),
        thinking_level="medium",
        stream_fn=_scripted_stream(_boom),
        span_recorder=None,
    )

    assert status == "failed"
    run_row = _run_row(run_id)
    assert run_row["status"] == "failed"
    assert run_row["stop_reason"] == "llm_error"
    assert "401" in (run_row["last_error"] or "")


@pytest.mark.asyncio
async def test_run_llm_call_error_retryable_true_three_times_failed(monkeypatch):
    """LLMCallError(retryable=True) 连续 3 次 → increment_attempts 单点置终态 failed。"""
    from app.services.llm.client import LLMCallError

    run_id = "run-e-retry3"
    sr_id = 502
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(sync_record_id=sr_id)

    call_count = {"n": 0}

    async def _boom(messages, *, tools=None, tool_choice=None):
        call_count["n"] += 1
        raise LLMCallError("500 Internal Server Error", retryable=True)

    # 第 1 次 → processing (attempts=1)
    status1 = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _make_bgm()),
        thinking_level="medium",
        stream_fn=_scripted_stream(_boom),
        span_recorder=None,
    )
    assert status1 == "processing"

    # 重新 claim 并跑第 2 次 → processing (attempts=2)
    database_manager.agent_runs.update_run_status(run_id, "pending")
    status2 = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _make_bgm()),
        thinking_level="medium",
        stream_fn=_scripted_stream(_boom),
        span_recorder=None,
    )
    assert status2 == "processing"

    # 第 3 次 → failed (attempts=3)
    database_manager.agent_runs.update_run_status(run_id, "pending")
    status3 = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _make_bgm()),
        thinking_level="medium",
        stream_fn=_scripted_stream(_boom),
        span_recorder=None,
    )
    assert status3 == "failed"
    run_row = _run_row(run_id)
    assert run_row["status"] == "failed"
    # 终态由 increment_attempts 单点事务写入（stop_reason='failed'），外层不再二次 mark_failed
    assert run_row["stop_reason"] == "failed"


@pytest.mark.asyncio
async def test_handle_result_llm_error_marks_failed(monkeypatch):
    """_handle_result 对 stop_reason='llm_error' → mark_failed(stop_reason='llm_error')。"""
    from unittest.mock import MagicMock

    from app.services.agent.loop import RunResult

    dbm = MagicMock()
    result = RunResult(stop_reason="llm_error")

    status = llm_assist._handle_result(
        dbm,
        "run-llm-err",
        result,
        sync_record=_make_sync_record(sync_record_id=510),
        sync_record_id=510,
        bgm=None,
        notification_service=None,
        total_tokens=0,
    )

    assert status == "failed"
    dbm.agent_runs.mark_failed.assert_called_once()
    call_kwargs = dbm.agent_runs.mark_failed.call_args[1]
    assert call_kwargs.get("stop_reason") == "llm_error"


@pytest.mark.asyncio
async def test_handle_result_max_tokens_marks_failed(monkeypatch):
    """_handle_result 对 stop_reason='max_tokens' → mark_failed(stop_reason='max_tokens')。"""
    from unittest.mock import MagicMock

    from app.services.agent.loop import RunResult

    dbm = MagicMock()
    result = RunResult(stop_reason="max_tokens")

    status = llm_assist._handle_result(
        dbm,
        "run-max-tok",
        result,
        sync_record=_make_sync_record(sync_record_id=511),
        sync_record_id=511,
        bgm=None,
        notification_service=None,
        total_tokens=0,
    )

    assert status == "failed"
    dbm.agent_runs.mark_failed.assert_called_once()
    call_kwargs = dbm.agent_runs.mark_failed.call_args[1]
    assert call_kwargs.get("stop_reason") == "max_tokens"


@pytest.mark.asyncio
async def test_handle_result_end_turn_still_succeeded(monkeypatch):
    """_handle_result 对 stop_reason='end_turn' 仍落 succeeded（语义不变）。"""
    from unittest.mock import MagicMock

    from app.services.agent.loop import RunResult

    dbm = MagicMock()
    result = RunResult(stop_reason="end_turn")

    status = llm_assist._handle_result(
        dbm,
        "run-end-turn",
        result,
        sync_record=_make_sync_record(sync_record_id=512),
        sync_record_id=512,
        bgm=None,
        notification_service=None,
        total_tokens=321,
    )

    assert status == "succeeded"
    dbm.agent_runs.mark_succeeded.assert_called_once_with(
        "run-end-turn", stop_reason="end_turn", total_tokens=321
    )


@pytest.mark.asyncio
async def test_handle_result_invalid_subject_id_passes_total_tokens(monkeypatch):
    """submit 校验失败落 succeeded 时透传全轮累计 total_tokens。"""
    from unittest.mock import MagicMock

    from app.services.agent.loop import RunResult

    dbm = MagicMock()
    result = RunResult(
        stop_reason="submit_suggestion",
        terminal_args={"subject_id": "999999", "reason": "非法"},
    )
    monkeypatch.setattr(
        llm_assist, "_validate_subject_id", lambda sid: (False, "subject_id 非法")
    )

    status = llm_assist._handle_result(
        dbm,
        "run-invalid-sid",
        result,
        sync_record=_make_sync_record(sync_record_id=513),
        sync_record_id=513,
        bgm=None,
        notification_service=None,
        total_tokens=88,
    )

    assert status == "succeeded"
    dbm.agent_runs.mark_succeeded.assert_called_once_with(
        "run-invalid-sid",
        stop_reason="submit_suggestion",
        last_error="subject_id 非法",
        total_tokens=88,
    )


@pytest.mark.asyncio
async def test_handle_result_exhausted_passes_total_tokens(monkeypatch):
    """exhausted 且兜底解析无建议时落 succeeded 并透传 total_tokens。"""
    from unittest.mock import MagicMock

    from app.services.agent.loop import RunResult

    dbm = MagicMock()
    result = RunResult(stop_reason="exhausted", last_response=MagicMock())
    monkeypatch.setattr(
        llm_assist, "parse_suggestion", lambda content: (None, "无建议")
    )

    status = llm_assist._handle_result(
        dbm,
        "run-exhausted",
        result,
        sync_record=_make_sync_record(sync_record_id=514),
        sync_record_id=514,
        bgm=None,
        notification_service=None,
        total_tokens=99,
    )

    assert status == "succeeded"
    dbm.agent_runs.mark_succeeded.assert_called_once_with(
        "run-exhausted",
        stop_reason="exhausted",
        last_error="无建议",
        total_tokens=99,
    )


def test_begin_replayed_round_sets_next_iteration_to_iteration_plus_one():
    """begin_replayed_round(iteration) 应设 _current_iteration=iteration 且 _next_iteration=iteration+1。"""
    recorder = TraceRecorder("run-test", start_iteration=0)
    recorder.begin_replayed_round(5)
    assert recorder._current_iteration == 5
    assert recorder._next_iteration == 6


# ---------------------------------------------------------------------------
# _persist_llm_candidate 写入 agent_runs.ended_at 为 epoch 整数
# ---------------------------------------------------------------------------


def test_persist_llm_candidate_ended_at_is_epoch_integer(monkeypatch):
    """_persist_llm_candidate 写入 agent_runs.ended_at 为 epoch 整数。"""
    import time

    run_id = "run-ended-at"
    sr_id = 300
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    database_manager.log_pending_candidate(
        request_title=f"rule-{sr_id}",
        request_season=1,
        user_name="alice",
        source="plex",
        candidates=[{"subject_id": "111", "name": "A", "score": 0.8}],
        sync_record_id=sr_id,
    )
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    before = int(time.time())
    llm_assist._persist_llm_candidate(
        database_manager,
        run_id=run_id,
        sync_record_id=sr_id,
        sync_record=sr,
        business_key=build_match_business_key(
            sr["user_name"], sr["title"], sr["season"]
        ),
        subject_id="123",
        reason="r",
        stop_reason="submit_suggestion",
    )
    after = int(time.time())

    run_row = _run_row(run_id)
    ended_at = run_row["ended_at"]
    # 断言 ended_at 为 int 且 > 0
    assert isinstance(ended_at, int), f"ended_at 应为 int，实际 {type(ended_at)}"
    assert ended_at > 0, "ended_at 应 > 0"
    assert before <= ended_at <= after + 1, (
        f"ended_at 应在 [{before}, {after}+1] 范围内，实际 {ended_at}"
    )

    # 断言 _iso_from_epoch 返回非 None（可解析为 ISO 字符串）
    from app.api.agent_runs import _iso_from_epoch

    iso = _iso_from_epoch(ended_at)
    assert iso is not None, "_iso_from_epoch(ended_at) 应返回非 None"

    # 断言 SQLite typeof(ended_at)='integer'
    conn = _db_conn()
    cur = conn.execute(
        "SELECT typeof(ended_at) FROM agent_runs WHERE run_id=?", (run_id,)
    )
    row = cur.fetchone()
    assert row[0] == "integer", f"SQLite typeof(ended_at) 应为 'integer'，实际 {row[0]}"


# ---------------------------------------------------------------------------
# 恢复续跑单一入口 continue_run
# （由调度器 _recover_run 调用：replay → 补执行 → 续跑 → 落库）
# ---------------------------------------------------------------------------


def _make_continuation_repo() -> MagicMock:
    """恢复续跑测试用 agent_runs repo mock（increment_attempts 默认未达上限）。"""
    repo = MagicMock()
    repo.increment_attempts.return_value = 1
    return repo


def _make_continuation_dbm(repo: MagicMock) -> MagicMock:
    dbm = MagicMock()
    dbm.agent_runs = repo
    return dbm


def _make_replay_result(
    *,
    executed: int = 0,
    missing: list | None = None,
    last_response: dict | ReplayResponse | None = None,
    messages: list | None = None,
    total_tokens: int = 0,
) -> ReplayResult:
    # 测试便捷构造：dict → 类型化模型（与生产 replay 解析后的类型一致）
    resp = (
        ReplayResponse.model_validate(last_response)
        if isinstance(last_response, dict)
        else last_response
    )
    return ReplayResult(
        messages=messages or [Message(role="system", content="s")],
        executed_iterations=executed,
        missing_tool_calls=[
            ReplayToolCall.model_validate(tc) for tc in (missing or [])
        ],
        last_response=resp,
        total_tokens=total_tokens,
    )


def _medium_cfg(raw_max: str = "") -> dict:
    return {
        "llm_match_thinking_level": "medium",
        "llm_match_max_iterations": raw_max,
    }


# end_turn 分派 ---------------------------------------------------------


def test_continue_run_end_turn_marks_succeeded_without_llm():
    """last_response=end_turn → 直接 mark_succeeded，不调 LLM（run_loop）。"""
    repo = _make_continuation_repo()
    rr = _make_replay_result(
        executed=1,
        last_response={"stop_reason": "end_turn", "content": "x", "tool_calls": []},
        total_tokens=222,
    )
    loop = AsyncMock()
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime.run_loop", loop),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    repo.mark_succeeded.assert_called_once_with(
        "r", stop_reason="end_turn", total_tokens=222
    )
    loop.assert_not_awaited()


# submit 分派 -----------------------------------------------------------


def test_continue_run_submit_suggestion_dispatches_to_handle_result():
    """last_response=submit_suggestion → 捕获参数走校验落库路径（_handle_result）。"""
    repo = _make_continuation_repo()
    handle = MagicMock()
    fake_svc = MagicMock()
    rr = _make_replay_result(
        executed=1,
        last_response={
            "stop_reason": "submit_suggestion",
            "content": "",
            "tool_calls": [
                {
                    "id": "s1",
                    "name": "submit_suggestion",
                    "input": {"subject_id": "123", "reason": "跨季匹配"},
                }
            ],
        },
    )
    loop = AsyncMock()
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime.run_loop", loop),
        patch("app.services.matching.llm_assist._handle_result", handle),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r",
                ctx=_ctx({"id": 1}, MagicMock()),
                notification_service=fake_svc,
            )
        )

    handle.assert_called_once()
    result_arg = handle.call_args[0][2]  # _handle_result(dbm, run_id, result, ...)
    assert result_arg.stop_reason == "submit_suggestion"
    assert result_arg.terminal_args == {"subject_id": "123", "reason": "跨季匹配"}
    # 接线断言：notification_service 必须透传（生产链路真正发送站内信）
    assert handle.call_args[1].get("notification_service") is fake_svc
    loop.assert_not_awaited()


def test_continue_run_submit_uses_replayed_total_tokens():
    """submit_suggestion 直接分派分支把 replay 累计 tokens 传给 _handle_result。

    恢复路径无实时 recorder，历史轮次 tokens 由 ``ReplayResult.total_tokens`` 携带；
    不得再硬编码 0。
    """
    repo = _make_continuation_repo()
    handle = MagicMock()
    rr = _make_replay_result(
        executed=1,
        last_response={
            "stop_reason": "submit_suggestion",
            "content": "",
            "tool_calls": [
                {
                    "id": "s1",
                    "name": "submit_suggestion",
                    "input": {"subject_id": "123", "reason": "跨季匹配"},
                }
            ],
        },
        total_tokens=150,
    )
    loop = AsyncMock()
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime.run_loop", loop),
        patch("app.services.matching.llm_assist._handle_result", handle),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r-tok", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    handle.assert_called_once()
    assert handle.call_args[1].get("total_tokens") == 150, (
        f"恢复 submit 分支应传 replay 累计 tokens=150，"
        f"实际 {handle.call_args[1].get('total_tokens')}"
    )
    loop.assert_not_awaited()


# 补执行缺失工具 ---------------------------------------------------------


def test_continue_run_tool_use_backfills_and_continues_loop():
    """last_response 含 tool_use（缺失工具）→ 补执行缺失工具 + 回填后继续 run_loop。"""
    repo = _make_continuation_repo()
    missing = {"id": "t1", "name": "search_bangumi", "input": {"title": "foo"}}
    rr = _make_replay_result(
        executed=0,
        missing=[missing],
        last_response={
            "stop_reason": "tool_use",
            "content": "go",
            "tool_calls": [missing],
        },
    )
    backfill = AsyncMock()
    loop = AsyncMock(return_value=RunResult(stop_reason="end_turn"))
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime.run_loop", loop),
        patch("app.services.agent.run.runtime._replay_missing_tool", backfill),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    # 缺失工具补执行一次，并锚定同轮 span（sequence=0）
    backfill.assert_awaited_once()
    backfill_call = backfill.await_args
    assert backfill_call is not None
    assert backfill_call.args[0] == ReplayToolCall.model_validate(missing)
    assert backfill_call.kwargs.get("sequence") == 0
    assert backfill_call.kwargs.get("span_recorder") is not None
    # 回填后进入下一轮 run_loop 续跑（1 次 LLM 调用）
    loop.assert_awaited_once()


# last_response=None 完整续跑 -------------------------------------------


def test_continue_run_last_response_none_runs_loop_and_lands_result():
    """last_response=None → loop 从 executed_iterations 续跑并落 RunResult。"""
    repo = _make_continuation_repo()
    handle = MagicMock()
    seed = [Message(role="system", content="s"), Message(role="user", content="u")]
    rr = _make_replay_result(executed=4, missing=[], last_response=None, messages=seed)
    loop = AsyncMock(return_value=RunResult(stop_reason="end_turn"))
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime.run_loop", loop),
        patch("app.services.matching.llm_assist._handle_result", handle),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    loop.assert_awaited_once()
    loop_call = loop.await_args
    assert loop_call is not None
    assert loop_call.kwargs["seed_messages"] is seed
    # medium=5，executed=4 → 剩余 1 轮
    assert loop_call.kwargs["max_iterations"] == 1
    handle.assert_called_once()
    handle_call = handle.call_args
    assert handle_call is not None
    assert handle_call[0][2].stop_reason == "end_turn"


def test_continue_run_continuation_sums_replay_and_new_round_tokens():
    """通用续跑分支 total_tokens = replay 历史累计 + 本次新轮累计。

    replay 历史 150 + 本次新产生 50 → ``_handle_result`` 必须收到 200，
    避免恢复续跑路径只记本次新轮、丢失历史 tokens。
    """
    repo = _make_continuation_repo()
    handle = MagicMock()
    rr = _make_replay_result(
        executed=1, missing=[], last_response=None, total_tokens=150
    )
    recorder = MagicMock()
    recorder.total_tokens = 50
    trace_recorder_cls = MagicMock(return_value=recorder)
    loop = AsyncMock(return_value=RunResult(stop_reason="end_turn"))
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime.run_loop", loop),
        patch("app.services.agent.run.runtime.TraceRecorder", trace_recorder_cls),
        patch("app.services.matching.llm_assist._handle_result", handle),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    loop.assert_awaited_once()
    handle.assert_called_once()
    assert handle.call_args[1].get("total_tokens") == 200, (
        "续跑分支应叠加 replay 历史 tokens(150) 与本次新轮 tokens(50)，"
        f"实际 {handle.call_args[1].get('total_tokens')}"
    )


# config_override 非正数回退 None ---------------------------------------


def _run_continue_run_with_config(raw_max: str):
    """以指定 llm_match_max_iterations 跑一次 continue_run，返回捕获的 config_override。"""
    repo = _make_continuation_repo()
    captured: dict = {}

    def _gmi(task_type, thinking_level, config_override=None):
        captured["config_override"] = config_override
        return 3

    log = MagicMock()
    rr = _make_replay_result(
        executed=0,
        last_response={"stop_reason": "end_turn", "content": "x", "tool_calls": []},
    )
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.get_max_iterations", _gmi),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.matching.llm_assist.logger", log),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg(raw_max)
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r", ctx=_ctx({"id": 1}, MagicMock())
            )
        )
    return captured.get("config_override"), log


def test_continue_run_zero_config_override_falls_back_to_none():
    override, log = _run_continue_run_with_config("0")
    assert override is None, "0 应回退 None（避免 max_iterations=0 空跑）"
    assert log.warning.called


def test_continue_run_negative_config_override_falls_back_to_none():
    override, log = _run_continue_run_with_config("-3")
    assert override is None, "负值应回退 None"
    assert log.warning.called


def test_continue_run_positive_config_override_is_passed_through():
    override, _ = _run_continue_run_with_config("7")
    assert override == 7


# 预算耗尽必须落终态 -----------------------------------------------------


def test_continue_run_tool_use_no_remaining_marks_succeeded():
    """tool_use 补执行后 remaining<=0 → 落 succeeded(exhausted)，不续跑。"""
    repo = _make_continuation_repo()
    missing = {"id": "t1", "name": "search_bangumi", "input": {"title": "foo"}}
    rr = _make_replay_result(
        executed=4,  # medium=5 → remaining=1；补执行后 -1 → 0
        missing=[missing],
        last_response={
            "stop_reason": "tool_use",
            "content": "go",
            "tool_calls": [missing],
        },
        total_tokens=111,
    )
    loop = AsyncMock()
    backfill = AsyncMock()
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime._replay_missing_tool", backfill),
        patch("app.services.agent.run.runtime.run_loop", loop),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    # 不再续跑 loop，但必须落终态（否则 run 永久 processing）
    loop.assert_not_awaited()
    backfill.assert_not_awaited()
    repo.mark_succeeded.assert_called_once_with(
        "r", stop_reason="exhausted", total_tokens=111
    )


def test_continue_run_no_remaining_before_replay_marks_succeeded():
    """replay 后剩余轮次已耗尽（remaining<=0）同样必须落终态。"""
    repo = _make_continuation_repo()
    rr = _make_replay_result(
        executed=5,  # medium=5 → remaining=0
        missing=[],
        last_response=None,
        total_tokens=333,
    )
    loop = AsyncMock()
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime.run_loop", loop),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    loop.assert_not_awaited()
    repo.mark_succeeded.assert_called_once_with(
        "r", stop_reason="exhausted", total_tokens=333
    )


def test_continue_run_replay_exhausted_logs_warning():
    """预算耗尽（replay 前）→ warning 且注明恢复(replay)路径，不得停留在 debug。"""
    repo = _make_continuation_repo()
    rr = _make_replay_result(
        executed=5, missing=[], last_response=None, total_tokens=444
    )
    log = MagicMock()
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime.logger", log),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    repo.mark_succeeded.assert_called_once_with(
        "r", stop_reason="exhausted", total_tokens=444
    )
    warnings = [str(c.args[0]) for c in log.warning.call_args_list]
    assert any("恢复" in m or "replay" in m.lower() for m in warnings), (
        f"预算耗尽应 warning 且注明恢复(replay)路径，实际 {warnings}"
    )
    debugs = [str(c.args[0]) for c in log.debug.call_args_list]
    assert not any("已无剩余轮次" in m for m in debugs), (
        "预算耗尽日志不应停留在 debug 级"
    )


# 终局优先于预算耗尽判定（恢复路径顺序调整） ------------------------------


def test_continue_run_zero_remaining_with_submit_uses_terminal():
    """remaining==0 + last_response 含 submit → 走 handle_terminal，不落 exhausted。"""
    repo = _make_continuation_repo()
    handle = MagicMock()
    rr = _make_replay_result(
        executed=5,  # medium=5 → remaining=0
        missing=[],
        last_response={
            "stop_reason": "tool_use",
            "content": "",
            "tool_calls": [
                {
                    "id": "s1",
                    "name": "submit_suggestion",
                    "input": {"subject_id": "123", "reason": "末轮已提交"},
                }
            ],
        },
    )
    loop = AsyncMock()
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime.run_loop", loop),
        patch("app.services.matching.llm_assist._handle_result", handle),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    handle.assert_called_once()
    result_arg = handle.call_args[0][2]
    assert result_arg.stop_reason == "submit_suggestion"
    assert result_arg.terminal_args == {"subject_id": "123", "reason": "末轮已提交"}
    repo.mark_succeeded.assert_not_called()
    loop.assert_not_awaited()


def test_continue_run_zero_remaining_recovery_end_turn_lands_exhausted():
    """remaining==0 的文本响应属收尾轮 → 按 live 落 exhausted 并交场景兜底解析。

    收尾轮（预算耗尽后 ``loop._final_recovery``）只做二分：含终止工具调用 →
    submit_suggestion；否则 exhausted（响应文本交 ``parse_suggestion`` 兜底）。
    恢复路径若按 provider 原始 stop_reason 直落 end_turn，兜底解析会被跳过、
    终态语义与 live 分叉（live 侧判据见 loop._final_recovery 及其用例）。
    """
    repo = _make_continuation_repo()
    rr = _make_replay_result(
        executed=5,  # medium=5 → remaining=0
        missing=[],
        last_response={"stop_reason": "end_turn", "content": "放弃", "tool_calls": []},
        total_tokens=55,
    )
    loop = AsyncMock()
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch(
            "app.services.matching.llm_assist.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime.run_loop", loop),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    # 走场景终态处理：exhausted + 文本兜底解析（"放弃" 非 JSON → 记 last_error）
    repo.mark_succeeded.assert_called_once_with(
        "r",
        stop_reason="exhausted",
        last_error="JSON 提取失败",
        total_tokens=55,
    )
    loop.assert_not_awaited()


def test_continue_run_replay_exhausted_after_backfill_logs_warning():
    """补执行后预算耗尽 → warning 且注明恢复(replay)路径。"""
    repo = _make_continuation_repo()
    missing = {"id": "t1", "name": "search_bangumi", "input": {"title": "foo"}}
    rr = _make_replay_result(
        executed=4,
        missing=[missing],
        last_response={
            "stop_reason": "tool_use",
            "content": "go",
            "tool_calls": [missing],
        },
        total_tokens=66,
    )
    log = MagicMock()
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime.logger", log),
        patch("app.services.agent.run.runtime._replay_missing_tool", new=AsyncMock()),
        patch("app.services.agent.run.runtime.run_loop", new=AsyncMock()),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r2", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    repo.mark_succeeded.assert_called_once_with(
        "r2", stop_reason="exhausted", total_tokens=66
    )
    warnings = [str(c.args[0]) for c in log.warning.call_args_list]
    assert any("恢复" in m or "replay" in m.lower() for m in warnings), (
        f"补执行后预算耗尽应 warning 且注明恢复(replay)路径，实际 {warnings}"
    )
    debugs = [str(c.args[0]) for c in log.debug.call_args_list]
    assert not any("已无剩余轮次" in m for m in debugs), (
        "补执行后预算耗尽日志不应停留在 debug 级"
    )


# LLM 调用失败按可重试性分流 --------------------------------------------


def test_continue_run_llm_call_error_retryable_false_marks_failed():
    """LLMCallError(retryable=False) → mark_failed(stop_reason='llm_error')。"""
    from app.services.llm.client import LLMCallError

    repo = _make_continuation_repo()
    rr = _make_replay_result(executed=0, missing=[], last_response=None)

    async def _boom(messages, *, tools=None, tool_choice=None):
        raise LLMCallError("401 Unauthorized", retryable=False)
        yield  # pragma: no cover - 使其成为 async generator，供流式消费

    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch(
            "app.services.matching.llm_assist._build_default_stream_fn",
            return_value=_boom,
        ),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r-err-terminal", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    repo.mark_failed.assert_called_once()
    call_kwargs = repo.mark_failed.call_args[1]
    assert call_kwargs.get("stop_reason") == "llm_error"
    assert "401" in call_kwargs.get("last_error", "")
    repo.increment_attempts.assert_not_called()


def test_continue_run_llm_call_error_retryable_true_increments_attempts():
    """LLMCallError(retryable=True) → increment_attempts 并携带 last_error。"""
    from app.services.llm.client import LLMCallError

    repo = _make_continuation_repo()
    rr = _make_replay_result(executed=0, missing=[], last_response=None)

    async def _boom(messages, *, tools=None, tool_choice=None):
        raise LLMCallError("500 Internal Server Error", retryable=True)
        yield  # pragma: no cover - 使其成为 async generator，供流式消费

    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch(
            "app.services.matching.llm_assist._build_default_stream_fn",
            return_value=_boom,
        ),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r-err-retry", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    repo.increment_attempts.assert_called_once_with(
        "r-err-retry", last_error="500 Internal Server Error"
    )
    repo.mark_failed.assert_not_called()


def test_continue_run_outer_exception_logs_current_run_status():
    """最外层 except 日志须带当前 run 状态，便于定位「已终态后的异常」。"""
    repo = _make_continuation_repo()
    repo.get_run.return_value = {"status": "processing"}
    rr = _make_replay_result(executed=0, missing=[], last_response=None)
    log = MagicMock()

    async def _stream_fn(messages, *, tools=None, tool_choice=None):
        yield StreamChunk(type="stop", stop_reason="end_turn")

    boom_loop = AsyncMock(side_effect=RuntimeError("kaboom"))
    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch(
            "app.services.matching.llm_assist._build_default_stream_fn",
            return_value=_stream_fn,
        ),
        patch("app.services.agent.run.runtime.run_loop", boom_loop),
        patch("app.services.agent.run.runtime.logger", log),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r-outer-err", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    errors = [str(c.args[0]) for c in log.error.call_args_list]
    assert any("processing" in m for m in errors), (
        f"最外层异常日志应包含当前 run 状态(processing)，实际 {errors}"
    )
    repo.increment_attempts.assert_called_once()


# 缺失工具补执行（readonly 执行 / 非 read 占位闭合协议） -------------


def test_replay_missing_tool_appends_tool_result_to_messages():
    """缺失的 read 工具补执行 → 结果回填 messages（assistant 后有 tool_result）。"""

    def _handler(args):
        return "SEARCH-RESULT"

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="get_subject_detail",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=_handler,
            access="read",
        )
    )

    messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="ctx"),
        Message(
            role="assistant",
            content=[
                ToolUseBlock(
                    id="t2", name="get_subject_detail", input={"subject_id": "2"}
                )
            ],
        ),
        Message(
            role="user",
            content=[ToolResultBlock(tool_use_id="t1", content="r1", is_error=False)],
        ),
    ]

    def _count_tool_results(msgs):
        return sum(
            1
            for m in msgs
            if m.role == "user"
            and isinstance(m.content, list)
            and any(isinstance(b, ToolResultBlock) for b in m.content)
        )

    before = _count_tool_results(messages)
    asyncio.run(
        runtime._replay_missing_tool(
            ReplayToolCall(
                id="t2", name="get_subject_detail", input={"subject_id": "2"}
            ),
            registry,
            messages,
        )
    )

    after = _count_tool_results(messages)
    assert after == before + 1 == 2  # t1（已记录）+ t2（补执行）
    trs = [
        cast("ToolResultBlock", m.content[0])
        for m in messages
        if m.role == "user"
        and isinstance(m.content, list)
        and any(isinstance(b, ToolResultBlock) for b in m.content)
    ]
    assert any(t.tool_use_id == "t2" and t.content == '"SEARCH-RESULT"' for t in trs), (
        "补执行的 tool_result 应对应缺失的 tool_use t2，且按 JSON 序列化（字符串带引号）"
    )


_SKIP_PLACEHOLDER = "skipped: will be re-invoked in continuation"


def _last_tool_result(messages: list) -> ToolResultBlock | None:
    for m in reversed(messages):
        if m.role == "user" and isinstance(m.content, list) and m.content:
            blk = m.content[0]
            if isinstance(blk, ToolResultBlock):
                return blk
    return None


def _registry_with(
    name: str, access: Literal["read", "write", "terminal"], called: list
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name=name,
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=lambda args: called.append(name) or "X",
            access=access,
        )
    )
    return registry


def test_replay_missing_write_tool_appends_placeholder_tool_result(log_records):
    """write 缺失工具不重放副作用，但回填占位 tool_result 闭合协议；记 info。"""
    called: list = []
    registry = _registry_with("write_mapping", "write", called)
    messages = [
        Message(
            role="assistant",
            content=[ToolUseBlock(id="w1", name="write_mapping", input={})],
        )
    ]

    asyncio.run(
        runtime._replay_missing_tool(
            ReplayToolCall(id="w1", name="write_mapping", input={}),
            registry,
            messages,
        )
    )

    assert called == []
    blk = _last_tool_result(messages)
    assert blk is not None, "write 缺失工具也必须回填 tool_result 闭合协议"
    assert blk.tool_use_id == "w1"
    assert blk.is_error is False
    assert blk.content == _SKIP_PLACEHOLDER
    # 分支流转：不重放副作用这一关键决策必须默认可见（≥ info，不能停在 debug）
    infos = [line for level, line in log_records if level == "INFO"]
    assert any("非只读/未注册" in line for line in infos), (
        f"占位回填（不重放副作用）应记 info，实际 {infos}"
    )


def test_replay_missing_terminal_tool_appends_placeholder_tool_result():
    """terminal 缺失工具不重放，回填占位 tool_result。"""
    called: list = []
    registry = _registry_with("submit_suggestion", "terminal", called)
    messages: list = []

    asyncio.run(
        runtime._replay_missing_tool(
            ReplayToolCall(
                id="s1", name="submit_suggestion", input={"subject_id": "1"}
            ),
            registry,
            messages,
        )
    )

    assert called == []
    blk = _last_tool_result(messages)
    assert blk is not None
    assert blk.tool_use_id == "s1"
    assert blk.content == _SKIP_PLACEHOLDER
    assert blk.is_error is False


def test_replay_missing_unregistered_tool_appends_placeholder_tool_result():
    """未注册工具同样回填占位，避免 tool_use 悬空。"""
    registry = ToolRegistry()
    messages: list = []

    asyncio.run(
        runtime._replay_missing_tool(
            ReplayToolCall(id="u1", name="ghost_tool", input={}),
            registry,
            messages,
        )
    )

    blk = _last_tool_result(messages)
    assert blk is not None, "未注册工具同样需回填占位，避免 tool_use 悬空"
    assert blk.tool_use_id == "u1"
    assert blk.content == _SKIP_PLACEHOLDER


def test_replay_missing_tool_serializes_dict_result_as_json():
    """dict 工具结果经补执行后按 JSON 字符串回填（与 execute_batch 契约一致）。"""

    def _handler(args):
        return {"id": 2, "name": "花咲くいろは"}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="get_subject_detail",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=_handler,
            access="read",
        )
    )
    messages: list = []

    asyncio.run(
        runtime._replay_missing_tool(
            ReplayToolCall(id="t9", name="get_subject_detail", input={}),
            registry,
            messages,
        )
    )

    blk = _last_tool_result(messages)
    assert blk is not None
    assert blk.tool_use_id == "t9"
    # 新契约：JSON 字符串（ensure_ascii=False），而非 Python repr/str
    assert json.loads(blk.content) == {"id": 2, "name": "花咲くいろは"}


# 补执行门控以 access == "read"（无副作用语义）为准，与 idempotent 正交 ---------


def test_replay_missing_non_idempotent_read_tool_still_replayed():
    """access=read 但 idempotent=False：补执行门控看 access，仍会补执行（不重放副作用语义）。"""
    called: list = []
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read_non_idem",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=lambda args: called.append("read_non_idem") or "R",
            access="read",
            idempotent=False,
        )
    )
    messages: list = []

    asyncio.run(
        runtime._replay_missing_tool(
            ReplayToolCall(id="r1", name="read_non_idem", input={}),
            registry,
            messages,
        )
    )

    # access == "read" 门控放行：handler 被补执行
    assert called == ["read_non_idem"]
    blk = _last_tool_result(messages)
    assert blk is not None
    assert blk.tool_use_id == "r1"
    assert blk.content == '"R"'


def test_replay_missing_idempotent_write_tool_appends_placeholder():
    """access=write 但 idempotent=True：幂等 ≠ 无副作用，补执行门控仍回填占位不重放。"""
    called: list = []
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="write_idem",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=lambda args: called.append("write_idem") or "W",
            access="write",
            idempotent=True,
        )
    )
    messages: list = []

    asyncio.run(
        runtime._replay_missing_tool(
            ReplayToolCall(id="w1", name="write_idem", input={}),
            registry,
            messages,
        )
    )

    # 副作用语义门控：不补执行，仅回填占位闭合协议
    assert called == []
    blk = _last_tool_result(messages)
    assert blk is not None
    assert blk.tool_use_id == "w1"
    assert blk.content == _SKIP_PLACEHOLDER
    assert blk.is_error is False


# 补执行落 tool_execute span（二次 replay 不再判缺失） -------------------


def test_replay_missing_unnamed_tool_logs_info(log_records):
    """缺失工具无 name（防御分支）：跳过并记 info（不静默）。"""
    called: list = []
    registry = _registry_with("x", "read", called)
    messages: list = []

    asyncio.run(
        runtime._replay_missing_tool(
            ReplayToolCall(id="n1", name="", input={}),
            registry,
            messages,
        )
    )

    infos = [line for level, line in log_records if level == "INFO"]
    assert any("无 name" in line for line in infos), (
        f"无 name 防御分支应记 info，实际 {infos}"
    )
    assert messages == []


def test_replay_missing_read_tool_failure_logs_warning(caplog):
    """只读工具补执行失败（异常被吞并回填错误块）：记 warning。"""

    def _boom(args):
        raise RuntimeError("tool down")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read_boom",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=_boom,
            access="read",
        )
    )
    messages = [
        Message(
            role="assistant",
            content=[ToolUseBlock(id="r1", name="read_boom", input={})],
        )
    ]

    asyncio.run(
        runtime._replay_missing_tool(
            ReplayToolCall(id="r1", name="read_boom", input={}),
            registry,
            messages,
        )
    )

    blk = _last_tool_result(messages)
    assert blk is not None and blk.is_error is True
    # 失败 warning 由唯一执行入口（run_tool）统一产出；tools.py 用 stdlib logging
    messages = [r.getMessage() for r in caplog.records]
    assert any("read_boom" in m and "执行失败" in m for m in messages), (
        f"补执行失败应记 warning，实际 {messages}"
    )


def test_replay_missing_tool_writes_span_and_second_replay_not_missing(monkeypatch):
    """补执行缺失工具后写 tool_execute span，二次 replay 不再判缺失。"""
    from app.services.agent.observe import trace

    real_replay = trace.replay

    run_id = "run-continue-replay-span"
    sr_id = 401
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(sync_record_id=sr_id)

    call_count = {"n": 0}

    async def _chat(
        messages, *, tools=None, tool_choice=None, job_name=None, thinking_level=None
    ):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return ChatResponse(
                content="",
                stop_reason="tool_use",
                blocks=[
                    ToolUseBlock(id="t1", name="search_bangumi", input={"title": "x"})
                ],
            )
        return ChatResponse(content="done", stop_reason="end_turn")

    client = MagicMock()
    response_fn = AsyncMock(side_effect=_chat)
    _wire_stream_client(client, response_fn)
    monkeypatch.setattr(
        "app.services.matching.llm_assist.get_llm_client", lambda: client
    )

    class _Bgm:
        def search(self, **kwargs):
            return [{"id": 1, "name": "result"}]

        def get_subject(self, sid):
            return {"name": f"s-{sid}", "name_cn": f"条目-{sid}"}

        def get_related_subjects(self, sid):
            return []

        async def search_async(self, **kwargs):
            return self.search(**kwargs)

        async def get_subject_async(self, sid):
            return self.get_subject(sid)

        async def get_related_subjects_async(self, sid):
            return self.get_related_subjects(sid)

    bgm = _Bgm()
    eb_calls = {"n": 0}

    async def _eb(self, tool_calls, *, recorder=None):
        eb_calls["n"] += 1
        if eb_calls["n"] == 1:
            raise RuntimeError("crash mid-exec")
        return {
            tc.id: ToolResultBlock(tool_use_id=tc.id, content="ok", is_error=False)
            for tc in tool_calls
        }

    # per-run registry：patch 类方法以覆盖任意实例
    monkeypatch.setattr(ToolRegistry, "execute_batch", _eb)

    async def _go():
        # 初始正常运行至崩溃（记录 1 条 llm_chat span，但 tool_execute 缺失）
        await llm_assist.get_scenario_runtime().run(
            run_id, ctx=_ctx(sr, bgm), thinking_level="medium"
        )
        # 恢复续跑：真实 trace.replay + 真实 run_loop
        await llm_assist.get_scenario_runtime().continue_run(run_id, ctx=_ctx(sr, bgm))

    asyncio.run(_go())

    steps = database_manager.agent_runs.get_steps(run_id)
    tool_steps = [s for s in steps if s["name"] == "tool_execute"]
    assert len(tool_steps) >= 1, (
        f"补执行应产生至少 1 条 tool_execute span，实际 {len(tool_steps)}"
    )

    rr2 = real_replay(run_id)
    assert len(rr2.missing_tool_calls) == 0, (
        f"二次 replay 不应再判缺失，实际 missing={rr2.missing_tool_calls}"
    )


# 端到端断点恢复：恢复续跑不重复调 LLM ---------------------------------------


def test_continue_run_recovery_no_double_llm_call(monkeypatch):
    """崩溃前 1 次 + 恢复后 1 次 → 累计 LLM 调用 2，run 落 succeeded。"""
    run_id = "run-continue-m22"
    sr_id = 992
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(sync_record_id=sr_id)

    spy_calls = {"n": 0}

    async def _chat(
        messages, *, tools=None, tool_choice=None, job_name=None, thinking_level=None
    ):
        spy_calls["n"] += 1
        if spy_calls["n"] == 1:
            return ChatResponse(
                content="",
                stop_reason="tool_use",
                blocks=[
                    ToolUseBlock(id="t1", name="search_bangumi", input={"title": "foo"})
                ],
            )
        return ChatResponse(content="done", stop_reason="end_turn")

    response_fn = AsyncMock(side_effect=_chat)
    client = MagicMock()
    _wire_stream_client(client, response_fn)
    monkeypatch.setattr(
        "app.services.matching.llm_assist.get_llm_client", lambda: client
    )

    class _Bgm:
        def search(self, **kwargs):
            return [{"id": 1, "name": "foo"}]

        def get_subject(self, sid):
            return {"name": f"subject-{sid}", "name_cn": f"条目-{sid}"}

        def get_related_subjects(self, sid):
            return []

        async def search_async(self, **kwargs):
            return self.search(**kwargs)

        async def get_subject_async(self, sid):
            return self.get_subject(sid)

        async def get_related_subjects_async(self, sid):
            return self.get_related_subjects(sid)

    bgm = _Bgm()
    eb_calls = {"n": 0}

    async def _eb(self, tool_calls, *, recorder=None):
        eb_calls["n"] += 1
        if eb_calls["n"] == 1:
            raise RuntimeError("crash mid-exec")
        return {
            tc.id: ToolResultBlock(tool_use_id=tc.id, content="ok", is_error=False)
            for tc in tool_calls
        }

    # per-run registry：patch 类方法以覆盖任意实例
    monkeypatch.setattr(ToolRegistry, "execute_batch", _eb)

    async def _go():
        await llm_assist.get_scenario_runtime().run(
            run_id, ctx=_ctx(sr, bgm), thinking_level="medium"
        )
        await llm_assist.get_scenario_runtime().continue_run(run_id, ctx=_ctx(sr, bgm))

    asyncio.run(_go())

    assert response_fn.call_count == 2, (
        f"期望累计 2 次 LLM 调用，实际 {response_fn.call_count}"
    )
    run_row = _run_row(run_id)
    assert run_row["status"] == "succeeded"


# 恢复续跑写 chat span + thinking_level 透传 -------------------------


def test_continue_run_writes_chat_span(monkeypatch):
    """续跑轮应产生 chat span（last_response=None 进入通用循环）。"""
    run_id = "run-continue-chat-span"
    sr_id = 993
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(sync_record_id=sr_id)

    async def _chat(
        messages, *, tools=None, tool_choice=None, job_name=None, thinking_level=None
    ):
        return ChatResponse(content="done", stop_reason="end_turn")

    client = MagicMock()
    response_fn = AsyncMock(side_effect=_chat)
    _wire_stream_client(client, response_fn)
    monkeypatch.setattr(
        "app.services.matching.llm_assist.get_llm_client", lambda: client
    )

    rr = _make_replay_result(
        executed=0,
        missing=[],
        last_response=None,
        messages=[
            Message(role="system", content="s"),
            Message(role="user", content="u"),
        ],
    )
    monkeypatch.setattr("app.services.agent.observe.trace.replay", lambda rid: rr)

    asyncio.run(
        llm_assist.get_scenario_runtime().continue_run(
            run_id, ctx=_ctx(sr, MagicMock())
        )
    )

    steps = database_manager.agent_runs.get_steps(run_id)
    chat_steps = [s for s in steps if s["name"] == "llm_chat"]
    assert len(chat_steps) >= 1, (
        f"恢复路径应产生至少 1 条 chat span，实际 {len(chat_steps)}"
    )


def test_continue_run_respects_thinking_level(monkeypatch):
    """配置 thinking_level='high' → _build_default_stream_fn 收到 'high'。"""
    repo = _make_continuation_repo()
    captured: dict = {}

    def _fake_build(thinking_level):
        captured["thinking_level"] = thinking_level

        async def stream_fn(messages, *, tools=None, tool_choice=None):
            yield StreamChunk(type="stop", stop_reason="end_turn")

        return stream_fn

    rr = _make_replay_result(executed=0, missing=[], last_response=None)

    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch(
            "app.services.matching.llm_assist._build_default_stream_fn",
            side_effect=_fake_build,
        ),
    ):
        cm.get_sync_llm_match_config.return_value = {
            "llm_match_thinking_level": "high",
            "llm_match_max_iterations": "",
        }
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r-think", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    assert captured.get("thinking_level") == "high", (
        f"恢复路径应透传 thinking_level='high'，实际 {captured}"
    )


def test_continue_run_normalizes_uppercase_thinking_level(tmp_path):
    """ini 配 'HIGH' → 集中配置归一化为 'high' → 恢复路径透传 'high'。"""
    from app.core.config import ConfigManager

    ini = tmp_path / "config.ini"
    ini.write_text("[sync]\nllm_match_thinking_level = HIGH\n", encoding="utf-8")
    cm = ConfigManager.__new__(ConfigManager)
    cm.platform = "Test"
    cm.cwd = tmp_path
    cm.config_paths = {
        "env": None,
        "mounted": tmp_path / "__no_mounted__.ini",
        "dev": tmp_path / "__no_dev__.ini",
        "default": ini,
    }
    cm.active_config_path = ini
    cm._config_cache = None
    cm._last_modified = 0
    cm._load_config()

    # 前置断言：真实 config 已完成归一化（否则后续透传断言无意义）
    assert cm.get_sync_llm_match_config()["llm_match_thinking_level"] == "high"

    repo = _make_continuation_repo()
    captured: dict = {}

    def _fake_build(thinking_level):
        captured["thinking_level"] = thinking_level

        async def stream_fn(messages, *, tools=None, tool_choice=None):
            yield StreamChunk(type="stop", stop_reason="end_turn")

        return stream_fn

    rr = _make_replay_result(executed=0, missing=[], last_response=None)

    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager", cm),
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch(
            "app.services.matching.llm_assist._build_default_stream_fn",
            side_effect=_fake_build,
        ),
    ):
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r-think-normalize", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    assert captured.get("thinking_level") == "high", (
        f"恢复路径应透传归一化后的 thinking_level='high'，实际 {captured}"
    )


# 续跑新 span iteration 严格大于既有最大 iteration ----------------------


def test_continue_run_iteration_strictly_greater_than_existing_max(monkeypatch):
    """executed_iterations=2（既有最大 1）→ 续跑 chat span iteration >= 2。"""
    run_id = "run-continue-iter"
    sr_id = 994
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(sync_record_id=sr_id)

    async def _chat(
        messages, *, tools=None, tool_choice=None, job_name=None, thinking_level=None
    ):
        return ChatResponse(content="done", stop_reason="end_turn")

    client = MagicMock()
    response_fn = AsyncMock(side_effect=_chat)
    _wire_stream_client(client, response_fn)
    monkeypatch.setattr(
        "app.services.matching.llm_assist.get_llm_client", lambda: client
    )

    rr = _make_replay_result(
        executed=2,
        missing=[],
        last_response=None,
        messages=[
            Message(role="system", content="s"),
            Message(role="user", content="u"),
        ],
    )
    monkeypatch.setattr("app.services.agent.observe.trace.replay", lambda rid: rr)

    asyncio.run(
        llm_assist.get_scenario_runtime().continue_run(
            run_id, ctx=_ctx(sr, MagicMock())
        )
    )

    steps = database_manager.agent_runs.get_steps(run_id)
    chat_steps = [s for s in steps if s["name"] == "llm_chat"]
    assert len(chat_steps) >= 1, "恢复路径应产生至少 1 条 chat span"
    min_chat_iter = min(s["iteration"] for s in chat_steps)
    assert min_chat_iter >= 2, (
        f"续跑 chat span 的最小 iteration 应 >= 2（接续 executed_iterations=2），"
        f"实际最小 iteration={min_chat_iter}"
    )


# 二次恢复不产生额外 LLM 调用，且 chat span 无撞号 --------------------


@pytest.mark.asyncio
async def test_continue_run_double_recovery_no_extra_llm_call(monkeypatch):
    """崩溃 → 恢复完成 → 再次重置 processing → 第二次恢复不再额外调 LLM。"""
    from app.services.agent.observe import trace

    real_replay = trace.replay

    run_id = "run-continue-double"
    sr_id = 995
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(sync_record_id=sr_id)

    chat_calls = {"n": 0}

    async def _chat(
        messages, *, tools=None, tool_choice=None, job_name=None, thinking_level=None
    ):
        chat_calls["n"] += 1
        if chat_calls["n"] == 1:
            return ChatResponse(
                content="",
                stop_reason="tool_use",
                blocks=[
                    ToolUseBlock(id="t1", name="search_bangumi", input={"title": "x"})
                ],
            )
        return ChatResponse(content="done", stop_reason="end_turn")

    client = MagicMock()
    response_fn = AsyncMock(side_effect=_chat)
    _wire_stream_client(client, response_fn)
    monkeypatch.setattr(
        "app.services.matching.llm_assist.get_llm_client", lambda: client
    )

    class _Bgm:
        def search(self, **kwargs):
            return [{"id": 1, "name": "result"}]

        def get_subject(self, sid):
            return {"name": f"s-{sid}", "name_cn": f"条目-{sid}"}

        def get_related_subjects(self, sid):
            return []

        async def search_async(self, **kwargs):
            return self.search(**kwargs)

        async def get_subject_async(self, sid):
            return self.get_subject(sid)

        async def get_related_subjects_async(self, sid):
            return self.get_related_subjects(sid)

    bgm = _Bgm()

    eb_calls = {"n": 0}

    async def _eb(self, tool_calls, *, recorder=None):
        """模拟 execute_batch：写 tool_execute span（同真实实现），首次调用抛错。"""
        eb_calls["n"] += 1
        if eb_calls["n"] == 1:
            if recorder is not None:
                for i, tc in enumerate(tool_calls):
                    sid = recorder.start_tool(tc, sequence=i)
                    recorder.end_tool(sid, error="crash")
            raise RuntimeError(f"crash #{eb_calls['n']}")
        results = {}
        for i, tc in enumerate(tool_calls):
            sid = recorder.start_tool(tc, sequence=i) if recorder else None
            blk = ToolResultBlock(tool_use_id=tc.id, content="ok", is_error=False)
            results[tc.id] = blk
            if sid is not None:
                assert recorder is not None
                recorder.end_tool(sid, result=blk)
        return results

    monkeypatch.setattr(ToolRegistry, "execute_batch", _eb)

    # 第一次 run → 崩溃
    await llm_assist.get_scenario_runtime().run(
        run_id, ctx=_ctx(sr, bgm), thinking_level="medium"
    )
    # 第一次恢复（完成对话）
    await llm_assist.get_scenario_runtime().continue_run(run_id, ctx=_ctx(sr, bgm))

    steps_after_first = database_manager.agent_runs.get_steps(run_id)
    chat_iters_after_first = [
        s["iteration"] for s in steps_after_first if s["name"] == "llm_chat"
    ]
    assert len(chat_iters_after_first) == len(set(chat_iters_after_first)), (
        f"第一次恢复后 chat span iteration 存在撞号：{chat_iters_after_first}"
    )

    # 模拟二次崩溃：改回 processing
    database_manager.agent_runs.update_run_status(run_id, "processing")

    # 第二次恢复（应直接 mark_succeeded，无额外 LLM 调用）
    await llm_assist.get_scenario_runtime().continue_run(run_id, ctx=_ctx(sr, bgm))

    assert chat_calls["n"] == 2, (
        f"期望累计 2 次 LLM 调用（第二次恢复不应额外调 LLM），实际 {chat_calls['n']}"
    )

    steps_final = database_manager.agent_runs.get_steps(run_id)
    chat_iters_final = [s["iteration"] for s in steps_final if s["name"] == "llm_chat"]
    assert len(chat_iters_final) == len(set(chat_iters_final)), (
        f"最终 chat span iteration 存在撞号：{chat_iters_final}"
    )

    rr_final = real_replay(run_id)
    assert len(rr_final.missing_tool_calls) == 0, (
        f"二次 replay 不应再判缺失，实际 missing={rr_final.missing_tool_calls}"
    )


# run() 可重试失败达上限单点置终态（无双写） -----------------------------


def test_run_retryable_llm_error_at_limit_single_terminal_write():
    """可重试 LLM 失败达上限 → 仅 increment_attempts 单点（内部置终态），不再 mark_failed。"""
    from app.services.llm.client import LLMCallError

    repo = MagicMock()
    repo.atomic_claim.return_value = True
    repo.increment_attempts.return_value = 3
    dbm = MagicMock()
    dbm.agent_runs = repo

    async def _boom(messages, *, tools=None, tool_choice=None):
        raise LLMCallError("500 boom", retryable=True)

    with (
        patch("app.services.agent.run.runtime.get_database_manager", return_value=dbm),
        patch("app.services.matching.llm_assist.config_manager") as cm,
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        status = asyncio.run(
            llm_assist.get_scenario_runtime().run(
                "run-double-write",
                ctx=_ctx(_make_sync_record(sync_record_id=520), MagicMock()),
                thinking_level="medium",
                stream_fn=_scripted_stream(_boom),
            )
        )

    assert status == "failed"
    repo.increment_attempts.assert_called_once_with(
        "run-double-write", last_error="500 boom"
    )
    # 终态写入收敛到 increment_attempts 单点事务，外层不得二次 mark_failed
    repo.mark_failed.assert_not_called()


# ---------------------------------------------------------------------------
# total_tokens 全轮累计（非仅末轮）
# ---------------------------------------------------------------------------


def _search_response_with_usage(tokens: int) -> ChatResponse:
    """带 usage 的搜索响应（tool_use），供多轮累计测试用。"""
    return ChatResponse(
        content="",
        stop_reason="tool_use",
        blocks=[
            ToolUseBlock(id="t1", name="search_bangumi", input={"title": "花开伊吕波"})
        ],
        usage=Usage(total_tokens=tokens),
    )


def _submit_response_with_usage(tokens: int, subject_id="123", reason="跨季匹配"):
    """带 usage 的 submit_suggestion 响应。"""
    return ChatResponse(
        content="",
        stop_reason="tool_use",
        blocks=[
            ToolUseBlock(
                id="s1",
                name="submit_suggestion",
                input={"subject_id": subject_id, "reason": reason},
            )
        ],
        usage=Usage(total_tokens=tokens),
    )


def test_trace_recorder_accumulates_total_tokens_across_rounds():
    """chat span 逐轮累计 usage.total_tokens。"""
    from unittest.mock import patch

    recorder = TraceRecorder("run-tok-rec", start_iteration=0)
    responses = [
        ChatResponse(content="", stop_reason="end_turn", usage=Usage(total_tokens=100)),
        ChatResponse(content="", stop_reason="end_turn", usage=Usage(total_tokens=100)),
    ]
    chat = _chat_side_effect(responses)

    with (
        patch("app.services.agent.observe.recorder.start_span", return_value="span-x"),
        patch("app.services.agent.observe.recorder.end_span"),
    ):
        _drain_chat_span(recorder, _scripted_stream(chat))
        _drain_chat_span(recorder, _scripted_stream(chat))

    assert recorder.total_tokens == 200, (
        f"应累计 2 轮 tokens=200，实际 {recorder.total_tokens}"
    )


@pytest.mark.asyncio
async def test_total_tokens_accumulates_across_all_rounds(monkeypatch):
    """2 轮 chat 各 100 tokens → 终态 agent_runs.total_tokens == 200。"""
    run_id = "run-tokens-accum"
    sr_id = 520
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    chat = _chat_side_effect(
        [
            _search_response_with_usage(100),
            _submit_response_with_usage(100),
        ]
    )

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _make_bgm()),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        notification_service=None,
    )

    assert status == "succeeded"
    run_row = _run_row(run_id)
    assert run_row["total_tokens"] == 200, (
        f"应累计全部轮次 tokens=200，实际 {run_row['total_tokens']}"
    )


@pytest.mark.asyncio
async def test_total_tokens_zero_when_no_usage():
    """响应无 usage 时累计为 0（不报错、不误记）。"""
    run_id = "run-tokens-zero"
    sr_id = 521
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    with patch.object(llm_assist, "_validate_subject_id", return_value=(True, "")):
        chat = _chat_side_effect(
            [_search_response(), _submit_response("123", "跨季匹配")]
        )
        status = await llm_assist.get_scenario_runtime().run(
            run_id,
            ctx=_ctx(sr, _make_bgm()),
            thinking_level="medium",
            stream_fn=_scripted_stream(chat),
            notification_service=None,
        )

    assert status == "succeeded"
    run_row = _run_row(run_id)
    assert run_row["total_tokens"] == 0, (
        f"无 usage 应记 0，实际 {run_row['total_tokens']}"
    )


# ---------------------------------------------------------------------------
# 静默分支补日志 / _prefetch_bgm_name 失败记 error /
# TraceRecorder docstring 与 _SYSTEM_SUFFIX 文案清理
#
# 说明：本模块使用 ``app.core.logging.logger``（自定义 print 实现），
# stdlib ``caplog`` 无法捕获；此处沿用 ``tests/services/agent/test_replay.py``
# 的监听器捕获方式（监听器不受级别阈值限制，可断言级别 + 关键字）。
# ---------------------------------------------------------------------------


@pytest.fixture
def log_records():
    """捕获自定义 Logger 产出的 ``(level, line)`` 记录，供级别 + 关键字断言。"""
    from app.core.logging import logger as app_logger

    records: list[tuple[str, str]] = []

    def _listener(line: str, level: str) -> None:
        records.append((level, line))

    app_logger.add_listener(_listener)
    yield records
    app_logger.remove_listener(_listener)


def test_extract_candidates_invalid_json_logs_warning(log_records):
    """match_trace 为非法 JSON 字符串 → warning（含异常信息）且返回 []。"""
    sr = {"match_trace": "{ this is not json"}

    result = llm_assist._extract_candidates(sr)

    assert result == []
    warnings = [line for level, line in log_records if level == "WARNING"]
    assert warnings, "非法 JSON 不应静默吞掉，必须记 warning"
    assert any("match_trace" in line and "解析失败" in line for line in warnings), (
        f"warning 应指明 match_trace JSON 解析失败，实际 {warnings}"
    )
    # 含异常信息（JSONDecodeError 文本），证明未只记一句无信息的占位
    assert any("Expecting" in line for line in warnings), (
        f"warning 应包含异常信息，实际 {warnings}"
    )


def test_extract_candidates_non_dict_step_logs_info(log_records):
    """steps 中非 dict 元素 → info（不再静默 continue），返回 []。"""
    sr = {"match_trace": {"steps": ["not-a-dict"]}}

    result = llm_assist._extract_candidates(sr)

    assert result == []
    infos = [line for level, line in log_records if level == "INFO"]
    assert any("非 dict step" in line for line in infos), (
        f"跳过非 dict step 应记 info，实际 {infos}"
    )


def test_extract_candidates_non_dict_candidate_logs_info(log_records):
    """候选列表含非 dict 元素 → info（不再静默 continue），返回 []。"""
    sr = {"match_trace": {"steps": [{"candidates": ["bad-cand"]}]}}

    result = llm_assist._extract_candidates(sr)

    assert result == []
    infos = [line for level, line in log_records if level == "INFO"]
    assert any("非 dict 候选" in line for line in infos), (
        f"跳过非 dict 候选应记 info，实际 {infos}"
    )


def test_extract_candidates_empty_or_duplicate_subject_id_logs_info(log_records):
    """subject_id 为空 / 重复 → info（两种原因均须可见），只保留唯一有效候选。"""
    sr = {
        "match_trace": {
            "steps": [
                {
                    "candidates": [
                        {"subject_id": ""},
                        {"subject_id": "111", "score": 0.5},
                        {"subject_id": "111", "score": 0.9},
                    ]
                }
            ]
        }
    }

    result = llm_assist._extract_candidates(sr)

    assert [str(c["subject_id"]) for c in result] == ["111"]
    infos = [line for level, line in log_records if level == "INFO"]
    assert any("subject_id 为空" in line for line in infos), (
        f"subject_id 为空应记 info，实际 {infos}"
    )
    assert any("重复 subject_id" in line for line in infos), (
        f"subject_id 重复应记 info，实际 {infos}"
    )


def test_prefetch_bgm_name_failure_logs_error_with_subject_id(log_records):
    """bgm.get_subject 抛异常 → error（含 subject_id + 异常信息），仍返回空串。"""

    class _BoomBgm:
        def get_subject(self, sid):
            raise RuntimeError("boom-network")

    result = llm_assist._prefetch_bgm_name(_BoomBgm(), "123")

    assert result == "", "失败时行为不变：仍返回空串"
    errors = [line for level, line in log_records if level == "ERROR"]
    assert errors, "预取失败不应静默 pass，必须记 error"
    assert any("123" in line and "boom-network" in line for line in errors), (
        f"error 应包含 subject_id 与异常信息，实际 {errors}"
    )


def test_prefetch_bgm_name_none_bgm_returns_empty_without_error(log_records):
    """bgm 为 None（无 HTTP 机会）→ 返回空串且不记 error（非异常路径）。"""
    assert llm_assist._prefetch_bgm_name(None, "1") == ""
    assert not [line for level, line in log_records if level == "ERROR"], (
        "bgm=None 属正常短路，不应记 error"
    )


def test_system_suffix_describes_interactive_rounds_not_tool_call_count():
    """[剩余轮次] 文案应表述为可交互轮次（每轮可执行多个工具）。"""
    suffix = llm_assist._SYSTEM_SUFFIX

    assert "表示剩余可交互轮次（每轮可执行多个工具）" in suffix
    assert "表示剩余可调用工具的次数" not in suffix, "旧文案应被替换"


# ---------------------------------------------------------------------------
# 收尾路径异步化 —— 消除事件循环上的同步阻塞
#
# 收尾链 ``_handle_result``（同步）含阻塞 HTTP（``_validate_subject_id`` /
# ``_prefetch_bgm_name``）与限速器同步 sleep，必须移入线程池执行，否则
# 阻塞事件循环。（线程安全前提核查见源码 ``_handle_result_async`` docstring）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_tail_does_not_block_event_loop(monkeypatch):
    """run 收尾期间（bgm.get_subject 同步睡 0.2s）事件循环仍被调度。

    证据：心跳协程在慢函数执行窗口内持续递增（每 0.01s 一次），且落库/通知
    行为保持正常。若收尾链在事件循环线程同步执行，心跳在该窗口内为 0。
    """
    run_id = "run-async-tail"
    sr_id = 91
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)

    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))

    tail_active = threading.Event()

    class _SlowBgm:
        """同步 get_subject（收尾链）与 async 协议（工具路径）均为阻塞模拟。"""

        def search(self, **kwargs):
            return [{"id": 1}]

        def get_subject(self, sid):
            tail_active.set()
            time.sleep(0.2)
            tail_active.clear()
            return {"name": f"subject-{sid}", "name_cn": f"条目-{sid}"}

        def get_related_subjects(self, sid):
            return []

        async def search_async(self, **kwargs):
            return [{"id": 1}]

        async def get_subject_async(self, sid):
            tail_active.set()
            await asyncio.sleep(0.2)
            tail_active.clear()
            return {"name": f"subject-{sid}", "name_cn": f"条目-{sid}"}

        async def get_related_subjects_async(self, sid):
            return []

    beats_during_tail = 0

    async def _heartbeat():
        nonlocal beats_during_tail
        while True:
            await asyncio.sleep(0.01)
            if tail_active.is_set():
                beats_during_tail += 1

    ns = _make_notify()
    hb = asyncio.create_task(_heartbeat())
    await asyncio.sleep(0)  # 让心跳先进入等待，再执行收尾
    try:
        status = await llm_assist.get_scenario_runtime().run(
            run_id,
            ctx=_ctx(sr, _SlowBgm()),
            thinking_level="medium",
            stream_fn=_scripted_stream(
                _chat_side_effect([_search_response(), _submit_response()])
            ),
            notification_service=ns,
            span_recorder=None,
        )
    finally:
        hb.cancel()
        with suppress(asyncio.CancelledError):
            await hb

    assert status == "succeeded"
    # 0.2s / 0.01s ≈ 20 次机会；阈值 5 留足 CI 余量
    assert beats_during_tail >= 5, (
        f"收尾期间事件循环应持续调度心跳（实际仅 {beats_during_tail} 次）"
    )
    _assert_candidate_written(sr_id)
    ns.notify.assert_called_once()


@pytest.mark.asyncio
async def test_run_tail_concurrent_db_ops_during_slow_prefetch_no_lock_error(
    monkeypatch,
):
    """收尾线程池执行期间，事件循环/其他线程并发 DB 操作必须安全。

    窗口构造：``_prefetch_bgm_name`` 调用 ``bgm.get_subject`` 时置位 ``entered``
    并阻塞在 ``release`` 上，把收尾线程钉在慢预取窗口内；测试侧在窗口内用
    ``asyncio.to_thread`` 并发发起一次读（``agent_runs.get_run``）与一次写
    （``log_pending_candidate``）。若收尾链错误占有共享锁或未走线程池，这些
    并发操作会抛 ``database is locked`` / 争用异常。

    断言：并发操作全部成功且无异常、目标 run 终态 succeeded、通知恰好一次。
    """
    run_id = "run-tail-concurrent"
    sr_id = 92
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=True, sync_record_id=sr_id)
    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))

    entered = threading.Event()
    release = threading.Event()

    class _BlockingBgm:
        """get_subject 阻塞在慢预取窗口，直到测试侧放行。"""

        def search(self, **kwargs):
            return [{"id": 1}]

        def get_subject(self, sid):
            entered.set()
            # 超时兜底：避免测试侧异常时永久卡死
            assert release.wait(timeout=10), "收尾慢预取窗口未被测试释放"
            return {"name": f"subject-{sid}", "name_cn": f"条目-{sid}"}

        def get_related_subjects(self, sid):
            return []

        async def search_async(self, **kwargs):
            return self.search(**kwargs)

        async def get_subject_async(self, sid):
            return self.get_subject(sid)

        async def get_related_subjects_async(self, sid):
            return self.get_related_subjects(sid)

    ns = _make_notify()
    task = asyncio.create_task(
        llm_assist.get_scenario_runtime().run(
            run_id,
            ctx=_ctx(sr, _BlockingBgm()),
            thinking_level="medium",
            stream_fn=_scripted_stream(
                _chat_side_effect([_search_response(), _submit_response()])
            ),
            notification_service=ns,
            span_recorder=None,
        )
    )

    # 等待收尾线程进入慢预取窗口
    for _ in range(1000):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered.is_set(), "收尾线程未进入慢预取窗口"

    errors: list[Exception] = []

    def _concurrent_read():
        try:
            return database_manager.agent_runs.get_run(run_id)
        except Exception as e:  # noqa: BLE001 - 收集并发异常用于断言
            errors.append(e)
            return None

    def _concurrent_write():
        try:
            return database_manager.log_pending_candidate(
                request_title="并发写入",
                request_season=1,
                user_name="bob",
                source="plex",
                candidates=[{"subject_id": "999", "name": "并发", "score": 0.5}],
                sync_record_id=993,
            )
        except Exception as e:  # noqa: BLE001 - 收集并发异常用于断言
            errors.append(e)
            return None

    read_row, new_cid = await asyncio.gather(
        asyncio.to_thread(_concurrent_read),
        asyncio.to_thread(_concurrent_write),
    )

    release.set()
    status = await asyncio.wait_for(task, timeout=10)

    assert not errors, f"收尾窗口内并发 DB 操作不应抛异常: {errors}"
    assert not any("locked" in str(e).lower() for e in errors), (
        f"不应出现 database is locked 类错误: {errors}"
    )
    assert read_row is not None and read_row["run_id"] == run_id
    assert new_cid, "并发写入应成功返回候选 id"
    assert status == "succeeded"
    run_row = _run_row(run_id)
    assert run_row["status"] == "succeeded"
    _assert_candidate_written(sr_id)
    ns.notify.assert_called_once()


def test_continue_run_submit_tail_runs_off_event_loop_thread():
    """continue_run 的 submit 收尾分派不得在事件循环线程执行（线程归属证据）。"""
    repo = _make_continuation_repo()
    rr = _make_replay_result(
        executed=1,
        last_response={
            "stop_reason": "submit_suggestion",
            "content": "",
            "tool_calls": [
                {
                    "id": "s1",
                    "name": "submit_suggestion",
                    "input": {"subject_id": "123", "reason": "跨季匹配"},
                }
            ],
        },
    )
    loop = AsyncMock()
    main_ident = threading.get_ident()
    seen: dict = {}

    def _capture(*args, **kwargs):
        seen["ident"] = threading.get_ident()
        return "succeeded"

    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime.run_loop", loop),
        patch(
            "app.services.matching.llm_assist._handle_result",
            side_effect=_capture,
        ),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r-tail", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    assert seen.get("ident") is not None, "_handle_result 应被调用"
    assert seen["ident"] != main_ident, "收尾链应在工作线程执行，不得占用事件循环线程"


def test_continue_run_continuation_tail_runs_off_event_loop_thread():
    """_execute_continuation 续跑后的收尾分派同样不在事件循环线程执行。"""
    repo = _make_continuation_repo()
    seed = [Message(role="system", content="s"), Message(role="user", content="u")]
    rr = _make_replay_result(executed=2, missing=[], last_response=None, messages=seed)
    loop = AsyncMock(return_value=RunResult(stop_reason="end_turn"))
    main_ident = threading.get_ident()
    seen: dict = {}

    def _capture(*args, **kwargs):
        seen["ident"] = threading.get_ident()
        return "succeeded"

    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch("app.services.agent.run.runtime.run_loop", loop),
        patch(
            "app.services.matching.llm_assist._handle_result",
            side_effect=_capture,
        ),
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r-cont", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    loop.assert_awaited_once()
    assert seen.get("ident") is not None, "_handle_result 应被调用"
    assert seen["ident"] != main_ident, (
        "续跑收尾链应在工作线程执行，不得占用事件循环线程"
    )


# ---------------------------------------------------------------------------
# give_up 放弃协议 + 终止提交软护栏（veto）
# ---------------------------------------------------------------------------

# 负样本真实 reason（来自 golden cassette m011/m012），用于验证 veto 命中
_M011_REASON = (
    "标题\u201c特典映像\u201d过于泛化（仅意为\u201c特典影像\u201d），"
    "无父系列/年份等线索，无法唯一确定。"
    "检索到多个同名特典条目（男子高校生の日常、我的英雄学院、BLACK LAGOON、加速世界等）。"
    "其中\u201c男子高校生の日常 特典映像\u201d(72767)名称最直接匹配且为完整6话特典，"
    "暂推荐，但存在较大不确定性。"
)
_M012_REASON = (
    "未找到名为「Formula 1 2025 赛季回顾」的条目。"
    "Bangumi 上 F1 相关条目仅有 Netflix 纪录片《一级方程式：疾速求生》第1-5季"
    "（2019-2023）及 2025 年电影《F1：狂飙飞车》(id 560263)。"
    "若该媒体实为 F1 题材电影，最接近的是 560263；"
    "但标题「2025赛季回顾」与现有条目均不符，无法确认，故仅作最接近推荐，建议人工复核。"
)

# 正向样本代表理由（真实 cassette 风格），必须零误伤
_CONFIDENT_REASONS = [
    "标题「无职转生 第三季 ～到了异世界就拿出真本事～」对应 Bangumi 条目 501963"
    "「無職転生Ⅲ ～異世界行ったら本気だす～」，放送开始 2026-07-04，"
    "与线索中的季数3及开播日期完全一致。",
    "标题「逃げ上手の若君 第二期」、放送开始 2026-07-17、季数2 与线索完全一致，"
    "为《擅长逃跑的殿下》第二季（CloverWorks，山崎雄太监督）。",
    "标题 Friends、季 1、开播日期 1994-09-22 完全匹配 Bangumi 条目 3864"
    "「老友记 第一季 / Friends (Season 1)」（NBC，1994-09-22 开播，24集）。",
    "标题\u201c狐独摇滚\u201d应为\u201c孤独摇滚！\u201d（ぼっち・ざ・ろっく！）的误写，"
    "开播日期2022-10-08与条目完全一致，季1对应TV本篇，故推荐ID 328609。",
]


def test_submit_suggestion_schema_exposes_give_up_and_relaxed_required():
    """give_up 协议：schema 新增 give_up 布尔属性，required 放宽为仅 reason。"""
    registry = ToolRegistry()
    llm_assist.register_match_tools(registry, _make_bgm())

    defn = registry.get("submit_suggestion")
    assert defn is not None
    props = defn.parameters["properties"]
    assert props["give_up"]["type"] == "boolean"
    assert props["give_up"]["default"] is False
    assert defn.parameters["required"] == ["reason"]
    assert "give_up" in defn.description


def test_match_hooks_registers_veto_terminal():
    """匹配场景 hooks 注册 veto 回调（runtime 据此透传给 loop）。"""
    assert llm_assist._MATCH_HOOKS.veto_terminal is llm_assist._match_veto_terminal


def test_match_veto_terminal_hits_negative_sample_reasons():
    """m011/m012 真实不确定理由 → 返回暂缓提示（命中标记）。"""
    for reason in (_M011_REASON, _M012_REASON):
        hint = llm_assist._match_veto_terminal({"subject_id": "1", "reason": reason})
        assert hint is not None, f"不确定理由应触发 veto: {reason[:30]}"
        assert "提交暂缓" in hint
        assert "give_up=true" in hint


def test_match_veto_terminal_no_false_positive_on_confident_reasons():
    """18 条正向案例风格理由 → 不命中（零误伤）。"""
    for reason in _CONFIDENT_REASONS:
        hint = llm_assist._match_veto_terminal({"subject_id": "1", "reason": reason})
        assert hint is None, f"确定理由不应触发 veto: {reason[:30]}"


def test_match_veto_terminal_passes_give_up_and_missing_reason():
    """give_up=true 是被鼓励行为，放行；无 reason 亦放行。"""
    assert (
        llm_assist._match_veto_terminal({"give_up": True, "reason": "无法确定，故放弃"})
        is None
    )
    assert llm_assist._match_veto_terminal({"subject_id": "1"}) is None


def test_handle_result_give_up_marks_succeeded_without_persist_or_notify(
    monkeypatch,
):
    """give_up=true → succeeded/give_up，不落库、不通知、不校验 subject_id。"""
    from unittest.mock import MagicMock

    from app.services.agent.loop import RunResult

    dbm = MagicMock()
    ns = _make_notify()
    persist_spy = MagicMock(return_value=True)
    validate_calls = {"n": 0}

    def _validate(sid):
        validate_calls["n"] += 1
        return (True, "")

    monkeypatch.setattr(llm_assist, "_persist_and_notify", persist_spy)
    monkeypatch.setattr(llm_assist, "_validate_subject_id", _validate)

    result = RunResult(
        stop_reason="submit_suggestion",
        terminal_args={"give_up": True, "reason": "确实无法确定，放弃"},
    )

    status = llm_assist._handle_result(
        dbm,
        "run-give-up",
        result,
        sync_record=_make_sync_record(sync_record_id=520),
        sync_record_id=520,
        bgm=_make_bgm(),
        notification_service=ns,
        total_tokens=42,
    )

    assert status == "succeeded"
    dbm.agent_runs.mark_succeeded.assert_called_once_with(
        "run-give-up", stop_reason="give_up", total_tokens=42
    )
    assert persist_spy.call_count == 0
    ns.notify.assert_not_called()
    assert validate_calls["n"] == 0


def test_handle_result_give_up_false_takes_normal_submit_path(monkeypatch):
    """give_up 缺省 / False → 走既有校验落库路径（行为不变）。"""
    from unittest.mock import MagicMock

    from app.services.agent.loop import RunResult

    dbm = MagicMock()
    persist_spy = MagicMock(return_value=True)
    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    monkeypatch.setattr(llm_assist, "_persist_and_notify", persist_spy)

    result = RunResult(
        stop_reason="submit_suggestion",
        terminal_args={"subject_id": "123", "reason": "确定", "give_up": False},
    )

    status = llm_assist._handle_result(
        dbm,
        "run-give-up-false",
        result,
        sync_record=_make_sync_record(sync_record_id=521),
        sync_record_id=521,
        bgm=_make_bgm(),
        notification_service=None,
        total_tokens=7,
    )

    assert status == "succeeded"
    assert persist_spy.call_count == 1
    dbm.agent_runs.mark_succeeded.assert_not_called()


@pytest.mark.asyncio
async def test_run_uncertain_reason_veto_then_give_up_ends_succeeded(monkeypatch):
    """端到端：不确定 submit 被 veto → 下一轮 give_up → 终态 succeeded(give_up)。"""
    run_id = "run-veto-giveup"
    sr_id = 7711
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(with_candidates=False, sync_record_id=sr_id)

    uncertain = ChatResponse(
        content="",
        stop_reason="tool_use",
        blocks=[
            ToolUseBlock(
                id="s1",
                name="submit_suggestion",
                input={"subject_id": "123", "reason": _M011_REASON},
            )
        ],
    )
    give_up = ChatResponse(
        content="",
        stop_reason="tool_use",
        blocks=[
            ToolUseBlock(
                id="s2",
                name="submit_suggestion",
                input={"give_up": True, "reason": "确实无法确定，放弃"},
            )
        ],
    )
    chat = _chat_side_effect([uncertain, give_up])
    ns = _make_notify()

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, _make_bgm()),
        thinking_level="medium",
        stream_fn=_scripted_stream(chat),
        notification_service=ns,
        span_recorder=None,
    )

    assert status == "succeeded"
    run_row = _run_row(run_id)
    assert run_row["status"] == "succeeded"
    assert run_row["stop_reason"] == "give_up"
    ns.notify.assert_not_called()
    assert _read_candidate(sr_id) is None, "give_up 不应落库任何候选"


# ---------------------------------------------------------------------------
# 流式注入端到端（mock 事件流）——停点提前执行 search + submit 捕获落库
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_stream_fn_end_to_end_early_execution_and_submit(monkeypatch):
    """注入 stream_fn（事件流）：幂等 search 停点提前执行，submit 捕获落库。"""
    run_id = "run-stream-e2e"
    sr_id = 9001
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(sync_record_id=sr_id)
    monkeypatch.setattr(llm_assist, "_validate_subject_id", lambda sid: (True, ""))
    bgm = _make_bgm()
    ns = _make_notify()
    calls = {"n": 0}

    async def stream_fn(messages, *, tools=None, tool_choice=None):
        calls["n"] += 1
        if calls["n"] == 1:
            yield StreamChunk(
                type="tool_use_start", tool_use_id="t1", tool_name="search_bangumi"
            )
            yield StreamChunk(
                type="tool_use_delta",
                tool_use_id="t1",
                partial_json='{"title": "花开伊吕波"}',
            )
            yield StreamChunk(type="tool_use_stop", tool_use_id="t1")
            yield StreamChunk(type="stop", stop_reason="tool_use")
        else:
            yield StreamChunk(
                type="tool_use_start",
                tool_use_id="s1",
                tool_name="submit_suggestion",
            )
            yield StreamChunk(
                type="tool_use_delta",
                tool_use_id="s1",
                partial_json='{"subject_id": "123", "reason": "跨季匹配"}',
            )
            yield StreamChunk(type="tool_use_stop", tool_use_id="s1")
            yield StreamChunk(type="stop", stop_reason="tool_use")

    status = await llm_assist.get_scenario_runtime().run(
        run_id,
        ctx=_ctx(sr, bgm),
        thinking_level="medium",
        stream_fn=stream_fn,
        notification_service=ns,
    )

    assert status == "succeeded"
    _assert_candidate_written(sr_id, subject_id="123", reason="跨季匹配")
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# 工具 handler 级防御：非数字 subject_id 不得让 int() 抛 ValueError 冒泡
# ---------------------------------------------------------------------------


class TestSubjectIdCoercionDefense:
    """``get_subject_detail`` / ``get_related_subjects`` 对非法 subject_id 返回结构化错误。

    正常执行路径已由 ToolRegistry._validate（type=string + pattern ^\\d+$）拦截，
    此处覆盖 handler 直接调用时的防御，避免 int() ValueError 冒泡中断工具循环。
    """

    @staticmethod
    def _handler(name: str):
        defns = llm_assist.register_match_tools(ToolRegistry(), _make_bgm())
        return next(d.handler for d in defns if d.name == name)

    @pytest.mark.parametrize("bad", ["abc", "", "12x", "  ", None, []])
    @pytest.mark.asyncio
    async def test_get_subject_detail_invalid_returns_error(self, bad):
        result = await self._handler("get_subject_detail")({"subject_id": bad})
        assert result == {"error": "invalid subject_id"}

    @pytest.mark.parametrize("bad", ["abc", "", "12x", None])
    @pytest.mark.asyncio
    async def test_get_related_subjects_invalid_returns_error(self, bad):
        result = await self._handler("get_related_subjects")({"subject_id": bad})
        assert result == {"error": "invalid subject_id"}

    @pytest.mark.asyncio
    async def test_get_subject_detail_valid_still_calls_bgm(self):
        result = await self._handler("get_subject_detail")({"subject_id": "123"})
        assert result["name"] == "subject-123"
        assert result["name_cn"] == "条目-123"


def test_replay_missing_read_tool_capture_falls_back_to_placeholder(
    log_records, monkeypatch
):
    """补执行遇 capture（read 门控失效）：warning + 占位，不重放副作用。"""
    from app.services.agent.protocols import TerminalCapture

    def _boom(args):
        raise AssertionError("capture 路径不得执行 handler")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read_cap",
            description="d",
            parameters={},
            handler=_boom,
            access="read",
        )
    )

    async def _fake_run_tool(tool_use, *, recorder=None, sequence=0):
        return TerminalCapture(name=tool_use.name, args=tool_use.input)

    monkeypatch.setattr(registry, "run_tool", _fake_run_tool)
    messages = [
        Message(
            role="assistant",
            content=[ToolUseBlock(id="c1", name="read_cap", input={})],
        )
    ]

    asyncio.run(
        runtime._replay_missing_tool(
            ReplayToolCall(id="c1", name="read_cap", input={}),
            registry,
            messages,
        )
    )

    blk = _last_tool_result(messages)
    assert blk is not None
    assert blk.content == _SKIP_PLACEHOLDER, (
        f"capture 应回填占位（不重放副作用），实际 {blk.content!r}"
    )
    assert blk.is_error is False
    warns = [line for level, line in log_records if level == "WARNING"]
    assert any("终止工具" in line and "read_cap" in line for line in warns), warns


# ---------------------------------------------------------------------------
# agent_run_failed 通知（run 进入 failed 终态的判定点）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_terminal_llm_failure_notifies_agent_run_failed():
    """确定性 LLM 失败 → 发送 agent_run_failed（携带 run_id / stop_reason）。"""
    from app.services.llm.client import LLMCallError

    run_id = "run-e-notify"
    sr_id = 510
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(sync_record_id=sr_id)

    async def _boom(messages, *, tools=None, tool_choice=None):
        raise LLMCallError("401 Unauthorized", retryable=False)

    with patch("app.services.base.notifier_helpers.notification_service") as notify:
        status = await llm_assist.get_scenario_runtime().run(
            run_id,
            ctx=_ctx(sr, _make_bgm()),
            thinking_level="medium",
            stream_fn=_scripted_stream(_boom),
            span_recorder=None,
        )

    assert status == "failed"
    notify.notify.assert_called_once()
    assert notify.notify.call_args.args[0] == "agent_run_failed"
    kwargs = notify.notify.call_args.kwargs
    assert kwargs.get("run_id") == run_id
    assert kwargs.get("task_type") == "match"
    assert kwargs.get("stop_reason") == "llm_error"


@pytest.mark.asyncio
async def test_run_retryable_failure_below_limit_does_not_notify():
    """可重试失败且未达上限（attempts<3）→ 不发失败通知（避免噪声）。"""
    from app.services.llm.client import LLMCallError

    run_id = "run-e-notify-retry"
    sr_id = 511
    database_manager.agent_runs.create_pending(run_id, "match", sr_id)
    sr = _make_sync_record(sync_record_id=sr_id)

    async def _boom(messages, *, tools=None, tool_choice=None):
        raise LLMCallError("429 rate limited", retryable=True)

    with patch("app.services.base.notifier_helpers.notification_service") as notify:
        status = await llm_assist.get_scenario_runtime().run(
            run_id,
            ctx=_ctx(sr, _make_bgm()),
            thinking_level="medium",
            stream_fn=_scripted_stream(_boom),
            span_recorder=None,
        )

    assert status == "processing"
    notify.notify.assert_not_called()


def test_continue_run_terminal_failure_notifies_agent_run_failed():
    """恢复续跑确定性失败 → 发送 agent_run_failed。"""
    from app.services.llm.client import LLMCallError

    repo = _make_continuation_repo()
    rr = _make_replay_result(executed=0, missing=[], last_response=None)

    async def _boom(messages, *, tools=None, tool_choice=None):
        raise LLMCallError("401 Unauthorized", retryable=False)
        yield  # pragma: no cover - 使其成为 async generator，供流式消费

    with (
        patch("app.services.agent.observe.trace.replay", return_value=rr),
        patch("app.services.matching.llm_assist.config_manager") as cm,
        patch(
            "app.services.agent.run.runtime.get_database_manager",
            return_value=_make_continuation_dbm(repo),
        ),
        patch(
            "app.services.matching.llm_assist._build_default_stream_fn",
            return_value=_boom,
        ),
        patch("app.services.base.notifier_helpers.notification_service") as notify,
    ):
        cm.get_sync_llm_match_config.return_value = _medium_cfg()
        asyncio.run(
            llm_assist.get_scenario_runtime().continue_run(
                "r-err-notify", ctx=_ctx({"id": 1}, MagicMock())
            )
        )

    repo.mark_failed.assert_called_once()
    notify.notify.assert_called_once()
    assert notify.notify.call_args.args[0] == "agent_run_failed"
    assert notify.notify.call_args.kwargs.get("run_id") == "r-err-notify"


def test_mark_persist_failed_notifies_agent_run_failed():
    """落库失败终态化（persist_error）→ 发送 agent_run_failed 通知。"""
    dbm = MagicMock()
    with patch("app.services.base.notifier_helpers.notification_service") as notify:
        llm_assist._mark_persist_failed(dbm, "run-persist", RuntimeError("disk full"))

    dbm.agent_runs.mark_failed.assert_called_once()
    notify.notify.assert_called_once()
    assert notify.notify.call_args.args[0] == "agent_run_failed"
    assert notify.notify.call_args.kwargs.get("stop_reason") == "persist_error"
