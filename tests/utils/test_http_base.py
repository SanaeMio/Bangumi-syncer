"""HttpClientBase / SyncHttpClient / AsyncHttpClient 单元测试"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.utils.http_base import AsyncHttpClient, SyncHttpClient

# ===== 辅助函数 =====


def _mock_response(status_code=200, json_data=None, text="", headers=None):
    """创建模拟 httpx.Response"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.elapsed = MagicMock()
    resp.elapsed.total_seconds.return_value = 0.05
    resp.headers = headers if headers is not None else {}
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def _mock_sync_httpx(response=None, side_effect=None):
    """创建模拟 httpx.Client"""
    mock = MagicMock()
    if side_effect is not None:
        mock.request = MagicMock(side_effect=side_effect)
    else:
        mock.request = MagicMock(return_value=response)
    mock.close = MagicMock()
    return mock


def _mock_async_httpx(response=None, side_effect=None):
    """创建模拟 httpx.AsyncClient"""
    mock = MagicMock()
    if side_effect is not None:
        mock.request = AsyncMock(side_effect=side_effect)
    else:
        mock.request = AsyncMock(return_value=response)
    mock.aclose = AsyncMock()
    mock.stream = MagicMock()
    return mock


# ===== 链式配置 =====


class TestChainableConfig:
    def test_prefix_returns_self(self):
        client = SyncHttpClient(label="Test")
        assert client.prefix("🔔") is client
        assert client._prefix == "🔔 "

    def test_prefix_empty(self):
        client = SyncHttpClient(label="Test")
        client.prefix("")
        assert client._prefix == ""

    def test_success_tpl_returns_self(self):
        client = SyncHttpClient(label="Test")
        assert client.success_tpl("成功") is client
        assert client._success_tpl == "成功"

    def test_failure_tpl_returns_self(self):
        client = SyncHttpClient(label="Test")
        assert client.failure_tpl("失败") is client
        assert client._failure_tpl == "失败"

    def test_on_success_returns_self(self):
        client = SyncHttpClient(label="Test")

        def fn(r, u):
            return "ok"

        assert client.on_success(fn) is client
        assert client._success_msg is fn

    def test_on_failure_returns_self(self):
        client = SyncHttpClient(label="Test")

        def fn(e, u):
            return "err"

        assert client.on_failure(fn) is client
        assert client._failure_msg is fn

    def test_chained_config(self):
        client = (
            SyncHttpClient(label="Test")
            .prefix("🚀")
            .success_tpl("推送成功")
            .failure_tpl("推送失败: {error}")
        )
        assert client._prefix == "🚀 "
        assert client._success_tpl == "推送成功"
        assert client._failure_tpl == "推送失败: {error}"


# ===== 模板格式化 =====


class TestFormatSuccess:
    def test_template_with_variables(self):
        client = SyncHttpClient(label="Test").success_tpl(
            "OK {status_code} {url} {elapsed}"
        )
        resp = _mock_response(status_code=200)
        result = client._format_success(resp, "http://example.com")
        assert "200" in result
        assert "http://example.com" in result
        assert "0.05s" in result

    def test_template_plain_text(self):
        client = SyncHttpClient(label="Test").success_tpl("请求成功")
        result = client._format_success(_mock_response(), "http://example.com")
        assert result == "请求成功"

    def test_callback_overrides_template(self):
        client = (
            SyncHttpClient(label="Test")
            .success_tpl("ignored")
            .on_success(lambda r, u: f"custom {r.status_code}")
        )
        resp = _mock_response(status_code=201)
        assert client._format_success(resp, "http://example.com") == "custom 201"


class TestFormatFailure:
    def test_template_with_variables(self):
        client = SyncHttpClient(label="Test").failure_tpl("FAIL {error_type}: {error}")
        result = client._format_failure(ValueError("bad"), "http://example.com")
        assert "ValueError" in result
        assert "bad" in result

    def test_template_plain_text(self):
        client = SyncHttpClient(label="Test").failure_tpl("请求失败")
        result = client._format_failure(RuntimeError("x"), "http://example.com")
        assert result == "请求失败"

    def test_callback_overrides_template(self):
        client = (
            SyncHttpClient(label="Test")
            .failure_tpl("ignored")
            .on_failure(lambda e, u: f"custom {type(e).__name__}")
        )
        assert (
            client._format_failure(ValueError("bad"), "http://example.com")
            == "custom ValueError"
        )


# ===== 重试判断 =====


