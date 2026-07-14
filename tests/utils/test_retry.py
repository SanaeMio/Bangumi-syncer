"""HTTP 请求重试通用工具测试"""

from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from app.utils.retry import (
    RETRY_EXCEPTIONS,
    RETRY_STATUS_CODES,
    compute_backoff_delay,
    http_retry_sync,
)


def _make_response(status_code: int) -> httpx.Response:
    """构造带 status_code 的 mock 响应对象"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    return resp


class TestRetryStatusCodes:
    """测试 RETRY_STATUS_CODES 常量"""

    def test_contains_expected_codes(self):
        """应包含 429, 500, 502, 503, 504 五个状态码"""
        assert 429 in RETRY_STATUS_CODES
        assert 500 in RETRY_STATUS_CODES
        assert 502 in RETRY_STATUS_CODES
        assert 503 in RETRY_STATUS_CODES
        assert 504 in RETRY_STATUS_CODES

    def test_does_not_contain_non_retry_codes(self):
        """不应包含非重试状态码"""
        assert 200 not in RETRY_STATUS_CODES
        assert 404 not in RETRY_STATUS_CODES
        assert 400 not in RETRY_STATUS_CODES


class TestRetryExceptions:
    """测试 RETRY_EXCEPTIONS 常量"""

    def test_is_tuple(self):
        """应为 tuple 类型"""
        assert isinstance(RETRY_EXCEPTIONS, tuple)

    def test_contains_expected_exceptions(self):
        """应包含 httpx.TimeoutException / ConnectError / HTTPError"""
        assert httpx.TimeoutException in RETRY_EXCEPTIONS
        assert httpx.ConnectError in RETRY_EXCEPTIONS
        assert httpx.HTTPError in RETRY_EXCEPTIONS

    def test_has_three_entries(self):
        """应恰好包含 3 个异常类型"""
        assert len(RETRY_EXCEPTIONS) == 3


class TestComputeBackoffDelay:
    """测试 compute_backoff_delay"""

    def test_attempt_zero(self):
        """attempt=0 → 2^0 = 1.0"""
        assert compute_backoff_delay(0) == 1.0

    def test_attempt_one(self):
        """attempt=1 → 2^1 = 2.0"""
        assert compute_backoff_delay(1) == 2.0

    def test_attempt_two(self):
        """attempt=2 → 2^2 = 4.0"""
        assert compute_backoff_delay(2) == 4.0

    def test_attempt_three(self):
        """attempt=3 → 2^3 = 8.0"""
        assert compute_backoff_delay(3) == 8.0

    def test_cap_applied(self):
        """cap=5.0 时 attempt=3 应被截断到 5.0"""
        assert compute_backoff_delay(3, cap=5.0) == 5.0

    def test_cap_not_applied_when_below(self):
        """cap=10.0 时 attempt=3 (8.0) 未达上限，保持 8.0"""
        assert compute_backoff_delay(3, cap=10.0) == 8.0

    def test_custom_base(self):
        """自定义 base=3, attempt=2 → 3^2 = 9.0"""
        assert compute_backoff_delay(2, base=3) == 9.0

    def test_returns_float(self):
        """返回值应为 float 类型"""
        assert isinstance(compute_backoff_delay(0), float)
        assert isinstance(compute_backoff_delay(3), float)

    def test_cap_none_no_limit(self):
        """cap=None 时无上限"""
        assert compute_backoff_delay(10) == 1024.0


class TestHttpRetrySyncSuccess:
    """测试 http_retry_sync 成功路径"""

    @patch("app.utils.retry.time.sleep")
    def test_success_first_try(self, mock_sleep):
        """首次即成功，不重试"""
        response = _make_response(200)
        do_request = MagicMock(return_value=response)

        result = http_retry_sync(do_request)

        assert result is response
        do_request.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("app.utils.retry.time.sleep")
    def test_retry_on_429_then_success(self, mock_sleep):
        """429 后重试一次成功"""
        retry_resp = _make_response(429)
        ok_resp = _make_response(200)
        do_request = MagicMock(side_effect=[retry_resp, ok_resp])

        result = http_retry_sync(do_request, max_retries=3)

        assert result is ok_resp
        assert do_request.call_count == 2
        mock_sleep.assert_called_once_with(1.0)

    @patch("app.utils.retry.time.sleep")
    def test_retry_on_503_then_success(self, mock_sleep):
        """503 后重试一次成功"""
        retry_resp = _make_response(503)
        ok_resp = _make_response(200)
        do_request = MagicMock(side_effect=[retry_resp, ok_resp])

        result = http_retry_sync(do_request, max_retries=3)

        assert result is ok_resp
        assert do_request.call_count == 2
        mock_sleep.assert_called_once_with(1.0)

    @patch("app.utils.retry.time.sleep")
    def test_non_retry_status_200_returns_immediately(self, mock_sleep):
        """200 状态码立即返回"""
        response = _make_response(200)
        do_request = MagicMock(return_value=response)

        result = http_retry_sync(do_request)

        assert result is response
        do_request.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("app.utils.retry.time.sleep")
    def test_non_retry_status_404_returns_immediately(self, mock_sleep):
        """404 状态码立即返回（不重试）"""
        response = _make_response(404)
        do_request = MagicMock(return_value=response)

        result = http_retry_sync(do_request)

        assert result is response
        do_request.assert_called_once()
        mock_sleep.assert_not_called()


class TestHttpRetrySyncExhaustedStatus:
    """测试 http_retry_sync 状态码重试耗尽"""

    @patch("app.utils.retry.time.sleep")
    def test_exhausted_retries_returns_last_response(self, mock_sleep):
        """重试耗尽时应返回最后一个响应（不抛异常）"""
        retry_resp = _make_response(503)
        do_request = MagicMock(return_value=retry_resp)

        result = http_retry_sync(do_request, max_retries=3)

        assert result is retry_resp
        # 1 次初始 + 3 次重试 = 4 次
        assert do_request.call_count == 4
        # sleep 调用 3 次，延迟 1.0, 2.0, 4.0
        assert mock_sleep.call_args_list == [
            call(1.0),
            call(2.0),
            call(4.0),
        ]

    @patch("app.utils.retry.time.sleep")
    def test_exhausted_retries_with_custom_backoff(self, mock_sleep):
        """自定义退避参数下重试耗尽时的 sleep 序列"""
        retry_resp = _make_response(429)
        do_request = MagicMock(return_value=retry_resp)

        result = http_retry_sync(
            do_request,
            max_retries=2,
            backoff_base=3,
            backoff_cap=10,
        )

        assert result is retry_resp
        # 1 + 2 = 3 次
        assert do_request.call_count == 3
        # base=3, attempt 0→1, 1→3
        assert mock_sleep.call_args_list == [call(1.0), call(3.0)]

    @patch("app.utils.retry.time.sleep")
    def test_zero_retries_returns_retry_response(self, mock_sleep):
        """max_retries=0 时不重试，直接返回响应"""
        retry_resp = _make_response(503)
        do_request = MagicMock(return_value=retry_resp)

        result = http_retry_sync(do_request, max_retries=0)

        assert result is retry_resp
        do_request.assert_called_once()
        mock_sleep.assert_not_called()


class TestHttpRetrySyncExceptions:
    """测试 http_retry_sync 异常重试路径"""

    @patch("app.utils.retry.time.sleep")
    def test_retry_on_timeout_then_success(self, mock_sleep):
        """TimeoutException 后重试成功"""
        ok_resp = _make_response(200)
        do_request = MagicMock(
            side_effect=[httpx.TimeoutException("timeout"), ok_resp]
        )

        result = http_retry_sync(do_request, max_retries=3)

        assert result is ok_resp
        assert do_request.call_count == 2
        mock_sleep.assert_called_once_with(1.0)

    @patch("app.utils.retry.time.sleep")
    def test_retry_on_connect_error_then_success(self, mock_sleep):
        """ConnectError 后重试成功"""
        ok_resp = _make_response(200)
        do_request = MagicMock(
            side_effect=[httpx.ConnectError("connect failed"), ok_resp]
        )

        result = http_retry_sync(do_request, max_retries=3)

        assert result is ok_resp
        assert do_request.call_count == 2
        mock_sleep.assert_called_once_with(1.0)

    @patch("app.utils.retry.time.sleep")
    def test_retry_on_http_error_then_success(self, mock_sleep):
        """HTTPError 后重试成功"""
        ok_resp = _make_response(200)
        do_request = MagicMock(
            side_effect=[httpx.HTTPError("http error"), ok_resp]
        )

        result = http_retry_sync(do_request, max_retries=3)

        assert result is ok_resp
        assert do_request.call_count == 2
        mock_sleep.assert_called_once_with(1.0)

    @patch("app.utils.retry.time.sleep")
    def test_exhausted_retries_reraises_exception(self, mock_sleep):
        """异常重试耗尽时应重新抛出异常"""
        exc = httpx.TimeoutException("timeout")
        do_request = MagicMock(side_effect=exc)

        with pytest.raises(httpx.TimeoutException):
            http_retry_sync(do_request, max_retries=3)

        # 1 + 3 = 4 次
        assert do_request.call_count == 4
        assert mock_sleep.call_args_list == [
            call(1.0),
            call(2.0),
            call(4.0),
        ]

    @patch("app.utils.retry.time.sleep")
    def test_exhausted_retries_reraises_connect_error(self, mock_sleep):
        """ConnectError 重试耗尽时重新抛出"""
        exc = httpx.ConnectError("connect failed")
        do_request = MagicMock(side_effect=exc)

        with pytest.raises(httpx.ConnectError):
            http_retry_sync(do_request, max_retries=2)

        assert do_request.call_count == 3
        assert mock_sleep.call_args_list == [call(1.0), call(2.0)]

    @patch("app.utils.retry.time.sleep")
    def test_non_retry_exception_reraises_immediately(self, mock_sleep):
        """非 RETRY_EXCEPTIONS 异常应立即抛出，不重试"""
        exc = ValueError("not a retry exception")
        do_request = MagicMock(side_effect=exc)

        with pytest.raises(ValueError):
            http_retry_sync(do_request, max_retries=3)

        do_request.assert_called_once()
        mock_sleep.assert_not_called()


class TestHttpRetrySyncLabel:
    """测试 http_retry_sync 的 label 参数"""

    @patch("app.utils.retry.time.sleep")
    def test_custom_label_used_in_logging(self, mock_sleep):
        """自定义 label 不影响重试逻辑"""
        retry_resp = _make_response(429)
        ok_resp = _make_response(200)
        do_request = MagicMock(side_effect=[retry_resp, ok_resp])

        result = http_retry_sync(do_request, max_retries=3, label="自定义请求")

        assert result is ok_resp
        assert do_request.call_count == 2

    @patch("app.utils.retry.time.sleep")
    def test_custom_label_with_exception(self, mock_sleep):
        """自定义 label 在异常重试时不影响逻辑"""
        ok_resp = _make_response(200)
        do_request = MagicMock(
            side_effect=[httpx.TimeoutException("timeout"), ok_resp]
        )

        result = http_retry_sync(do_request, max_retries=3, label="BGM API")

        assert result is ok_resp
        assert do_request.call_count == 2


class TestHttpRetrySyncSleepSequence:
    """专门验证 sleep 调用序列的测试"""

    @patch("app.utils.retry.time.sleep")
    def test_sleep_sequence_for_multiple_retries(self, mock_sleep):
        """多次重试时 sleep 调用应使用指数退避序列 1, 2, 4"""
        retry_resp = _make_response(500)
        ok_resp = _make_response(200)
        do_request = MagicMock(
            side_effect=[retry_resp, retry_resp, retry_resp, ok_resp]
        )

        result = http_retry_sync(do_request, max_retries=3)

        assert result is ok_resp
        assert do_request.call_count == 4
        assert mock_sleep.call_args_list == [
            call(1.0),
            call(2.0),
            call(4.0),
        ]

    @patch("app.utils.retry.time.sleep")
    def test_sleep_sequence_with_cap(self, mock_sleep):
        """backoff_cap 限制 sleep 时长"""
        retry_resp = _make_response(500)
        ok_resp = _make_response(200)
        do_request = MagicMock(
            side_effect=[retry_resp, retry_resp, retry_resp, ok_resp]
        )

        result = http_retry_sync(
            do_request, max_retries=3, backoff_cap=3.0
        )

        assert result is ok_resp
        # attempt 0→1.0, 1→2.0, 2→4.0 但被 cap 限制为 3.0
        assert mock_sleep.call_args_list == [
            call(1.0),
            call(2.0),
            call(3.0),
        ]
