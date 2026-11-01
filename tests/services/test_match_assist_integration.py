"""匹配增强接入 + confirm/reject 不触碰 agent_runs 集成测试

覆盖：
- 开关开 + LLM 可用 → 失败落 agent_runs(pending) + trace 补 llm_assist step（persist 前）
- 无候选也落任务
- 开关关 → 原失败逻辑完全不变（无 agent_runs 调用、无 trace step）
- LLM 配置缺失 → 不落任务 + 日志含 "LLM 配置缺失"
- 入队决策：enqueue_match_run 返回 dict（decision/run_id），orchestrator 记录 decision
- confirm/reject 候选不再改写 run 状态（用户结果由 pending_candidates 承载）
- 不带 llm_subject_id 的既有 confirm → 原逻辑闭环
- API：confirm 端点接收可选 llm_subject_id 并优先使用
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.models.sync import CustomItem
from app.services.sync_service import SyncService
from app.services.sync_service.match_trace import MatchTrace
from app.services.sync_service.orchestrator import (
    MATCH_ASSIST_MAX_TOTAL_ATTEMPTS,
    MATCH_ASSIST_REUSE_WINDOW_DAYS,
    SyncOrchestrator,
)

# ----------------------------------------------------------------------
# 测试夹具
# ----------------------------------------------------------------------


def _make_item() -> CustomItem:
    return CustomItem(
        media_type="episode",
        title="测试番剧",
        season=1,
        episode=1,
        release_date="2024",
        user_name="tester",
        sync_action="mark_watching",
    )


def _make_trace(with_candidates: bool = False) -> MatchTrace:
    trace = MatchTrace(
        request_title="测试番剧",
        request_ori_title="",
        request_season=1,
        request_episode=1,
    )
    if with_candidates:
        from app.services.sync_service.match_trace import MatchCandidate

        step = trace.start_step("api_search")
        step.status = "miss"
        step.candidates = [MatchCandidate(subject_id="111", name="某番")]
    return trace


def _make_orchestrator() -> SyncOrchestrator:
    sync = MagicMock()
    sync._format_subject_not_found_message.return_value = "未找到匹配的番剧"
    return SyncOrchestrator(sync)


def _make_orchestrator_agent_runs(
    *, decision: str = "created", run_id: str = "generated-run"
) -> MagicMock:
    """构造带 enqueue_match_run 返回值的 agent_runs mock"""
    agent_runs = MagicMock()
    agent_runs.enqueue_match_run.return_value = {"decision": decision, "run_id": run_id}
    return agent_runs


def _assist_steps(steps: list) -> list:
    """从 trace steps 中筛出 llm_assist step"""
    return [s for s in steps if s["stage"] == "llm_assist"]


def _capturing_persist(store: dict, record_id: int = 123):
    """返回捕获 persist 时刻 trace 序列化结果的 fake_persist"""

    def fake_persist(t, *a, **k):
        store["steps"] = t.to_dict()["steps"]
        return record_id

    return fake_persist


# ----------------------------------------------------------------------
# _handle_match_failure 接入 + 去重
# ----------------------------------------------------------------------


@pytest.mark.parametrize("with_candidates", [True, False])
@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_handle_match_failure_enqueues_when_enabled(
    mock_db, mock_cfg, mock_notify, with_candidates
):
    """开关开 + LLM 可用 → 调 enqueue_match_run（业务键 + accepted_mapping_valid）

    无候选也落任务；trace step 必须在 _persist_sync_record 之前（persist 被
    mock，其序列化结果即被视为 persist 时刻的 trace 状态）。
    """
    orch = _make_orchestrator()
    agent_runs = MagicMock()
    agent_runs.enqueue_match_run.return_value = {
        "decision": "created",
        "run_id": "generated-run",
    }
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": True}
    mock_cfg.get_llm_config.return_value = {
        "api_key": "sk-test",
        "provider": "openai_compat",
    }
    mock_db.agent_runs = agent_runs

    item = _make_item()
    trace = _make_trace(with_candidates=with_candidates)
    captured: dict = {}

    def fake_persist(t, *a, **k):
        captured["steps"] = t.to_dict()["steps"]
        return 123

    with (
        patch.object(orch, "_persist_sync_record", side_effect=fake_persist),
        patch("app.services.mapping_service.mapping_service") as mock_mapping,
    ):
        mock_mapping.find_mapping.return_value = ("", "", "")
        orch._handle_match_failure(item, "plex", trace, "err", [""])

    agent_runs.enqueue_match_run.assert_called_once()
    kwargs = agent_runs.enqueue_match_run.call_args.kwargs
    # 入队前移：persist 之前入队，尚无 sync_record_id
    assert kwargs["sync_record_id"] is None
    assert kwargs["run_id"]
    assert kwargs["business_key"]  # 业务键非空
    # 决策策略参数显式传入（无默认值兜底）
    assert kwargs["reuse_window_days"] == MATCH_ASSIST_REUSE_WINDOW_DAYS
    assert kwargs["max_total_attempts"] == MATCH_ASSIST_MAX_TOTAL_ATTEMPTS
    assert kwargs["accepted_mapping_valid"] is False  # 映射未命中 → 无效
    # trace step 在 persist 前已存在，reason 与实际入队成功语义一致
    assert any(
        s["stage"] == "llm_assist"
        and s["status"] == "pending"
        and s["reason"] == "已提交 AI 评估"
        for s in captured["steps"]
    )
    # persist 后写关联表 + 刷新主指针（created 回填 sync_record_id）
    agent_runs.add_run_sync_record_link.assert_called_once_with(
        "generated-run", 123, "created"
    )
    agent_runs.update_run_sync_record_id.assert_called_once_with("generated-run", 123)


@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_handle_match_failure_switch_off_no_enqueue(mock_db, mock_cfg, mock_notify):
    """开关关 → 原失败逻辑完全不变（无 agent_runs 调用、无 trace step）"""
    orch = _make_orchestrator()
    agent_runs = MagicMock()
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": False}
    mock_cfg.get_llm_config.return_value = {"api_key": "sk-test"}
    mock_db.agent_runs = agent_runs

    item = _make_item()
    trace = _make_trace(with_candidates=True)

    with patch.object(orch, "_persist_sync_record", return_value=123):
        orch._handle_match_failure(item, "plex", trace, "err", [""])

    agent_runs.enqueue_match_run.assert_not_called()
    assert not any(s["stage"] == "llm_assist" for s in trace.to_dict()["steps"])


@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_handle_match_failure_llm_missing_no_enqueue(
    mock_db, mock_cfg, mock_notify, capsys
):
    """LLM 配置缺失 → 不落任务 + 日志含 'LLM 配置缺失'"""
    orch = _make_orchestrator()
    agent_runs = MagicMock()
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": True}
    mock_cfg.get_llm_config.return_value = {"api_key": ""}
    mock_db.agent_runs = agent_runs

    item = _make_item()
    trace = _make_trace(with_candidates=True)

    with patch.object(orch, "_persist_sync_record", return_value=123):
        orch._handle_match_failure(item, "plex", trace, "err", [""])

    agent_runs.enqueue_match_run.assert_not_called()
    assert "LLM 配置缺失" in capsys.readouterr().out


@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_enqueue_decision_in_flight_logged(mock_db, mock_cfg, mock_notify, capsys):
    """入队返回 in_flight → 记录 decision 与复用的 run_id，不报错"""
    orch = _make_orchestrator()
    agent_runs = MagicMock()
    agent_runs.enqueue_match_run.return_value = {
        "decision": "in_flight",
        "run_id": "existing-run",
    }
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": True}
    mock_cfg.get_llm_config.return_value = {"api_key": "sk"}
    mock_db.agent_runs = agent_runs

    item = _make_item()
    trace = _make_trace()

    with (
        patch.object(orch, "_persist_sync_record", return_value=123),
        patch("app.services.mapping_service.mapping_service") as mock_mapping,
    ):
        mock_mapping.find_mapping.return_value = ("", "", "")
        orch._handle_match_failure(item, "plex", trace, "err", [""])

    agent_runs.enqueue_match_run.assert_called_once()
    out = capsys.readouterr().out
    assert "decision=in_flight" in out
    assert "existing-run" in out


@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_enqueue_decision_reuse_holding_logged(mock_db, mock_cfg, mock_notify, capsys):
    """入队返回 reuse_holding（候选等待用户处理）→ 记录 decision，不新建 run"""
    orch = _make_orchestrator()
    agent_runs = MagicMock()
    agent_runs.enqueue_match_run.return_value = {
        "decision": "reuse_holding",
        "run_id": "holding-run",
    }
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": True}
    mock_cfg.get_llm_config.return_value = {"api_key": "sk"}
    mock_db.agent_runs = agent_runs

    item = _make_item()
    trace = _make_trace()

    with (
        patch.object(orch, "_persist_sync_record", return_value=123),
        patch("app.services.mapping_service.mapping_service") as mock_mapping,
    ):
        mock_mapping.find_mapping.return_value = ("", "", "")
        orch._handle_match_failure(item, "plex", trace, "err", [""])

    agent_runs.enqueue_match_run.assert_called_once()
    assert "reuse_holding" in capsys.readouterr().out


# ----------------------------------------------------------------------
# 入队前移 + trace 记录实际 run_id + run↔record 关联 + 文案
# ----------------------------------------------------------------------


@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_trace_records_actual_new_run_id_when_created(mock_db, mock_cfg, mock_notify):
    """created → persist 的 trace 里 llm_assist.step.run_id == 实际新建 run

    入队前移：enqueue 在 persist 之前调用，此时尚无 sync_record_id。
    """
    orch = _make_orchestrator()
    agent_runs = MagicMock()
    # created 语义：仓库回显传入的 run_id
    agent_runs.enqueue_match_run.side_effect = lambda **kw: {
        "decision": "created",
        "run_id": kw["run_id"],
    }
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": True}
    mock_cfg.get_llm_config.return_value = {"api_key": "sk"}
    mock_db.agent_runs = agent_runs

    item = _make_item()
    trace = _make_trace()
    captured: dict = {}

    with (
        patch.object(
            orch, "_persist_sync_record", side_effect=_capturing_persist(captured)
        ),
        patch("app.services.mapping_service.mapping_service") as mock_mapping,
    ):
        mock_mapping.find_mapping.return_value = ("", "", "")
        orch._handle_match_failure(item, "plex", trace, "err", [""])

    kwargs = agent_runs.enqueue_match_run.call_args.kwargs
    assert kwargs["run_id"]
    # 入队前移：persist 尚未发生，不携带 sync_record_id
    assert kwargs["sync_record_id"] is None
    assist = _assist_steps(captured["steps"])
    assert len(assist) == 1
    assert assist[0]["processed_payload"] == {
        "run_id": kwargs["run_id"],
        "decision": "created",
    }


@pytest.mark.parametrize("decision", ["in_flight", "reuse_holding"])
@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_trace_records_actual_reused_run_id(mock_db, mock_cfg, mock_notify, decision):
    """复用/在途 → trace 记录的是实际承担评估的已有 run B（非预生成 id）"""
    orch = _make_orchestrator()
    agent_runs = _make_orchestrator_agent_runs(decision=decision, run_id="run-B")
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": True}
    mock_cfg.get_llm_config.return_value = {"api_key": "sk"}
    mock_db.agent_runs = agent_runs

    item = _make_item()
    trace = _make_trace()
    captured: dict = {}

    with (
        patch.object(
            orch, "_persist_sync_record", side_effect=_capturing_persist(captured)
        ),
        patch("app.services.mapping_service.mapping_service") as mock_mapping,
    ):
        mock_mapping.find_mapping.return_value = ("", "", "")
        orch._handle_match_failure(item, "plex", trace, "err", [""])

    assist = _assist_steps(captured["steps"])
    assert len(assist) == 1
    assert assist[0]["processed_payload"]["run_id"] == "run-B"
    assert assist[0]["processed_payload"]["decision"] == decision


@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
def test_run_link_and_pointer_written_after_persist(mock_cfg, mock_notify, tmp_path):
    """persist 后写关联表 + 刷新 run 主指针 sync_record_id（真实 DB）"""
    from app.core.database import DatabaseManager

    dbm = DatabaseManager(str(tmp_path / "t6_link.db"))
    try:
        orch = _make_orchestrator()
        mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": True}
        mock_cfg.get_llm_config.return_value = {"api_key": "sk"}

        item = _make_item()
        trace = _make_trace()

        with (
            patch("app.services.sync_service.database_manager", dbm),
            patch.object(orch, "_persist_sync_record", return_value=777),
            patch("app.services.mapping_service.mapping_service") as mock_mapping,
        ):
            mock_mapping.find_mapping.return_value = ("", "", "")
            orch._handle_match_failure(item, "plex", trace, "err", [""])

        conn = dbm._connection._conn
        assert conn is not None
        rows = conn.execute(
            "SELECT run_id, sync_record_id, decision FROM agent_run_sync_records"
        ).fetchall()
        assert len(rows) == 1
        run_id, record_id, decision = rows[0]
        assert record_id == 777
        assert decision == "created"
        run = dbm.agent_runs.get_run(run_id)
        assert run is not None
        assert run["sync_record_id"] == 777
    finally:
        final_conn = dbm._connection._conn
        assert final_conn is not None
        final_conn.close()


@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_llm_assist_step_after_result_step(mock_db, mock_cfg, mock_notify):
    """llm_assist step 出现在 result 之后，且通过公开接口挂入"""
    orch = _make_orchestrator()
    agent_runs = _make_orchestrator_agent_runs()
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": True}
    mock_cfg.get_llm_config.return_value = {"api_key": "sk"}
    mock_db.agent_runs = agent_runs

    item = _make_item()
    trace = _make_trace()
    captured: dict = {}

    with (
        patch.object(
            orch, "_persist_sync_record", side_effect=_capturing_persist(captured)
        ),
        patch("app.services.mapping_service.mapping_service") as mock_mapping,
    ):
        mock_mapping.find_mapping.return_value = ("", "", "")
        orch._handle_match_failure(item, "plex", trace, "err", [""])

    stages = [s["stage"] for s in captured["steps"]]
    assert "result" in stages and "llm_assist" in stages
    assert stages.index("llm_assist") > stages.index("result")


@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_existing_llm_assist_step_not_reenqueued(mock_db, mock_cfg, mock_notify):
    """trace 已含 llm_assist（异常重入）→ 不重复 enqueue、不重复挂 step、不写关联"""
    orch = _make_orchestrator()
    agent_runs = _make_orchestrator_agent_runs()
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": True}
    mock_cfg.get_llm_config.return_value = {"api_key": "sk"}
    mock_db.agent_runs = agent_runs

    item = _make_item()
    trace = _make_trace()
    step = trace.start_step("llm_assist")
    step.status = "pending"
    step.processed_payload = {"run_id": "old-run", "decision": "created"}
    trace.finish()
    captured: dict = {}

    with patch.object(
        orch, "_persist_sync_record", side_effect=_capturing_persist(captured)
    ):
        orch._handle_match_failure(item, "plex", trace, "err", [""])

    agent_runs.enqueue_match_run.assert_not_called()
    agent_runs.add_run_sync_record_link.assert_not_called()
    assist = _assist_steps(captured["steps"])
    assert len(assist) == 1
    assert assist[0]["processed_payload"]["run_id"] == "old-run"


@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_message_and_notify_mention_ai_assist_when_enabled(
    mock_db, mock_cfg, mock_notify
):
    """启用时 SyncResponse.message 与 anime_not_found 通知含启用说明"""
    orch = _make_orchestrator()
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": True}
    mock_cfg.get_llm_config.return_value = {"api_key": "sk"}
    mock_db.agent_runs = _make_orchestrator_agent_runs()

    item = _make_item()
    trace = _make_trace()

    with (
        patch.object(orch, "_persist_sync_record", return_value=123),
        patch("app.services.mapping_service.mapping_service") as mock_mapping,
    ):
        mock_mapping.find_mapping.return_value = ("", "", "")
        resp = orch._handle_match_failure(item, "plex", trace, "err", [""])

    assert "已启用 AI 匹配评估" in resp.message
    call = mock_notify.notify.call_args
    assert call.args[0] == "anime_not_found"
    assert "已启用 AI 匹配评估" in call.kwargs["error_message"]


@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_message_plain_when_assist_disabled(mock_db, mock_cfg, mock_notify):
    """未启用时保持原文案，不含启用说明"""
    orch = _make_orchestrator()
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": False}
    mock_cfg.get_llm_config.return_value = {"api_key": "sk"}
    mock_db.agent_runs = _make_orchestrator_agent_runs()

    item = _make_item()
    trace = _make_trace()

    with patch.object(orch, "_persist_sync_record", return_value=123):
        resp = orch._handle_match_failure(item, "plex", trace, "err", [""])

    assert resp.message == "未找到匹配的番剧"
    assert "已启用 AI 匹配评估" not in resp.message
    assert mock_notify.notify.call_args.kwargs["error_message"] == "未找到匹配的番剧"


@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_enqueue_exception_degrades_without_blocking(
    mock_db, mock_cfg, mock_notify, capsys
):
    """enqueue 抛异常 → 主流程正常返回 error、warning 日志、decision=enqueue_failed、不写关联"""
    orch = _make_orchestrator()
    agent_runs = MagicMock()
    agent_runs.enqueue_match_run.side_effect = RuntimeError("db down")
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": True}
    mock_cfg.get_llm_config.return_value = {"api_key": "sk"}
    mock_db.agent_runs = agent_runs

    item = _make_item()
    trace = _make_trace()
    captured: dict = {}

    with patch.object(
        orch, "_persist_sync_record", side_effect=_capturing_persist(captured)
    ):
        resp = orch._handle_match_failure(item, "plex", trace, "err", [""])

    assert resp.status == "error"
    assist = _assist_steps(captured["steps"])
    assert len(assist) == 1
    assert assist[0]["processed_payload"]["decision"] == "enqueue_failed"
    assert assist[0]["processed_payload"]["run_id"]
    # 降级分支 reason 不得再谎报"已提交"，须与 decision 语义一致
    assert assist[0]["reason"] == "评估任务入队失败"
    agent_runs.add_run_sync_record_link.assert_not_called()
    agent_runs.update_run_sync_record_id.assert_not_called()
    assert "匹配增强任务入队失败" in capsys.readouterr().out


# ----------------------------------------------------------------------
# confirm / reject 不再触碰 agent_runs
# ----------------------------------------------------------------------


def _patch_db_for_confirm(record: dict, agent_runs: MagicMock) -> MagicMock:
    db = MagicMock()
    db.agent_runs = agent_runs
    db.get_pending_candidate_by_id.return_value = record
    db.update_pending_candidate_status.return_value = True
    db.resolve_similar_pending_candidates.return_value = None
    return db


def test_linkage_methods_removed():
    """业务联动方法已删除（用户处理结果由 pending_candidates 承载）"""
    assert not hasattr(SyncService, "_linkage_mark_applied")
    assert not hasattr(SyncService, "_linkage_mark_rejected")


def test_confirm_does_not_touch_agent_runs():
    """候选确认 → 写映射 + 更新候选状态，不再改写 agent_runs"""
    svc = SyncService()
    record = {
        "id": 5,
        "status": "pending",
        "request_title": "测试番",
        "request_season": 1,
        "user_name": "u1",
        "source": "plex",
        "sync_record_id": 7,
    }
    agent_runs = MagicMock()
    db = _patch_db_for_confirm(record, agent_runs)

    with (
        patch("app.services.sync_service.database_manager", db),
        patch.object(svc, "_validate_subject_id", return_value=(True, "")),
        patch(
            "app.services.sync_service.mapping_service.upsert_single_mapping",
            return_value=True,
        ) as mock_upsert,
        patch.object(svc, "_auto_replay_after_confirm", return_value=""),
    ):
        ok, msg = svc.confirm_pending_candidate(5, "999")

    assert ok is True
    # 映射以确认的主体写入（允许列表外 subject_id）
    mock_upsert.assert_called_once_with("测试番", "999", 1)
    # 用户处理结果由 pending_candidates 承载
    db.update_pending_candidate_status.assert_called_once()
    # run 状态不再被联动改写
    agent_runs.find_active_by_sync_record.assert_not_called()
    agent_runs.mark_applied.assert_not_called()
    agent_runs.update_run_status.assert_not_called()


def test_reject_does_not_touch_agent_runs():
    """候选忽略 → 更新候选状态，不触碰 agent_runs"""
    svc = SyncService()
    record = {
        "id": 8,
        "status": "pending",
        "request_title": "测试番",
        "request_season": 1,
        "user_name": "u1",
        "source": "plex",
        "sync_record_id": 10,
    }
    agent_runs = MagicMock()
    db = MagicMock()
    db.agent_runs = agent_runs
    db.get_pending_candidate_by_id.return_value = record
    db.update_pending_candidate_status.return_value = True

    with patch("app.services.sync_service.database_manager", db):
        ok, msg = svc.reject_pending_candidate(8)

    assert ok is True
    db.update_pending_candidate_status.assert_called_once_with(8, "rejected")
    agent_runs.find_active_by_sync_record.assert_not_called()
    agent_runs.mark_rejected.assert_not_called()


def test_confirm_legacy_without_llm_subject_still_closes():
    """不带关联 run 的既有 confirm → 原逻辑闭环，向后兼容"""
    svc = SyncService()
    record = {
        "id": 10,
        "status": "pending",
        "request_title": "测试番",
        "request_season": 1,
        "user_name": "u1",
        "source": "plex",
        # 无 sync_record_id：纯手动流
    }
    agent_runs = MagicMock()
    db = _patch_db_for_confirm(record, agent_runs)

    with (
        patch("app.services.sync_service.database_manager", db),
        patch.object(svc, "_validate_subject_id", return_value=(True, "")),
        patch(
            "app.services.sync_service.mapping_service.upsert_single_mapping",
            return_value=True,
        ) as mock_upsert,
        patch.object(svc, "_auto_replay_after_confirm", return_value=""),
    ):
        ok, msg = svc.confirm_pending_candidate(10, "123")

    assert ok is True
    mock_upsert.assert_called_once_with("测试番", "123", 1)
    agent_runs.mark_applied.assert_not_called()


# ----------------------------------------------------------------------
# API：confirm 端点接收可选 llm_subject_id
# ----------------------------------------------------------------------


def _build_sync_app():
    application = FastAPI()
    from app.api import sync as sync_module

    application.include_router(sync_module.router)
    return application


async def test_confirm_endpoint_uses_llm_subject_id():
    """API：body 含 llm_subject_id 时优先作为确认 subject_id 传入"""
    from app.api import deps, sync as sync_module

    captured: dict = {}

    def fake_confirm(candidate_id, subject_id):
        captured["subject_id"] = subject_id
        return True, "ok"

    app = _build_sync_app()
    with patch.object(sync_module, "sync_service") as svc:
        svc.confirm_pending_candidate.side_effect = fake_confirm

        async def _user(request=None, credentials=None):
            return {"id": 1, "username": "u", "is_admin": True}

        app.dependency_overrides[deps.get_current_user_flexible] = _user
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/pending-candidates/1/confirm",
                json={"llm_subject_id": "888", "subject_id": "111"},
            )

    assert resp.status_code == 200
    assert captured["subject_id"] == "888"


async def test_confirm_endpoint_falls_back_to_subject_id():
    """API：body 无 llm_subject_id 时沿用候选列表内选择的 subject_id（向后兼容）"""
    from app.api import deps, sync as sync_module

    captured: dict = {}

    def fake_confirm(candidate_id, subject_id):
        captured["subject_id"] = subject_id
        return True, "ok"

    app = _build_sync_app()
    with patch.object(sync_module, "sync_service") as svc:
        svc.confirm_pending_candidate.side_effect = fake_confirm

        async def _user(request=None, credentials=None):
            return {"id": 1, "username": "u", "is_admin": True}

        app.dependency_overrides[deps.get_current_user_flexible] = _user
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/pending-candidates/1/confirm", json={"subject_id": "222"}
            )

    assert resp.status_code == 200
    assert captured["subject_id"] == "222"


@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_candidate_persist_failure_updates_message(mock_db, mock_cfg, mock_notify):
    """候选写入失败：返回 message 明确提示，而不是普通未匹配。"""
    from app.services.sync_service.errors import PendingCandidatePersistError

    orch = _make_orchestrator()
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": False}
    mock_cfg.get_llm_config.return_value = {"api_key": ""}
    mock_db.agent_runs = _make_orchestrator_agent_runs()

    item = _make_item()
    trace = _make_trace()

    with (
        patch.object(orch, "_persist_sync_record", return_value=123),
        patch.object(
            orch._sync,
            "_sediment_pending_candidate",
            side_effect=PendingCandidatePersistError(123, "db error"),
        ),
    ):
        resp = orch._handle_match_failure(item, "plex", trace, "err", [""])

    assert resp.status == "error"
    assert "候选保存失败" in resp.message


@patch("app.services.sync_service.notification_service")
@patch("app.services.sync_service.config_manager")
@patch("app.services.sync_service.database_manager")
def test_candidate_contract_error_keeps_plain_message(mock_db, mock_cfg, mock_notify):
    """契约校验失败：候选已存在，message 保持普通未匹配。"""
    from app.services.sync_service.errors import PendingCandidateContractError

    orch = _make_orchestrator()
    mock_cfg.get_sync_llm_match_config.return_value = {"llm_match_assist": False}
    mock_cfg.get_llm_config.return_value = {"api_key": ""}
    mock_db.agent_runs = _make_orchestrator_agent_runs()

    item = _make_item()
    trace = _make_trace()

    with (
        patch.object(orch, "_persist_sync_record", return_value=123),
        patch.object(
            orch._sync,
            "_sediment_pending_candidate",
            side_effect=PendingCandidateContractError("reuse_holding", 123),
        ),
    ):
        resp = orch._handle_match_failure(item, "plex", trace, "err", [""])

    assert resp.status == "error"
    assert "候选保存失败" not in resp.message
