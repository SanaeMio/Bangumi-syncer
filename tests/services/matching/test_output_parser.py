"""app.services.matching.output_parser 测试。

结构化输出解析器：从 LLM 自由文本提取 JSON -> 校验 subject_id/reason ->
返回 (LLMSuggestion | None, 错误原因)。任何畸形/类型错误都必须以
(None, 原因) 降级，绝不抛异常。
"""

from app.services.matching.output_parser import LLMSuggestion, parse_suggestion


class TestParseSuggestionSuccess:
    """正常解析路径。"""

    def test_pure_json_success(self):
        """纯 JSON 文本可解析为 LLMSuggestion。"""
        text = '{"subject_id": "123", "reason": "标题语义相近"}'
        result, err = parse_suggestion(text)
        assert err == ""
        assert result is not None
        assert result.subject_id == "123"
        assert result.reason == "标题语义相近"

    def test_json_with_surrounding_noise(self):
        """带前后缀噪声（如"建议是：{...} 请确认"）也能提取。"""
        text = '好的，我的建议是：{"subject_id": "456", "reason": "续集判断"} 请确认'
        result, err = parse_suggestion(text)
        assert err == ""
        assert result is not None
        assert result.subject_id == "456"
        assert result.reason == "续集判断"

    def test_json_with_nested_object_noise(self):
        """首个 {...} 含嵌套对象时按平衡括号截取（不被内层 } 截断）。"""
        text = (
            '分析如下 {"subject_id": "789", '
            '"reason": "续集判断", "meta": {"score": 1}} 结束'
        )
        result, err = parse_suggestion(text)
        assert err == ""
        assert result is not None
        assert result.subject_id == "789"
        assert result.reason == "续集判断"

    def test_reason_missing_returns_empty_string(self):
        """reason 缺失时以空串成功返回（不视为失败）。"""
        text = '{"subject_id": "123"}'
        result, err = parse_suggestion(text)
        assert err == ""
        assert result is not None
        assert result.subject_id == "123"
        assert result.reason == ""


class TestParseSuggestionFailure:
    """降级路径：任何失败都返回 (None, 明确原因)，不抛异常。"""

    def test_empty_text(self):
        result, err = parse_suggestion("")
        assert result is None
        assert err == "空文本"

    def test_whitespace_only_text(self):
        result, err = parse_suggestion("   \n  ")
        assert result is None
        assert err == "空文本"

    def test_none_text(self):
        result, err = parse_suggestion(None)
        assert result is None
        assert err == "空文本"

    def test_no_json_at_all(self):
        """完全无 JSON 结构 -> JSON 提取失败。"""
        result, err = parse_suggestion("我觉得这个番组无法匹配")
        assert result is None
        assert err == "JSON 提取失败"

    def test_malformed_json_returns_none_no_raise(self):
        """截断/语法错误的 JSON -> (None, JSON 解析失败)，不抛异常。"""
        text = '{"subject_id": "123", "reason": "x"'  # 缺右括号
        result, err = parse_suggestion(text)
        assert result is None
        assert err == "JSON 解析失败"

    def test_subject_id_missing(self):
        text = '{"reason": "缺少 id"}'
        result, err = parse_suggestion(text)
        assert result is None
        assert err == "subject_id 非法"

    def test_subject_id_empty_string(self):
        text = '{"subject_id": "", "reason": "空 id"}'
        result, err = parse_suggestion(text)
        assert result is None
        assert err == "subject_id 非法"

    def test_subject_id_non_digit(self):
        text = '{"subject_id": "abc", "reason": "非数字"}'
        result, err = parse_suggestion(text)
        assert result is None
        assert err == "subject_id 非法"

    def test_subject_id_with_leading_zero_invalid(self):
        """纯数字约束 ^\\d+$，以 0 开头仍属纯数字 -> 允许（仅校验纯数字）。"""
        text = '{"subject_id": "0123", "reason": "ok"}'
        result, err = parse_suggestion(text)
        assert err == ""
        assert result is not None
        assert result.subject_id == "0123"

    def test_subject_id_int_coerced(self):
        """LLM 可能以数字形式返回 id，纯数字 int 应被容错 coerce 为字符串。"""
        text = '{"subject_id": 123, "reason": "数字 id"}'
        result, err = parse_suggestion(text)
        assert err == ""
        assert result is not None
        assert result.subject_id == "123"
        assert isinstance(result.subject_id, str)

    def test_subject_id_negative_int_invalid(self):
        text = '{"subject_id": -5, "reason": "负数"}'
        result, err = parse_suggestion(text)
        assert result is None
        assert err == "subject_id 非法"

    def test_reason_too_long(self):
        """reason 超过 200 字符 -> 拒绝（校验失败 -> no_suggestion）。"""
        long_reason = "x" * 201
        text = f'{{"subject_id": "123", "reason": "{long_reason}"}}'
        result, err = parse_suggestion(text)
        assert result is None
        assert err == "reason 超长"

    def test_reason_exactly_200_ok(self):
        """reason 恰好 200 字符 -> 通过。"""
        reason = "y" * 200
        text = f'{{"subject_id": "123", "reason": "{reason}"}}'
        result, err = parse_suggestion(text)
        assert err == ""
        assert result is not None
        assert len(result.reason) == 200

    def test_parsed_json_not_an_object(self):
        """JSON 解析成功但不是对象（如数组/字符串）-> 类型错误降级。"""
        result, err = parse_suggestion("[1, 2, 3]")
        assert result is None
        assert err == "JSON 解析失败"

    def test_reason_non_string_rejected(self):
        """reason 非字符串类型 -> 明确类型错误。"""
        text = '{"subject_id": "123", "reason": 123}'
        result, err = parse_suggestion(text)
        assert result is None
        assert err == "reason 非法"


class TestLLMSuggestion:
    """LLMSuggestion dataclass 自身契约。"""

    def test_dataclass_fields(self):
        s = LLMSuggestion(subject_id="1", reason="r")
        assert s.subject_id == "1"
        assert s.reason == "r"
