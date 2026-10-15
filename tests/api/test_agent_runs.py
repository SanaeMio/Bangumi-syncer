"""
Agent 追踪 API 测试

覆盖：
- 未认证请求 → 401
- 非本人/非管理员访问他人 run → 404（与不存在统一，不暴露存在性）+ 审计日志
- 本人访问 → 200 + 字段完整
- 管理员可访问他人 run → 200
- 不存在 run → 404 + 审计日志
- /steps 按 (iteration, sequence) 排序，且不含 replay_delta / payload_json
- display_json：llm_chat 截断、tool 预览、seed 条数、空 delta 容错
- 时间字段 epoch → ISO 8601；0/None → None
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api import agent_runs as agent_runs_module, deps
from app.utils.truncate import SHRINKED_MARKER


@pytest.fixture
def app():
    """仅挂载 agent_runs router 的最小应用（鉴权依赖由各用例覆盖）"""
    application = FastAPI()
    application.include_router(agent_runs_module.router)
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def mock_db(app):
    """mock database_manager（agent_runs repo + sync_records 查询）"""
    with patch.object(agent_runs_module, "database_manager") as mock_dm:
        mock_dm.agent_runs = MagicMock()
        mock_dm.sync_records = MagicMock()
        yield mock_dm


def _client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _override_user(app, user):
    async def _fake(request=None, credentials=None):
        return user

    app.dependency_overrides[deps.get_current_user_flexible] = _fake


async def test_get_agent_run_unauthenticated_returns_401(app, mock_db):
    """未认证（依赖直接抛 401）→ 401"""

    async def _raise_401(request=None, credentials=None):
        raise HTTPException(status_code=401, detail="未提供有效的认证信息")

    app.dependency_overrides[deps.get_current_user_flexible] = _raise_401

    async with _client(app) as client:
        resp = await client.get("/api/agent/runs/run-xyz")

    assert resp.status_code == 401


def _other_owner_run():
    """run-1 归属 bob 的最小元数据（当前用户 alice 无权访问）"""
    return {
        "run_id": "run-1",
        "task_type": "match",
        "sync_record_id": 42,
        "status": "succeeded",
        "stop_reason": "submit_suggestion",
        "attempts": 1,
        "total_attempts": 1,
        "total_tokens": 1234,
        "started_at": "2026-01-01 00:00:00",
        "ended_at": "2026-01-01 00:01:00",
        "created_at": "2026-01-01 00:00:00",
        "last_error": "",
    }


async def test_get_agent_run_other_user_returns_404_and_logs(app, mock_db):
    """非本人/非管理员访问他人 run → 404（与不存在同响应）+ 审计日志"""
    mock_db.agent_runs.get_run.return_value = _other_owner_run()
    # sync_record 42 归属 bob，当前用户是 alice（非管理员）
    mock_db.sync_records.get_sync_record_by_id.return_value = {"user_name": "bob"}

    await _override_user(app, {"username": "alice"})

    with patch.object(agent_runs_module, "logger") as mock_logger:
        async with _client(app) as client:
            resp = await client.get("/api/agent/runs/run-1")

    assert resp.status_code == 404
    # 与「不存在」同文案，外部无法区分
    assert resp.json()["detail"] == "追踪记录不存在"
    mock_logger.warning.assert_called_once()
    msg = str(mock_logger.warning.call_args.args[0])
    assert "run-1" in msg
    assert "alice" in msg
    assert "forbidden" in msg
    assert "bob" in msg


async def test_get_agent_run_steps_other_user_returns_404_and_logs(app, mock_db):
    """非本人/非管理员访问他人 run 的 steps → 404（与不存在同响应）+ 审计日志"""
    mock_db.agent_runs.get_run.return_value = _other_owner_run()
    mock_db.sync_records.get_sync_record_by_id.return_value = {"user_name": "bob"}

    await _override_user(app, {"username": "alice"})

    with patch.object(agent_runs_module, "logger") as mock_logger:
        async with _client(app) as client:
            resp = await client.get("/api/agent/runs/run-1/steps")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "追踪记录不存在"
    mock_logger.warning.assert_called_once()
    msg = str(mock_logger.warning.call_args.args[0])
    assert "run-1" in msg
    assert "alice" in msg
    assert "forbidden" in msg
    # 无权时不查询 steps
    mock_db.agent_runs.get_steps.assert_not_called()


async def test_get_agent_run_owner_allowed_200(app, mock_db):
    """本人访问 → 200 + 字段完整 + 时间 ISO 化"""
    run = {
        "run_id": "run-1",
        "task_type": "match",
        "sync_record_id": 42,
        "status": "succeeded",
        "stop_reason": "submit_suggestion",
        "attempts": 1,
        "total_attempts": 1,
        "total_tokens": 1234,
        "started_at": 1704067200,
        "ended_at": 1704067260,
        "created_at": 1704067200,
        "last_error": "boom",
        # 不应暴露的内部字段
        "payload_json": "SECRET",
        "replay_delta": "SECRET",
    }
    mock_db.agent_runs.get_run.return_value = run
    mock_db.sync_records.get_sync_record_by_id.return_value = {"user_name": "alice"}

    await _override_user(app, {"username": "alice"})

    async with _client(app) as client:
        resp = await client.get("/api/agent/runs/run-1")

    assert resp.status_code == 200
    data = resp.json()
    # 非时间字段精确相等
    assert data["run_id"] == "run-1"
    assert data["status"] == "succeeded"
    assert data["stop_reason"] == "submit_suggestion"
    assert data["task_type"] == "match"
    assert data["sync_record_id"] == 42
    assert data["attempts"] == 1
    assert data["total_attempts"] == 1
    assert data["total_tokens"] == 1234
    assert data["last_error"] == "boom"
    # 时间字段：以固定 epoch 为地面真值，做时刻等价断言（与运行时区无关）
    assert datetime.fromisoformat(data["started_at"]) == datetime.fromtimestamp(
        1704067200, tz=timezone.utc
    )
    assert datetime.fromisoformat(data["ended_at"]) == datetime.fromtimestamp(
        1704067260, tz=timezone.utc
    )
    assert datetime.fromisoformat(data["created_at"]) == datetime.fromtimestamp(
        1704067200, tz=timezone.utc
    )
    # 内部重放字段绝不可出现在观测 API 响应
    assert "payload_json" not in data
    assert "replay_delta" not in data


async def test_get_agent_run_admin_can_access_other_200(app, mock_db):
    """管理员可访问他人 run → 200（覆盖“非管理员才被拒”）"""
    run = {
        "run_id": "run-1",
        "task_type": "match",
        "sync_record_id": 42,
        "status": "processing",
        "stop_reason": "",
        "attempts": 0,
        "total_attempts": 0,
        "total_tokens": 0,
        "started_at": None,
        "ended_at": None,
        "created_at": "2026-01-01 00:00:00",
        "last_error": "",
    }
    mock_db.agent_runs.get_run.return_value = run
    mock_db.sync_records.get_sync_record_by_id.return_value = {"user_name": "bob"}

    await _override_user(app, {"username": "admin", "is_admin": True})

    async with _client(app) as client:
        resp = await client.get("/api/agent/runs/run-1")

    assert resp.status_code == 200
    assert resp.json()["status"] == "processing"


async def test_get_agent_run_not_found_404(app, mock_db):
    """不存在 run → 404 + 审计日志（not_found）"""
    mock_db.agent_runs.get_run.return_value = None

    await _override_user(app, {"username": "alice"})

    with patch.object(agent_runs_module, "logger") as mock_logger:
        async with _client(app) as client:
            resp = await client.get("/api/agent/runs/run-missing")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "追踪记录不存在"
    mock_logger.warning.assert_called_once()
    msg = str(mock_logger.warning.call_args.args[0])
    assert "run-missing" in msg
    assert "alice" in msg
    assert "not_found" in msg


async def test_get_agent_run_steps_not_found_404(app, mock_db):
    """不存在 run 的 steps → 404 + 审计日志（not_found）"""
    mock_db.agent_runs.get_run.return_value = None

    await _override_user(app, {"username": "alice"})

    with patch.object(agent_runs_module, "logger") as mock_logger:
        async with _client(app) as client:
            resp = await client.get("/api/agent/runs/run-missing/steps")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "追踪记录不存在"
    mock_logger.warning.assert_called_once()
    msg = str(mock_logger.warning.call_args.args[0])
    assert "run-missing" in msg
    assert "alice" in msg
    assert "not_found" in msg
    mock_db.agent_runs.get_steps.assert_not_called()


async def test_get_agent_run_steps_sorted_and_no_replay_delta(app, mock_db):
    """/steps 按 (iteration, sequence) 排序，且不含 replay_delta / payload_json"""
    run = {
        "run_id": "run-1",
        "task_type": "match",
        "sync_record_id": 42,
        "status": "succeeded",
    }
    mock_db.agent_runs.get_run.return_value = run
    mock_db.sync_records.get_sync_record_by_id.return_value = {"user_name": "alice"}

    # 故意乱序返回（repo 排序与 router 排序都应保证最终有序）
    unsorted_steps = [
        {
            "span_id": "s3",
            "name": "tool_execute",
            "status": "ok",
            "model": "",
            "tokens": 0,
            "latency_ms": 5,
            "tool_name": "get_detail",
            "input_summary": "id=1",
            "error": "",
            "iteration": 1,
            "sequence": 1,
            "replay_delta": '{"kind": "tool_result", "delta_version": 1, "tool_result":{"tool_use_id":"tu-3","content":"detail ok","is_error":false}}',
            "started_at": 1704067200,
            "ended_at": 1704067201,
        },
        {
            "span_id": "s1",
            "name": "llm_chat",
            "status": "ok",
            "model": "claude",
            "tokens": 100,
            "latency_ms": 200,
            "tool_name": "",
            "input_summary": "",
            "error": "",
            "iteration": 0,
            "sequence": 0,
            "replay_delta": '{"kind": "response", "delta_version": 1, "response":{"stop_reason":"end_turn","content":"你好","tool_calls":[]}}',
            "started_at": 1704067200,
            "ended_at": 1704067202,
        },
        {
            "span_id": "s2",
            "name": "tool_execute",
            "status": "ok",
            "model": "",
            "tokens": 0,
            "latency_ms": 5,
            "tool_name": "search",
            "input_summary": "q",
            "error": "",
            "iteration": 0,
            "sequence": 1,
            "replay_delta": '{"kind": "tool_result", "delta_version": 1, "tool_result":{"tool_use_id":"tu-2","content":"搜索结果","is_error":false}}',
            "started_at": 1704067200,
            "ended_at": 1704067201,
        },
    ]
    mock_db.agent_runs.get_steps.return_value = unsorted_steps

    await _override_user(app, {"username": "alice"})

    async with _client(app) as client:
        resp = await client.get("/api/agent/runs/run-1/steps")

    assert resp.status_code == 200
    steps = resp.json()
    assert [s["span_id"] for s in steps] == ["s1", "s2", "s3"]

    # 每个 span 不应暴露 replay_delta / payload_json，但应有 display_json
    for s in steps:
        assert "replay_delta" not in s, f"replay_delta 不应出现在响应: {s}"
        assert "payload_json" not in s, f"payload_json 不应出现在响应: {s}"
        assert "display_json" in s, f"display_json 应出现在响应: {s}"
        # display_json 应是嵌套对象（非「JSON 字符串再塞进 JSON 字段」的双重编码）
        assert isinstance(s["display_json"], dict)


# ---------------------------------------------------------------------------
# display_json 行为测试
# ---------------------------------------------------------------------------


def _make_step(span_id, name, replay_delta, iteration=0, sequence=0):
    return {
        "span_id": span_id,
        "name": name,
        "status": "ok",
        "model": "",
        "tokens": 0,
        "latency_ms": 5,
        "tool_name": "",
        "input_summary": "",
        "error": "",
        "iteration": iteration,
        "sequence": sequence,
        "replay_delta": replay_delta,
        "started_at": 1704067200,
        "ended_at": 1704067201,
    }


async def _get_steps(app, mock_db, steps):
    run = {
        "run_id": "run-1",
        "task_type": "match",
        "sync_record_id": 42,
        "status": "succeeded",
    }
    mock_db.agent_runs.get_run.return_value = run
    mock_db.sync_records.get_sync_record_by_id.return_value = {"user_name": "alice"}
    mock_db.agent_runs.get_steps.return_value = steps
    await _override_user(app, {"username": "alice"})
    async with _client(app) as client:
        resp = await client.get("/api/agent/runs/run-1/steps")
    assert resp.status_code == 200
    return resp.json()


async def test_display_json_llm_chat_small_content_not_truncated(app, mock_db):
    """llm_chat 小 content → display_json 含 stop_reason + content，无 shrinked 标记"""
    big = "x" * 50
    delta = json.dumps(
        {
            "kind": "response",
            "delta_version": 1,
            "response": {"stop_reason": "end_turn", "content": big, "tool_calls": []},
        }
    )
    steps = await _get_steps(app, mock_db, [_make_step("s1", "llm_chat", delta)])

    display = steps[0]["display_json"]
    assert display["stop_reason"] == "end_turn"
    assert display["content"] == big
    assert SHRINKED_MARKER not in json.dumps(display, ensure_ascii=False)


async def test_display_json_llm_chat_oversized_content_truncated_with_marker(
    app, mock_db
):
    """llm_chat 超长 content → display_json 截断且含 shrinked 标记，仍是嵌套对象"""
    huge = "A" * 5000  # 远超 2KB
    delta = json.dumps(
        {
            "kind": "response",
            "delta_version": 1,
            "response": {"stop_reason": "end_turn", "content": huge, "tool_calls": []},
        }
    )
    steps = await _get_steps(app, mock_db, [_make_step("s1", "llm_chat", delta)])

    display = steps[0]["display_json"]
    # 截断后仍是嵌套对象
    assert isinstance(display, dict)
    serialized = json.dumps(display, ensure_ascii=False)
    # 含 shrinked 标记
    assert SHRINKED_MARKER in serialized
    # 截断后大小 ≤ 2KB（对序列化结果度量）
    assert len(serialized.encode("utf-8")) <= 2 * 1024


async def test_display_json_tool_execute_preview_fields(app, mock_db):
    """tool_execute → display_json 含 tool_use_id / content / is_error"""
    delta = json.dumps(
        {
            "kind": "tool_result",
            "delta_version": 1,
            "tool_result": {
                "tool_use_id": "tu-1",
                "content": "ok结果",
                "is_error": False,
            },
        }
    )
    steps = await _get_steps(app, mock_db, [_make_step("s1", "tool_execute", delta)])

    display = steps[0]["display_json"]
    assert display["tool_use_id"] == "tu-1"
    assert display["content"] == "ok结果"
    assert display["is_error"] is False


async def test_display_json_seed_only_count(app, mock_db):
    """seed → display_json 只给条数，不给消息内容"""
    messages = [
        {"role": "user", "content": "msg1"},
        {"role": "assistant", "content": "msg2"},
    ]
    delta = json.dumps({"kind": "seed", "delta_version": 1, "seed_messages": messages})
    steps = await _get_steps(app, mock_db, [_make_step("s1", "seed", delta)])

    display = steps[0]["display_json"]
    assert display == {"seed_messages_count": 2}
    # 原始消息内容不应泄露
    serialized = json.dumps(display, ensure_ascii=False)
    assert "msg1" not in serialized
    assert "msg2" not in serialized


async def test_display_json_empty_delta_returns_empty_object(app, mock_db):
    """空 replay_delta → display_json 为空对象，不抛异常"""
    steps = await _get_steps(app, mock_db, [_make_step("s1", "llm_chat", "")])
    assert steps[0]["display_json"] == {}


async def test_display_json_malformed_delta_returns_empty_object(app, mock_db):
    """非法 JSON replay_delta → display_json 为空对象，不抛异常"""
    steps = await _get_steps(
        app, mock_db, [_make_step("s1", "llm_chat", "{not valid json")]
    )
    assert steps[0]["display_json"] == {}


async def test_display_json_llm_chat_content_list_returns_nested_original(app, mock_db):
    """content 为 list → display_json 原样返回嵌套列表（不 500、不字符串化）。"""
    segments = [{"type": "text", "text": "分段文本"}]
    delta = json.dumps(
        {
            "kind": "response",
            "delta_version": 1,
            "response": {
                "stop_reason": "end_turn",
                "content": segments,
                "tool_calls": [],
            },
        }
    )
    steps = await _get_steps(app, mock_db, [_make_step("s1", "llm_chat", delta)])

    display = steps[0]["display_json"]
    assert display["stop_reason"] == "end_turn"
    assert display["content"] == segments


async def test_display_json_llm_chat_content_object_returns_nested_original(
    app, mock_db
):
    """content 为对象（dict）→ display_json 原样返回嵌套对象。"""
    content = {"blocks": ["a", "b"], "n": 2}
    delta = json.dumps(
        {
            "kind": "response",
            "delta_version": 1,
            "response": {"stop_reason": "end_turn", "content": content},
        }
    )
    steps = await _get_steps(app, mock_db, [_make_step("s1", "llm_chat", delta)])

    assert steps[0]["display_json"]["content"] == content


async def test_display_json_no_truncatable_str_field_returns_empty_and_warns(
    app, mock_db
):
    """超限预览中字段全为非 str（无可截断字符串）→ 返回 {} 且记 warning。"""
    # stop_reason 非 str（int）+ content 为超大 dict：两个字段都不可按字符串截断
    delta = json.dumps(
        {
            "kind": "response",
            "delta_version": 1,
            "response": {"stop_reason": 123, "content": {"blob": "y" * 5000}},
        }
    )
    with patch.object(agent_runs_module, "logger") as mock_logger:
        steps = await _get_steps(app, mock_db, [_make_step("s1", "llm_chat", delta)])

    assert steps[0]["display_json"] == {}
    mock_logger.warning.assert_called_once()
    assert "无可截断字符串字段" in str(mock_logger.warning.call_args.args[0])


async def test_display_json_truncation_not_converging_returns_empty_and_warns(
    app, mock_db
):
    """仅有的 str 字段截断至空后仍超限（巨大非 str 负载）→ 收敛失败返回 {} 且记 warning。"""
    delta = json.dumps(
        {
            "kind": "response",
            "delta_version": 1,
            "response": {"stop_reason": "x" * 100, "content": {"blob": "y" * 5000}},
        }
    )
    with patch.object(agent_runs_module, "logger") as mock_logger:
        steps = await _get_steps(app, mock_db, [_make_step("s1", "llm_chat", delta)])

    assert steps[0]["display_json"] == {}
    # 多轮截断仍超限（非「无可截断」分支），走收敛失败分支
    mock_logger.warning.assert_called_once()
    assert "截断多轮后仍超限" in str(mock_logger.warning.call_args.args[0])


# ---------------------------------------------------------------------------
# 时间字段 ISO 化测试
# ---------------------------------------------------------------------------


async def test_run_timestamps_epoch_to_iso(app, mock_db):
    """agent_runs 时间字段 epoch → ISO 8601 字符串"""
    run = {
        "run_id": "run-1",
        "task_type": "match",
        "sync_record_id": 42,
        "status": "succeeded",
        "stop_reason": "submit_suggestion",
        "attempts": 1,
        "total_attempts": 1,
        "total_tokens": 1234,
        "started_at": 1704067200,
        "ended_at": 1704067260,
        "created_at": 1704067200,
        "last_error": "",
    }
    mock_db.agent_runs.get_run.return_value = run
    mock_db.sync_records.get_sync_record_by_id.return_value = {"user_name": "alice"}
    await _override_user(app, {"username": "alice"})

    async with _client(app) as client:
        resp = await client.get("/api/agent/runs/run-1")

    assert resp.status_code == 200
    data = resp.json()
    # ISO 格式断言：含 T、含时区偏移
    assert "T" in data["started_at"]
    assert "+" in data["started_at"] or "Z" in data["started_at"]
    # 机器可读 API 统一 UTC 表示（+00:00 结尾）
    assert data["started_at"].endswith("+00:00")
    assert data["ended_at"].endswith("+00:00")
    assert data["created_at"].endswith("+00:00")
    # 固定 epoch → 固定时刻（与运行时区无关）
    assert datetime.fromisoformat(data["started_at"]) == datetime.fromtimestamp(
        1704067200, tz=timezone.utc
    )
    assert datetime.fromisoformat(data["ended_at"]) == datetime.fromtimestamp(
        1704067260, tz=timezone.utc
    )


async def test_run_timestamps_zero_or_none_returns_none(app, mock_db):
    """epoch 为 0/None → 返回 None"""
    run = {
        "run_id": "run-1",
        "task_type": "match",
        "sync_record_id": 42,
        "status": "processing",
        "stop_reason": "",
        "attempts": 0,
        "total_attempts": 0,
        "total_tokens": 0,
        "started_at": 0,
        "ended_at": None,
        "created_at": 0,
        "last_error": "",
    }
    mock_db.agent_runs.get_run.return_value = run
    mock_db.sync_records.get_sync_record_by_id.return_value = {"user_name": "alice"}
    await _override_user(app, {"username": "alice"})

    async with _client(app) as client:
        resp = await client.get("/api/agent/runs/run-1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["started_at"] is None
    assert data["ended_at"] is None
    assert data["created_at"] is None


async def test_steps_timestamps_epoch_to_iso(app, mock_db):
    """agent_steps 时间字段 epoch → ISO 8601"""
    delta = json.dumps(
        {
            "kind": "response",
            "delta_version": 1,
            "response": {"stop_reason": "end_turn", "content": "hi"},
        }
    )
    steps = await _get_steps(
        app, mock_db, [_make_step("s1", "llm_chat", delta, iteration=0, sequence=0)]
    )
    s = steps[0]
    assert "T" in s["started_at"]
    # 机器可读 API 统一 UTC 表示（+00:00 结尾）
    assert s["started_at"].endswith("+00:00")
    assert s["ended_at"].endswith("+00:00")
    # 固定 epoch → 固定时刻（与运行时区无关）
    assert datetime.fromisoformat(s["started_at"]) == datetime.fromtimestamp(
        1704067200, tz=timezone.utc
    )
    assert datetime.fromisoformat(s["ended_at"]) == datetime.fromtimestamp(
        1704067201, tz=timezone.utc
    )


async def test_display_json_budget_message_preview(app, mock_db):
    """budget 行 → display_json 展示预算消息文本"""
    delta = json.dumps(
        {
            "kind": "budget_message",
            "delta_version": 1,
            "budget_message": "[剩余轮次：2]",
        }
    )
    steps = await _get_steps(app, mock_db, [_make_step("s1", "budget", delta)])

    assert steps[0]["display_json"] == {"budget_message": "[剩余轮次：2]"}


# ---------------------------------------------------------------------------
# runs 列表端点（观测）
# ---------------------------------------------------------------------------


def _list_run_row(run_id="run-1", **overrides):
    row = {
        "run_id": run_id,
        "task_type": "match",
        "status": "succeeded",
        "stop_reason": "submit_suggestion",
        "sync_record_id": 42,
        "attempts": 0,
        "total_attempts": 1,
        "total_tokens": 123,
        "started_at": 1704067200,
        "ended_at": 1704067202,
        "created_at": 1704067200,
        "last_error": "",
        "replay_delta": "should-not-leak",
    }
    row.update(overrides)
    return row


async def test_list_agent_runs_structure_fields_and_time_iso(app, mock_db):
    """列表：{items,total,page,page_size}；公共字段 + ISO 时间；不泄漏 replay_delta。"""
    mock_db.agent_runs.list_runs.return_value = [
        _list_run_row("run-2"),
        _list_run_row("run-1"),
    ]
    mock_db.agent_runs.count_runs.return_value = 42

    await _override_user(app, {"username": "alice"})

    async with _client(app) as client:
        resp = await client.get("/api/agent/runs?page=2&page_size=10")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 42
    assert data["page"] == 2
    assert data["page_size"] == 10
    assert [i["run_id"] for i in data["items"]] == ["run-2", "run-1"]
    item = data["items"][0]
    assert item["status"] == "succeeded"
    assert item["created_at"] == "2024-01-01T00:00:00+00:00"
    assert "replay_delta" not in item
    # 分页透传：offset=(2-1)*10
    kwargs = mock_db.agent_runs.list_runs.call_args.kwargs
    assert kwargs["limit"] == 10
    assert kwargs["offset"] == 10


async def test_list_agent_runs_owner_filter_and_admin(app, mock_db):
    """非 admin 传 user_name 过滤；admin 不过滤（None）。"""
    mock_db.agent_runs.list_runs.return_value = []
    mock_db.agent_runs.count_runs.return_value = 0

    await _override_user(app, {"username": "alice"})
    async with _client(app) as client:
        resp = await client.get("/api/agent/runs")
    assert resp.status_code == 200
    assert mock_db.agent_runs.list_runs.call_args.kwargs["user_name"] == "alice"

    mock_db.agent_runs.list_runs.reset_mock()
    await _override_user(app, {"username": "admin", "is_admin": True})
    async with _client(app) as client:
        resp = await client.get("/api/agent/runs")
    assert resp.status_code == 200
    assert mock_db.agent_runs.list_runs.call_args.kwargs["user_name"] is None


async def test_list_agent_runs_page_size_capped_and_page_floored(app, mock_db):
    """page_size 上限 100；page<1 归正为 1；days 非正数回退默认 30。"""
    import time as _time

    mock_db.agent_runs.list_runs.return_value = []
    mock_db.agent_runs.count_runs.return_value = 0
    await _override_user(app, {"username": "alice"})

    async with _client(app) as client:
        resp = await client.get("/api/agent/runs?page=0&page_size=500&days=0")

    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1
    assert data["page_size"] == 100
    kwargs = mock_db.agent_runs.list_runs.call_args.kwargs
    assert kwargs["limit"] == 100
    assert kwargs["offset"] == 0
    # days=0 → 默认 30 天窗口（since 在未来之前、且约为 now-30d 以内）
    now = int(_time.time())
    assert now - 31 * 86400 <= kwargs["since"] <= now - 29 * 86400


async def test_list_agent_runs_non_admin_without_username_returns_empty(app, mock_db):
    """非 admin 但无 username（异常态）：返回空列表且不查库（不泄漏全量）。"""
    await _override_user(app, {"username": ""})

    async with _client(app) as client:
        resp = await client.get("/api/agent/runs")

    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}
    mock_db.agent_runs.list_runs.assert_not_called()
    mock_db.agent_runs.count_runs.assert_not_called()
