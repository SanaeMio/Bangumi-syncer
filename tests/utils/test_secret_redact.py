"""`app.utils.secret_redact.redact_secrets` 单元测试。

覆盖 last_error 等外泄文本中常见密钥模式的遮蔽：
- Bearer / Basic 授权头
- Authorization 头（无 scheme 或带 scheme）
- URL / 键值 / JSON 形式：access_token / refresh_token / token / api_key /
  apikey / secret / password
- 正常文本不误伤；None / 空串原样返回
"""

import pytest

from app.utils.secret_redact import redact_secrets


class TestRedactPatterns:
    """各敏感模式：token 值被遮蔽、键名（可辨识前缀）保留。"""

    @pytest.mark.parametrize(
        ("raw", "expected", "leaked"),
        [
            # Bearer / Basic 授权头
            ("Bearer abc123", "Bearer ***", "abc123"),
            ("bearer abc123", "bearer ***", "abc123"),
            ("Authorization: Bearer tok-xyz", "Authorization: Bearer ***", "tok-xyz"),
            (
                "Authorization: Basic dXNlcjpwYXNz",
                "Authorization: Basic ***",
                "dXNlcjpwYXNz",
            ),
            ("authorization=Bearer tok-abc", "authorization=Bearer ***", "tok-abc"),
            # 键值 / URL 查询串形式
            ("access_token=abc123", "access_token=***", "abc123"),
            ("refresh_token=refresh-1", "refresh_token=***", "refresh-1"),
            ("token=abc123&foo=bar", "token=***&foo=bar", "abc123"),
            ("api_key=sk-xyz", "api_key=***", "sk-xyz"),
            ("apikey=sk-xyz", "apikey=***", "sk-xyz"),
            ("apiKey=sk-xyz", "apiKey=***", "sk-xyz"),
            ("secret=s3cr3t", "secret=***", "s3cr3t"),
            ("password=hunter2", "password=***", "hunter2"),
            # JSON 引号形式
            ('"access_token": "abc123"', '"access_token": "***"', "abc123"),
            ('{"api_key": "sk-live-1"}', '{"api_key": "***"}', "sk-live-1"),
            ("'password': 'hunter2'", "'password': '***'", "hunter2"),
            # 冒号分隔（无引号）
            ("token: abc123", "token: ***", "abc123"),
        ],
    )
    def test_secret_value_masked_and_key_preserved(self, raw, expected, leaked):
        assert redact_secrets(raw) == expected
        assert leaked not in redact_secrets(raw)

    def test_multiple_patterns_in_single_text(self):
        """同一文本内多个密钥模式全部遮蔽，非敏感内容保留。"""
        raw = (
            "请求失败: Authorization: Bearer tok123, "
            "access_token=abc & password=hunter2"
        )
        out = redact_secrets(raw)
        assert "tok123" not in out
        assert "abc" not in out
        assert "hunter2" not in out
        assert "Authorization: Bearer ***" in out
        assert "access_token=***" in out
        assert "password=***" in out
        assert "请求失败" in out


class TestRedactPassthrough:
    """无敏感模式 / 空值：原样返回，不误伤普通文本。"""

    @pytest.mark.parametrize(
        "text",
        [
            "正常错误信息，无敏感内容",
            "connection timeout after 30 seconds",
            "HTTP 500 from upstream service",
            "无法解析 LLM 响应（缺少 suggestions 字段）",
        ],
    )
    def test_plain_text_unchanged(self, text):
        assert redact_secrets(text) == text

    def test_none_returned_as_is(self):
        assert redact_secrets(None) is None

    def test_empty_string_returned_as_is(self):
        assert redact_secrets("") == ""

    @pytest.mark.parametrize(
        "value",
        [
            123,
            0,
            {"a": 1},
            ["x"],
            Exception("x"),
        ],
        ids=["int", "zero", "dict", "list", "exception"],
    )
    def test_non_string_returned_as_is_without_raising(self, value):
        """非 str 输入（int/dict/Exception 等）原样返回，不得抛 TypeError。"""
        assert redact_secrets(value) is value