class TestRetryChecks:
    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
    def test_should_retry_status_true(self, code):
        assert SyncHttpClient(label="T")._should_retry_status(code) is True

    @pytest.mark.parametrize("code", [200, 301, 400, 401, 403, 404])
    def test_should_retry_status_false(self, code):
        assert SyncHttpClient(label="T")._should_retry_status(code) is False

    def test_should_retry_exception_timeout(self):
        assert (
            SyncHttpClient(label="T")._should_retry_exception(
                httpx.TimeoutException("x")
            )
            is True
        )

    def test_should_retry_exception_connect_error(self):
        assert (
            SyncHttpClient(label="T")._should_retry_exception(httpx.ConnectError("x"))
            is True
        )

    def test_should_retry_exception_value_error(self):
        assert (
            SyncHttpClient(label="T")._should_retry_exception(ValueError("x")) is False
        )


# ===== SyncHttpClient =====


class TestSyncHttpClient:
    def test_client_property(self):
        with patch("app.utils.http_base.create_sync_client") as mc:
            mock_httpx = _mock_sync_httpx()
            mc.return_value = mock_httpx
            assert SyncHttpClient(label="T").client is mock_httpx

    def test_request_success(self):
        resp = _mock_response(status_code=200, json_data={"ok": True})
        with patch("app.utils.http_base.create_sync_client") as mc:
            mc.return_value = _mock_sync_httpx(response=resp)
            client = SyncHttpClient(label="T", max_retries=0)
            assert client.request("GET", "http://example.com") is resp

    def test_request_logs_info_success(self):
        resp = _mock_response(status_code=200, text="hello")
        with patch("app.utils.http_base.create_sync_client") as mc:
            mc.return_value = _mock_sync_httpx(response=resp)
            with patch("app.utils.http_base.logger") as ml:
                client = (
                    SyncHttpClient(label="T", max_retries=0)
                    .prefix("🔔")
                    .success_tpl("推送成功")
                )
                client.request("GET", "http://example.com")
                infos = [str(c) for c in ml.info.call_args_list]
                assert any("推送成功" in m for m in infos)
                assert any("[T]" in m and "GET" in m and "200" in m for m in infos)
                ml.debug.assert_called()

    def test_request_failure_logs(self):
        err = httpx.ConnectError("conn refused")
        with patch("app.utils.http_base.create_sync_client") as mc:
            mc.return_value = _mock_sync_httpx(side_effect=err)
            with patch("app.utils.http_base.logger") as ml:
                client = (
                    SyncHttpClient(label="T", max_retries=0)
                    .prefix("🔔")
                    .failure_tpl("推送失败: {error_type}")
                )
                with pytest.raises(httpx.ConnectError):
                    client.request("GET", "http://example.com")
                infos = [str(c) for c in ml.info.call_args_list]
                assert any("推送失败" in m and "ConnectError" in m for m in infos)

    def test_request_retries_on_status(self):
        r429 = _mock_response(status_code=429)
        r200 = _mock_response(status_code=200)
        with patch("app.utils.http_base.create_sync_client") as mc:
            mc.return_value = _mock_sync_httpx(side_effect=[r429, r200])
            with patch("app.utils.http_base.time.sleep"):
                client = SyncHttpClient(label="T", max_retries=2)
                assert client.request("GET", "http://example.com") is r200

    def test_request_retry_exhausted_returns_last(self):
        r429 = _mock_response(status_code=429)
        with patch("app.utils.http_base.create_sync_client") as mc:
            mc.return_value = _mock_sync_httpx(side_effect=[r429, r429, r429])
            with patch("app.utils.http_base.time.sleep"):
                client = SyncHttpClient(label="T", max_retries=2)
                assert client.request("GET", "http://example.com") is r429

    def test_request_retries_on_exception(self):
        err = httpx.TimeoutException("timeout")
        r200 = _mock_response(status_code=200)
        with patch("app.utils.http_base.create_sync_client") as mc:
            mc.return_value = _mock_sync_httpx(side_effect=[err, r200])
            with patch("app.utils.http_base.time.sleep"):
                client = SyncHttpClient(label="T", max_retries=2)
                assert client.request("GET", "http://example.com") is r200

    def test_request_exception_exhausted_raises(self):
        err = httpx.TimeoutException("timeout")
        with patch("app.utils.http_base.create_sync_client") as mc:
            mc.return_value = _mock_sync_httpx(side_effect=[err, err, err])
            with patch("app.utils.http_base.time.sleep"):
                client = SyncHttpClient(label="T", max_retries=2)
                with pytest.raises(httpx.TimeoutException):
                    client.request("GET", "http://example.com")

    def test_request_non_retryable_exception_raises(self):
        with patch("app.utils.http_base.create_sync_client") as mc:
            mc.return_value = _mock_sync_httpx(side_effect=ValueError("bad"))
            client = SyncHttpClient(label="T", max_retries=3)
            with pytest.raises(ValueError):
                client.request("GET", "http://example.com")

    def test_get_delegates_to_request(self):
        resp = _mock_response(status_code=200)
        with patch("app.utils.http_base.create_sync_client") as mc:
            mock_httpx = _mock_sync_httpx(response=resp)
            mc.return_value = mock_httpx
            SyncHttpClient(label="T").get("http://example.com", headers={"X": "1"})
            mock_httpx.request.assert_called_with(
                "GET", "http://example.com", headers={"X": "1"}
            )

    def test_post_delegates_to_request(self):
        resp = _mock_response(status_code=200)
        with patch("app.utils.http_base.create_sync_client") as mc:
            mock_httpx = _mock_sync_httpx(response=resp)
            mc.return_value = mock_httpx
            SyncHttpClient(label="T").post("http://example.com", json={"k": "v"})
            mock_httpx.request.assert_called_with(
                "POST", "http://example.com", json={"k": "v"}
            )

    def test_put_delegates_to_request(self):
        resp = _mock_response(status_code=200)
        with patch("app.utils.http_base.create_sync_client") as mc:
            mock_httpx = _mock_sync_httpx(response=resp)
            mc.return_value = mock_httpx
            SyncHttpClient(label="T").put("http://example.com")
            mock_httpx.request.assert_called_with("PUT", "http://example.com")

    def test_patch_delegates_to_request(self):
        resp = _mock_response(status_code=200)
        with patch("app.utils.http_base.create_sync_client") as mc:
            mock_httpx = _mock_sync_httpx(response=resp)
            mc.return_value = mock_httpx
            SyncHttpClient(label="T").patch("http://example.com")
            mock_httpx.request.assert_called_with("PATCH", "http://example.com")

    def test_close_calls_underlying(self):
        with patch("app.utils.http_base.create_sync_client") as mc:
            mock_httpx = _mock_sync_httpx()
            mc.return_value = mock_httpx
            SyncHttpClient(label="T").close()
            mock_httpx.close.assert_called_once()

    def test_context_manager(self):
        with patch("app.utils.http_base.create_sync_client") as mc:
            mock_httpx = _mock_sync_httpx()
            mc.return_value = mock_httpx
            with SyncHttpClient(label="T") as client:
                assert isinstance(client, SyncHttpClient)
            mock_httpx.close.assert_called_once()


