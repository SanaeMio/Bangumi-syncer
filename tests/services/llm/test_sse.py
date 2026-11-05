"""app.services.llm.sse 测试。

覆盖 SSE 文本行的纯解析逻辑与异步包装：事件边界、多行 data 拼接、
event/id/retry 字段、注释与未知字段、CRLF 兼容、残留事件宽容 dispatch。
"""

from app.services.llm.sse import SSEEvent, iter_sse_events, parse_sse_lines


class TestParseSSELines:
    """同步解析器 parse_sse_lines 的行为。"""

    def test_single_event(self):
        """单事件：data 行 + 空行 → 一个事件。"""
        events = list(parse_sse_lines(["data: hello", ""]))
        assert events == [SSEEvent(data="hello")]

    def test_multiline_data_joined_with_newline(self):
        """多行 data 用 \\n 拼接为同一事件的 data。"""
        events = list(parse_sse_lines(["data: line1", "data: line2", ""]))
        assert events == [SSEEvent(data="line1\nline2")]

    def test_event_field(self):
        """event 字段被解析到 SSEEvent.event。"""
        events = list(parse_sse_lines(["event: message_start", "data: {}"]))
        assert events == [SSEEvent(event="message_start", data="{}")]

    def test_comment_line_ignored(self):
        """以 : 开头的注释行不产出事件。"""
        events = list(parse_sse_lines([": ping", ""]))
        assert events == []

    def test_multiple_events_in_order(self):
        """连续多个事件按出现顺序产出。"""
        events = list(parse_sse_lines(["data: first", "", "data: second", ""]))
        assert events == [SSEEvent(data="first"), SSEEvent(data="second")]

    def test_no_space_after_colon(self):
        """冒号后无空格时原样取值。"""
        events = list(parse_sse_lines(['data:{"a":1}', ""]))
        assert events == [SSEEvent(data='{"a":1}')]

    def test_id_and_retry_fields(self):
        """id 与 retry 字段被解析，retry 转为 int。"""
        events = list(parse_sse_lines(["id: 42", "retry: 3000", "data: x", ""]))
        assert events == [SSEEvent(data="x", id="42", retry=3000)]

    def test_retry_invalid_value_ignored(self):
        """retry 非法值不报错，置为 None。"""
        events = list(parse_sse_lines(["retry: abc", "data: x", ""]))
        assert events == [SSEEvent(data="x", retry=None)]

    def test_trailing_event_without_blank_line(self):
        """流结束时无结尾空行，仍宽容 dispatch 残留事件。"""
        events = list(parse_sse_lines(["data: x"]))
        assert events == [SSEEvent(data="x")]

    def test_unknown_field_ignored(self):
        """未知字段不影响事件内容。"""
        events = list(parse_sse_lines(["foo: bar", "data: x", ""]))
        assert events == [SSEEvent(data="x")]

    def test_done_marker_passed_through(self):
        """[DONE] 作为普通 data 原样传递，不做特殊处理。"""
        events = list(parse_sse_lines(["data: [DONE]", ""]))
        assert events == [SSEEvent(data="[DONE]")]

    def test_empty_input(self):
        """空输入不产出任何事件。"""
        assert list(parse_sse_lines([])) == []

    def test_empty_data_value_produces_event(self):
        """data: 值为空时仍产出事件（data=\"\"）。"""
        events = list(parse_sse_lines(["data:", ""]))
        assert events == [SSEEvent(data="")]

    def test_crlf_stripped(self):
        """行尾 \\r（CRLF）被剥离，事件边界仍生效。"""
        events = list(parse_sse_lines(["data: hello\r", "\r"]))
        assert events == [SSEEvent(data="hello")]

    def test_line_without_colon_ignored(self):
        """无冒号的坏行不产出事件，也不污染后续事件。"""
        events = list(parse_sse_lines(["garbage", "data: x", ""]))
        assert events == [SSEEvent(data="x")]


class TestIterSSEEvents:
    """异步包装 iter_sse_events 与同步版本行为一致。"""

    async def test_async_wrapper_matches_sync(self):
        """异步行迭代器经包装后产出与同步版本一致的事件序列。"""

        async def agen():
            for line in ["data: first", "", "event: e", "data: second", ""]:
                yield line

        events = [event async for event in iter_sse_events(agen())]
        assert events == [
            SSEEvent(data="first"),
            SSEEvent(event="e", data="second"),
        ]
