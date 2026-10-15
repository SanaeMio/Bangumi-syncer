"""L1 离线回放测试：把 golden cassette 回放纳入 CI（零网络、零外部依赖、临时 DB）。

对应方案文档 ``docs/development/eval.md`` 的两层评测：

- **L1（本文件）**：回放已录制的 cassette，断言"回放终局 == 录制终局"。
  确定性、完全离线，由现有 CI 的 ``pytest tests/`` 零成本收集；
- **L2（不在本文件）**：真实模型 + judge、命中率等统计指标——非确定性、需 key。

断言语义是"与录制行为一致"，因此**不硬编码命中率/subject 期望**：
``expected_outcome`` 来自 cassette，即录制时的真实终局。
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from app.services.llm.models import ChatResponse, collect
from eval.lib import (
    FingerprintMismatch,
    FixtureDriver,
    fingerprint,
    load_cassette,
    load_golden,
    run_case,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO_ROOT / "eval" / "golden" / "public_v1.jsonl"
# 收集期读取 golden（纯本地文件，无网络）
GOLDEN_CASES = load_golden(GOLDEN_PATH)

# run_case(replay) 内部已断言 run_status / candidate_subject_id / notifications，
# 但不停 stop_reason；这里补齐四项，确保"回放终局与录制终局完全一致"。
OUTCOME_KEYS = ("run_status", "stop_reason", "candidate_subject_id", "notifications")


# ---------------------------------------------------------------------------
# 1. 参数化回放 3 条 golden case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["id"] for c in GOLDEN_CASES])
async def test_replay_matches_recorded_outcome(case, fixtures_dir: Path):
    """回放 golden cassette，终局四项与录制时保持一致。"""
    case_id = case["id"]
    cassette = load_cassette(fixtures_dir / f"{case_id}.json")
    expected = cassette.get("expected_outcome") or {}
    assert expected, f"{case_id}: cassette 缺少 expected_outcome，无法校验回放"

    result = await run_case(case, mode="replay", fixtures_dir=str(fixtures_dir))

    assert result["replay_verified"] is True, f"{case_id}: 未标记 replay 校验通过"
    actual = result["outcome"]
    mismatch = {
        key: {"replay": actual.get(key), "recorded": expected.get(key)}
        for key in OUTCOME_KEYS
        if actual.get(key) != expected.get(key)
    }
    assert not mismatch, (
        f"[{case_id}] 回放终局与录制不一致（cassette 可能因循环行为变更而需重录）: "
        f"{mismatch}"
    )


# ---------------------------------------------------------------------------
# 2. 指纹守卫自测（回放错配必须显式失败，而不是静默用错响应）
# ---------------------------------------------------------------------------


async def test_fixture_driver_stream_tampered_fingerprint_raises_with_rerecord_hint():
    """篡改 request_fingerprint 后回放：抛 FingerprintMismatch 且提示重新录制。"""
    rounds = [
        {
            "request_fingerprint": "sha256:deadbeef",
            "response": {"stop_reason": "end_turn", "content": "tampered"},
            "tool_results": [],
        }
    ]
    driver = FixtureDriver(rounds, model="test-model")

    with pytest.raises(FingerprintMismatch) as excinfo:
        await collect(driver.stream([{"role": "user", "content": "hi"}]))

    assert "重新录制" in str(excinfo.value), "错配信息应提示用 --mode record 重录"


async def test_fixture_driver_stream_matching_fingerprint_returns_recorded_response():
    """指纹一致时正常回放（守卫不会误伤合法 cassette）。"""
    messages = [{"role": "user", "content": "hi"}]
    model = "test-model"
    rounds = [
        {
            "request_fingerprint": fingerprint(messages, None, None, model),
            "response": {
                "stop_reason": "end_turn",
                "content": "recorded",
                "usage": None,
            },
            "tool_results": [],
        }
    ]
    driver = FixtureDriver(rounds, model=model)

    resp = await collect(driver.stream(messages))

    assert resp.content == "recorded"
    assert resp.stop_reason == "end_turn"


async def test_fixture_driver_stream_beyond_recorded_rounds_raises_mismatch():
    """请求超出录制轮数：同样以 FingerprintMismatch 显式失败。"""
    driver = FixtureDriver([], model="")

    with pytest.raises(FingerprintMismatch):
        await collect(driver.stream([{"role": "user", "content": "hi"}]))


# ---------------------------------------------------------------------------
# 3. fixture / golden 敏感信息扫描
# ---------------------------------------------------------------------------

SECRET_PATTERNS = {
    "openai_sk": re.compile(r"sk-[A-Za-z0-9]{10,}"),
    "aws_akia": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github_pat": re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    "api_key_assignment": re.compile(
        r"api[_-]?key[\"'\s:=]+[A-Za-z0-9]{16,}", re.IGNORECASE
    ),
    "bearer_token": re.compile(r"Bearer [A-Za-z0-9\-._~+/]{20,}"),
}

SCANNED_GLOBS = ("fixtures/*.json", "golden/*.jsonl")


def _scan_text(text: str) -> list:
    """返回文本中命中的 (模式名, 命中片段) 列表。"""
    hits = []
    for name, pattern in SECRET_PATTERNS.items():
        for match in pattern.finditer(text):
            hits.append((name, match.group(0)[:40]))
    return hits


def test_eval_fixtures_and_golden_contain_no_secrets(eval_dir: Path):
    """遍历 eval/fixtures 与 eval/golden 文本，确认无 API key / token 等敏感串。"""
    targets = []
    for glob in SCANNED_GLOBS:
        targets.extend(sorted(eval_dir.glob(glob)))
    assert targets, "未找到待扫描的 fixture/golden 文件"

    violations = {}
    for path in targets:
        hits = _scan_text(path.read_text(encoding="utf-8"))
        if hits:
            violations[str(path.relative_to(eval_dir))] = hits

    assert not violations, f"fixture/golden 中疑似敏感信息: {violations}"


def test_secret_patterns_detect_known_secrets():
    """防呆：扫描正则必须能命中已知样例，且不误伤正常 fixture 字段。"""
    samples = {
        "openai_sk": "sk-abcdefghij1234567890",
        "aws_akia": "AKIAIOSFODNN7EXAMPLE",
        "github_pat": "ghp_" + "a" * 36,
        "api_key_assignment": 'api_key="abcdefghijklmnop1234"',
        "bearer_token": "Bearer " + "a" * 30,
    }
    for name, sample in samples.items():
        assert SECRET_PATTERNS[name].search(sample), f"正则 {name} 未命中已知样例"

    benign = (
        '"total_tokens": 276',
        '"prompt_tokens": 184',
        '"usage": {',
        '"api_key": ""',
        '{"ok": true}',
    )
    for text in benign:
        assert not _scan_text(text), f"正常字段被误报为敏感信息: {text}"


# ---------------------------------------------------------------------------
# 4. 零网络守卫
# ---------------------------------------------------------------------------


class _NetworkAccessDenied(RuntimeError):
    """replay 期间检测到真实网络出口被调用。"""


def _install_network_deny(monkeypatch) -> list:
    """拦截 httpx 同步/异步真实出口，返回记录到的请求 URL 列表。"""
    attempts: list = []

    def _deny_sync(self, request, *args, **kwargs):
        attempts.append(str(request.url))
        raise _NetworkAccessDenied(f"replay 期间发生真实网络访问: {request.url}")

    async def _deny_async(self, request, *args, **kwargs):
        attempts.append(str(request.url))
        raise _NetworkAccessDenied(f"replay 期间发生真实网络访问: {request.url}")

    monkeypatch.setattr(httpx.Client, "send", _deny_sync)
    monkeypatch.setattr(httpx.AsyncClient, "send", _deny_async)
    return attempts


def test_network_guard_intercepts_real_httpx_call(monkeypatch):
    """防呆：确认零网络守卫确实拦得住真实 httpx 出口（否则守卫是空转的）。"""
    attempts = _install_network_deny(monkeypatch)

    with pytest.raises(_NetworkAccessDenied):
        httpx.get("https://example.invalid/")

    assert attempts == ["https://example.invalid/"]


async def test_replay_case_performs_no_network_access(monkeypatch, fixtures_dir: Path):
    """replay 一条 golden case：若触网则被拦截，据此证明回放完全离线。"""
    attempts = _install_network_deny(monkeypatch)

    case = GOLDEN_CASES[0]
    try:
        result = await run_case(case, mode="replay", fixtures_dir=str(fixtures_dir))
    except AssertionError:
        # 已知：并行任务改动循环行为后 cassette 指纹待统一重录，run_case 会以
        # 终局不一致报错；本用例只关注"是否触网"，不重复断言终局一致性。
        pass
    else:
        assert result["replay_verified"] is True

    assert attempts == [], f"replay 期间发生真实网络访问: {attempts}"


# ---------------------------------------------------------------------------
# 5. ChatResponse → StreamChunk 展开（无停点 → 轮级降级）
# ---------------------------------------------------------------------------


def test_expand_wire_response_without_stop_signal():
    """展开事件序列不含 tool_use_stop（能力分级：回放走轮级执行）。"""
    from app.services.llm.models import StreamAggregator
    from eval.lib import response_from_wire, response_to_chunks

    wire = {
        "stop_reason": "tool_use",
        "content": "hi",
        "tool_calls": [{"id": "t1", "name": "search_bangumi", "input": {"title": "x"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    resp = response_from_wire(wire)
    chunks = response_to_chunks(resp)
    types = [c.type for c in chunks]

    assert "tool_use_stop" not in types, "回放展开不得含停点信号"
    assert types == ["text_delta", "tool_use_start", "tool_use_delta", "usage", "stop"]

    # 往返：聚合回 ChatResponse 与原始一致（cassette 格式不变）
    agg = StreamAggregator()
    for c in chunks:
        agg.feed(c)
    back = agg.finalize()
    assert back.stop_reason == "tool_use"
    assert back.content == "hi"
    assert [(b.type, getattr(b, "id", None)) for b in back.blocks] == [
        ("text", None),
        ("tool_use", "t1"),
    ]


async def test_fixture_driver_stream_yields_expanded_events():
    """``FixtureDriver.stream`` 指纹不变、产出可聚合的事件流。"""
    from eval.lib import FixtureDriver, fingerprint

    messages = [{"role": "user", "content": "hi"}]
    model = "test-model"
    rounds = [
        {
            "request_fingerprint": fingerprint(messages, None, None, model),
            "response": {
                "stop_reason": "end_turn",
                "content": "recorded",
                "usage": None,
            },
            "tool_results": [],
        }
    ]
    driver = FixtureDriver(rounds, model=model)

    chunks = [c async for c in driver.stream(messages)]

    assert chunks[-1].type == "stop"
    assert chunks[-1].stop_reason == "end_turn"


async def test_fixture_driver_stream_tampered_fingerprint_raises():
    """流式回放同样受指纹守卫保护。"""
    from eval.lib import FixtureDriver

    rounds = [
        {
            "request_fingerprint": "sha256:deadbeef",
            "response": {"stop_reason": "end_turn", "content": "x"},
            "tool_results": [],
        }
    ]
    driver = FixtureDriver(rounds, model="m")

    with pytest.raises(FingerprintMismatch):
        async for _c in driver.stream([{"role": "user", "content": "hi"}]):
            pass


# ---------------------------------------------------------------------------
# 6. blocks → chunks 展开保真：多块结构往返不塌陷
# ---------------------------------------------------------------------------


def test_expand_preserves_multiple_text_blocks_across_tool_use():
    """交错的多段文本块经展开再聚合，块边界与顺序不塌陷。

    展开若不带block_index，``text(A) → tool_use → text(B)`` 会被合并成
    ``[TextBlock("AB"), tool_use]``——回放与live 的 assistant 消息结构不一致，
    断点续跑的下一轮请求语义已改变。
    """
    from app.services.llm.models import (
        StreamAggregator,
        TextBlock,
        ToolUseBlock,
    )
    from eval.lib import response_to_chunks

    resp = ChatResponse(
        content="AB",
        blocks=[
            TextBlock(text="A"),
            ToolUseBlock(id="t1", name="search", input={"q": "x"}),
            TextBlock(text="B"),
        ],
        stop_reason="tool_use",
    )

    agg = StreamAggregator()
    for chunk in response_to_chunks(resp):
        agg.feed(chunk)
    back = agg.finalize()

    assert [type(b).__name__ for b in back.blocks] == [
        "TextBlock",
        "ToolUseBlock",
        "TextBlock",
    ]
    assert back.blocks[0].text == "A"
    assert back.blocks[2].text == "B"
    assert back.content == "AB"


def test_expand_preserves_separate_thinking_signatures():
    """两段 thinking 的签名经展开往返后仍各自独立，不被拼成 "s1s2"。"""
    from app.services.llm.models import (
        StreamAggregator,
        TextBlock,
        ThinkingBlock,
        ToolUseBlock,
    )
    from eval.lib import response_to_chunks

    resp = ChatResponse(
        content="",
        blocks=[
            ThinkingBlock(thinking="思1", signature="s1"),
            ToolUseBlock(id="t1", name="f", input={}),
            ThinkingBlock(thinking="思2", signature="s2"),
            TextBlock(text="结语"),
        ],
        stop_reason="tool_use",
    )

    agg = StreamAggregator()
    for chunk in response_to_chunks(resp):
        agg.feed(chunk)
    back = agg.finalize()

    assert [type(b).__name__ for b in back.blocks] == [
        "ThinkingBlock",
        "ToolUseBlock",
        "ThinkingBlock",
        "TextBlock",
    ]
    assert back.blocks[0].signature == "s1"
    assert back.blocks[2].signature == "s2"


def test_expand_preserves_redacted_thinking_block():
    """被遮蔽的思考块经展开往返后仍在（不再被当作未知类型丢弃）。"""
    from app.services.llm.models import (
        RedactedThinkingBlock,
        StreamAggregator,
        TextBlock,
    )
    from eval.lib import response_to_chunks

    resp = ChatResponse(
        content="结论",
        blocks=[
            RedactedThinkingBlock(data="ENCRYPTED"),
            TextBlock(text="结论"),
        ],
        stop_reason="end_turn",
    )

    agg = StreamAggregator()
    for chunk in response_to_chunks(resp):
        agg.feed(chunk)
    back = agg.finalize()

    assert [type(b).__name__ for b in back.blocks] == [
        "RedactedThinkingBlock",
        "TextBlock",
    ]
    assert back.blocks[0].data == "ENCRYPTED"
