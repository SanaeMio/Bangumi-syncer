"""展示层截断工具测试（truncate_json / _shrink / MAX_PAYLOAD_JSON_BYTES）。

覆盖：
- 小 payload 原样通过（合法 JSON）
- 大 payload 截断后仍合法 JSON 且 ≤ 上限
- 截断结果以 ...[shrinked] 标记结尾
- 字符串输入同样处理
"""

from __future__ import annotations

import json
from unittest.mock import patch

from app.utils.truncate import (
    MAX_PAYLOAD_JSON_BYTES,
    SHRINKED_MARKER,
    _build_truncation_shell,
    _safe_json_dumps,
    truncate_json,
    truncate_text_with_marker,
)


class TestTruncateJsonSmallPassthrough:
    def test_small_dict_passthrough(self):
        small = {"a": 1}
        out = truncate_json(small)
        assert out == '{"a": 1}'
        json.loads(out)  # 合法

    def test_small_string_passthrough(self):
        # 字符串输入原样返回（保持与 trace.truncate_json 一致的行为）
        out = truncate_json("hi")
        assert out == "hi"


class TestTruncateJsonLargeBoundedAndValid:
    def test_large_dict_truncated_to_valid_json_and_bounded(self):
        big = {"content": "x" * 5000, "meta": "keep"}
        out = truncate_json(big)
        parsed = json.loads(out)  # 必须可解析
        assert isinstance(parsed, dict)
        assert len(out.encode("utf-8")) <= MAX_PAYLOAD_JSON_BYTES

    def test_large_string_truncated_to_valid_json_and_bounded(self):
        big = "y" * 5000
        out = truncate_json(big)
        parsed = json.loads(out)  # 必须可解析
        assert isinstance(parsed, str)
        assert len(out.encode("utf-8")) <= MAX_PAYLOAD_JSON_BYTES


class TestTruncateJsonShrinkedMarker:
    def test_truncated_result_preview_ends_with_marker(self):
        """超大 payload 降级包壳：preview 字段以 ...[shrinked] 标记结尾。

        多字段大值使 _shrink 无法落入上限，强制走降级包壳路径。
        """
        huge = {f"field_{i}": "z" * 5000 for i in range(10)}
        out = truncate_json(huge)
        parsed = json.loads(out)
        assert parsed.get("truncated") is True
        preview = parsed.get("preview")
        assert isinstance(preview, str)
        assert preview.endswith(SHRINKED_MARKER)

    def test_truncated_result_is_valid_json_and_bounded(self):
        """截断结果必须是合法 JSON 且 ≤ 上限。"""
        huge = {f"field_{i}": "z" * 5000 for i in range(10)}
        out = truncate_json(huge)
        json.loads(out)  # 合法
        assert len(out.encode("utf-8")) <= MAX_PAYLOAD_JSON_BYTES

    def test_custom_max_bytes(self):
        """自定义上限同样生效。"""
        payload = "a" * 200
        out = truncate_json(payload, max_bytes=50)
        json.loads(out)
        assert len(out.encode("utf-8")) <= 50


class TestTruncateJsonDeepNesting:
    def test_deep_nested_does_not_raise_recursion_error(self):
        """1000+ 层嵌套不触发 RecursionError，返回合法 JSON。

        病态构造：内层字符串足够大，使每一层的序列化结果都超上限，
        强制 _shrink 在每一层都递归 → 无保护时触发 RecursionError。
        """
        deep = "x" * 3000
        for _ in range(1000):
            deep = {"n": deep}
        out = truncate_json(deep)
        json.loads(out)  # 必须可解析

    def test_deep_nested_returns_bounded_json(self):
        """超深嵌套截断后仍 ≤ 上限。"""
        deep = "x" * 3000
        for _ in range(1200):
            deep = {"wrap": deep}
        out = truncate_json(deep)
        assert len(out.encode("utf-8")) <= MAX_PAYLOAD_JSON_BYTES
        json.loads(out)

    def test_deep_nested_list_does_not_raise(self):
        """深层嵌套列表同样受保护。"""
        deep = ["x" * 3000]
        for _ in range(1100):
            deep = [deep]
        out = truncate_json(deep)
        json.loads(out)


def _make_recursion_error_object() -> dict:
    """构造足以让 json.dumps 触发 RecursionError 的病态深嵌套对象。"""
    deep: object = "x"
    for _ in range(20000):
        deep = {"n": deep}
    return deep  # type: ignore[return-value]


class TestTruncateFailureLogging:
    def test_safe_json_dumps_recursion_error_logs_error_and_returns_none(self):
        """序列化触发 RecursionError 时记录 error 级别日志并返回 None。"""
        deep = _make_recursion_error_object()

        with patch("app.utils.truncate.logger") as mock_logger:
            result = _safe_json_dumps(deep)

        assert result is None
        mock_logger.error.assert_called_once()
        msg = str(mock_logger.error.call_args.args[0])
        assert "RecursionError" in msg
        mock_logger.warning.assert_not_called()

    def test_build_truncation_shell_invalid_json_logs_error(self):
        """包壳拼接结果非法 JSON 时记录 error 级别日志并继续缩减预算。"""
        # 前若干个切片字符含未转义控制字符，使拼接串不是合法 JSON
        text = "\x00" * 5000

        with patch("app.utils.truncate.logger") as mock_logger:
            result = _build_truncation_shell(text, 256)

        # 最终仍返回合法包壳（预算耗尽后仅剩标记）
        json.loads(result)
        assert result.endswith(SHRINKED_MARKER + '"}')
        mock_logger.error.assert_called()
        msgs = [str(c.args[0]) for c in mock_logger.error.call_args_list if c.args]
        assert any("JSON" in m for m in msgs), msgs
        mock_logger.warning.assert_not_called()


class TestTruncateTextWithMarkerTinyBudget:
    """truncate_text_with_marker 极小预算防御分支（预算容不下完整标记）。

    正常预算（2KB）下不可达，仅在预算 ≤ 标记字节数时退化。
    """

    def test_budget_equals_marker_length_returns_full_marker_and_warns(self):
        """max_bytes == 标记字节数 → 返回完整标记并记 warning，且 ≤ 预算。"""
        marker_len = len(SHRINKED_MARKER.encode("utf-8"))

        with patch("app.utils.truncate.logger") as mock_logger:
            result = truncate_text_with_marker("x" * 100, marker_len)

        assert result == SHRINKED_MARKER
        assert len(result.encode("utf-8")) <= marker_len
        mock_logger.warning.assert_called_once()
        assert "预算过小" in str(mock_logger.warning.call_args.args[0])

    def test_budget_below_marker_length_returns_marker_prefix_and_warns(self):
        """max_bytes < 标记字节数 → 返回标记前缀片段，不抛异常且 ≤ 预算。"""
        with patch("app.utils.truncate.logger") as mock_logger:
            result = truncate_text_with_marker("x" * 100, 5)

        assert result == "...[s"
        assert len(result.encode("utf-8")) == 5
        mock_logger.warning.assert_called_once()
        assert "预算过小" in str(mock_logger.warning.call_args.args[0])

    def test_zero_budget_returns_empty_string_and_warns(self):
        """max_bytes == 0 → 返回空串并记 warning。"""
        with patch("app.utils.truncate.logger") as mock_logger:
            result = truncate_text_with_marker("x" * 100, 0)

        assert result == ""
        mock_logger.warning.assert_called_once()
        assert "预算过小" in str(mock_logger.warning.call_args.args[0])