# ===== AsyncHttpClient =====


class TestAsyncHttpClient:
    @pytest.mark.asyncio
    async def test_client_property(self):
        with patch("app.utils.http_base.create_async_client") as mc:
            mock_httpx = _mock_async_httpx()
            mc.return_value = mock_httpx
            assert AsyncHttpClient(label="T").client is mock_httpx

    @pytest.mark.asyncio
    async def test_request_success(self):
        resp = _mock_response(status_code=200, json_data={"ok": True})
        with patch("app.utils.http_base.create_async_client") as mc:
            mc.return_value = _mock_async_httpx(response=resp)
            client = AsyncHttpClient(label="T", max_retries=0)
            assert await client.request("GET", "http://example.com") is resp

    @pytest.mark.asyncio
    async def test_request_retries_on_status(self):
        r429 = _mock_response(status_code=429)
        r200 = _mock_response(status_code=200)
        with patch("app.utils.http_base.create_async_client") as mc:
            mc.return_value = _mock_async_httpx(side_effect=[r429, r200])
            with patch("app.utils.http_base.asyncio.sleep"):
                client = AsyncHttpClient(label="T", max_retries=2)
                assert await client.request("GET", "http://example.com") is r200

    @pytest.mark.asyncio
    async def test_request_retry_exhausted_returns_last(self):
        r429 = _mock_response(status_code=429)
        with patch("app.utils.http_base.create_async_client") as mc:
            mc.return_value = _mock_async_httpx(side_effect=[r429, r429, r429])
            with patch("app.utils.http_base.asyncio.sleep"):
                client = AsyncHttpClient(label="T", max_retries=2)
                assert await client.request("GET", "http://example.com") is r429

    @pytest.mark.asyncio
    async def test_request_retries_on_exception(self):
        err = httpx.TimeoutException("timeout")
        r200 = _mock_response(status_code=200)
        with patch("app.utils.http_base.create_async_client") as mc:
            mc.return_value = _mock_async_httpx(side_effect=[err, r200])
            with patch("app.utils.http_base.asyncio.sleep"):
                client = AsyncHttpClient(label="T", max_retries=2)
                assert await client.request("GET", "http://example.com") is r200

    @pytest.mark.asyncio
    async def test_request_exception_exhausted_raises(self):
        err = httpx.TimeoutException("timeout")
        with patch("app.utils.http_base.create_async_client") as mc:
            mc.return_value = _mock_async_httpx(side_effect=[err, err, err])
            with patch("app.utils.http_base.asyncio.sleep"):
                client = AsyncHttpClient(label="T", max_retries=2)
                with pytest.raises(httpx.TimeoutException):
                    await client.request("GET", "http://example.com")

    @pytest.mark.asyncio
    async def test_get_delegates_to_request(self):
        resp = _mock_response(status_code=200)
        with patch("app.utils.http_base.create_async_client") as mc:
            mock_httpx = _mock_async_httpx(response=resp)
            mc.return_value = mock_httpx
            await AsyncHttpClient(label="T").get(
                "http://example.com", headers={"X": "1"}
            )
            mock_httpx.request.assert_called_with(
                "GET", "http://example.com", headers={"X": "1"}
            )

    @pytest.mark.asyncio
    async def test_post_delegates_to_request(self):
        resp = _mock_response(status_code=200)
        with patch("app.utils.http_base.create_async_client") as mc:
            mock_httpx = _mock_async_httpx(response=resp)
            mc.return_value = mock_httpx
            await AsyncHttpClient(label="T").post("http://example.com", json={"k": "v"})
            mock_httpx.request.assert_called_with(
                "POST", "http://example.com", json={"k": "v"}
            )

    @pytest.mark.asyncio
    async def test_aclose_calls_underlying(self):
        with patch("app.utils.http_base.create_async_client") as mc:
            mock_httpx = _mock_async_httpx()
            mc.return_value = mock_httpx
            await AsyncHttpClient(label="T").aclose()
            mock_httpx.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        with patch("app.utils.http_base.create_async_client") as mc:
            mock_httpx = _mock_async_httpx()
            mc.return_value = mock_httpx
            async with AsyncHttpClient(label="T") as client:
                assert isinstance(client, AsyncHttpClient)
            mock_httpx.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stream_calls_underlying(self):
        with patch("app.utils.http_base.create_async_client") as mc:
            mock_httpx = _mock_async_httpx()
            mock_ctx = MagicMock()
            mock_httpx.stream = MagicMock(return_value=mock_ctx)
            mc.return_value = mock_httpx
            client = AsyncHttpClient(label="T")
            result = client.stream("GET", "http://example.com")
            mock_httpx.stream.assert_called_with("GET", "http://example.com")
            assert result is mock_ctx


