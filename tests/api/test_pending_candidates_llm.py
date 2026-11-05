"""待确认候选 API 的 LLM / agent_run 字段测试。

覆盖：
- 列表 / 详情 / 按 sync_record_id 查询 均返回 llm_subject_id、llm_reason、agent_run_status、agent_run_id
- agent_run_status ∈ {pending, processing} 反映评估中；succeeded 且有建议 → 反映成功
- 无关联 agent run → agent_run_status / agent_run_id 为 null
"""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, sync
from app.core.database import database_manager


def _make_app():
    app = FastAPI()
    app.include_router(sync.router)

    async def _mock_user(request=None, credentials=None):
        return {"username": "tester", "id": 1, "is_admin": True}

    app.dependency_overrides[deps.get_current_user_flexible] = _mock_user
    return app


def _log_candidate_with_llm(title, sync_record_id, llm_subject_id, llm_reason):
    """沉淀一条带 LLM 建议的待确认候选（经真实 repo 写入）。"""
    cid = database_manager.log_pending_candidate(
        request_title=title,
        request_season=1,
        request_episode=1,
        user_name="tester",
        source="custom",
        candidates=[{"subject_id": "111", "name": "某番"}],
        sync_record_id=sync_record_id,
    )

    def _set_llm(conn):
        conn.execute(
            "UPDATE pending_candidates SET llm_subject_id=?, llm_reason=? WHERE id=?",
            (llm_subject_id, llm_reason, cid),
        )

    database_manager._execute_with_lock(_set_llm)
    return cid


def _create_run(sync_record_id, status):
    run_id = f"run-{uuid.uuid4()}"
    database_manager.agent_runs.create_pending(run_id, "match", sync_record_id)
    if status == "processing":
        database_manager.agent_runs.atomic_claim(run_id)
    elif status == "succeeded":
        database_manager.agent_runs.atomic_claim(run_id)
        database_manager.agent_runs.mark_succeeded(
            run_id, stop_reason="submit_suggestion"
        )
    return run_id


@pytest.fixture
def app():
    return _make_app()


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


async def test_list_includes_llm_and_agent_run_fields(app, transport):
    """列表响应每条记录含 llm 两列 + agent_run 状态/run_id。"""
    sync_record_id = 9001
    _create_run(sync_record_id, "processing")
    _log_candidate_with_llm("AI评估中候选", sync_record_id, "12345", "标题语义相近")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/pending-candidates?limit=50")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    rec = next(
        r for r in data["data"]["records"] if r["request_title"] == "AI评估中候选"
    )
    assert rec["llm_subject_id"] == "12345"
    assert rec["llm_reason"] == "标题语义相近"
    assert rec["agent_run_status"] == "processing"
    assert rec["agent_run_id"] is not None


async def test_detail_includes_llm_and_agent_run_fields(app, transport):
    """详情响应 record 含 llm 两列 + agent_run 状态（succeeded 有建议）。"""
    sync_record_id = 9002
    _create_run(sync_record_id, "succeeded")
    cid = _log_candidate_with_llm(
        "AI推荐候选", sync_record_id, "67890", "跨季判断为第一季"
    )

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/pending-candidates/{cid}")

    assert resp.status_code == 200
    rec = resp.json()["data"]["record"]
    assert rec["llm_subject_id"] == "67890"
    assert rec["llm_reason"] == "跨季判断为第一季"
    assert rec["agent_run_status"] == "succeeded"
    assert rec["agent_run_id"] is not None


async def test_detail_by_sync_record_includes_fields(app, transport):
    """按 sync_record_id 查询的候选 record 同样含 agent_run 状态。"""
    sync_record_id = 9003
    _create_run(sync_record_id, "succeeded")
    _log_candidate_with_llm("按记录查询候选", sync_record_id, "54321", "建议理由")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/records/{sync_record_id}/pending-candidate")

    assert resp.status_code == 200
    rec = resp.json()["data"]["record"]
    assert rec["agent_run_status"] == "succeeded"
    assert rec["agent_run_id"] is not None


async def test_no_agent_run_yields_null(app, transport):
    """无关联 agent run 时 agent_run_status / agent_run_id 为 null。"""
    cid = _log_candidate_with_llm("无run候选", 9004, "13579", "纯手动沉淀的建议")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/pending-candidates/{cid}")

    rec = resp.json()["data"]["record"]
    # llm 字段仍有值
    assert rec["llm_subject_id"] == "13579"
    assert rec["llm_reason"] == "纯手动沉淀的建议"
    # 但无关联 run → null
    assert rec["agent_run_status"] is None
    assert rec["agent_run_id"] is None


async def test_llm_fields_default_empty_when_absent(app, transport):
    """老记录缺 llm 字段时（不写建议）返回空串而非缺失键。"""
    cid = database_manager.log_pending_candidate(
        request_title="普通候选",
        request_season=1,
        request_episode=1,
        user_name="tester",
        source="custom",
        candidates=[{"subject_id": "111", "name": "某番"}],
        sync_record_id=None,
    )

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/pending-candidates/{cid}")

    rec = resp.json()["data"]["record"]
    assert rec["llm_subject_id"] == ""
    assert rec["llm_reason"] == ""
    assert rec["agent_run_status"] is None