# ===== 日志输出 =====


class TestLogging:
    def test_log_request_debug(self):
        client = SyncHttpClient(label="T")
        with patch("app.utils.http_base.logger") as ml:
            client._log_request(
                "POST",
                "http://example.com",
                headers={"X": "1"},
                params={"q": "test"},
                json={"k": "v"},
            )
            ml.debug.assert_called_once()
            msg = ml.debug.call_args[0][0]
            assert "[T]" in msg
            assert "POST" in msg
            assert "Headers" in msg
            assert "Params" in msg
            assert "Body(JSON)" in msg

    def test_log_request_with_form_data(self):
        client = SyncHttpClient(label="T")
        with patch("app.utils.http_base.logger") as ml:
            client._log_request("POST", "http://example.com", data={"field": "val"})
            msg = ml.debug.call_args[0][0]
            assert "Body(Form)" in msg

    def test_log_success_info_and_debug(self):
        resp = _mock_response(status_code=200, text="hello", headers={"X": "1"})
        client = SyncHttpClient(label="T").prefix("🚀").success_tpl("成功")
        with patch("app.utils.http_base.logger") as ml:
            client._log_success(resp, "GET", "http://example.com")
            assert ml.info.call_count == 2
            ml.debug.assert_called_once()

    def test_log_failure_info(self):
        client = (
            SyncHttpClient(label="T").prefix("🚀").failure_tpl("失败: {error_type}")
        )
        with patch("app.utils.http_base.logger") as ml:
            client._log_failure(ValueError("bad"), "GET", "http://example.com")
            assert ml.info.call_count == 2
            infos = [str(c) for c in ml.info.call_args_list]
            assert any("失败" in m and "ValueError" in m for m in infos)

    def test_log_retry_warning(self):
        client = SyncHttpClient(label="T")
        with patch("app.utils.http_base.logger") as ml:
            client._log_retry(1, "HTTP 429", 2.0)
            ml.warning.assert_called_once()
            msg = ml.warning.call_args[0][0]
            assert "第 1 次重试" in msg
            assert "HTTP 429" in msg
            assert "2.0s" in msg
