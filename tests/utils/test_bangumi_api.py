"""
Bangumi API 工具测试
"""

import sqlite3
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.utils.bangumi_api import BangumiApi


class TestBangumiApi:
    """Bangumi API 测试"""

    def test_init_default(self):
        api = BangumiApi()
        assert api.api_base == "https://api.bgm.tv"
        assert api.next_base == "https://next.bgm.tv"
        assert api.host == "https://api.bgm.tv/v0"
        assert api.username is None
        assert api.access_token is None
        assert api.private is True
        assert api.ssl_verify is True

    def test_init_with_params(self):
        api = BangumiApi(
            username="testuser",
            access_token="test_token",
            private=False,
            http_proxy="http://proxy:8080",
            ssl_verify=False,
            bgm_api_proxy="https://proxy.bgm.tv",
            bgm_next_proxy="https://next-proxy.bgm.tv",
        )
        assert api.username == "testuser"
        assert api.access_token == "test_token"
        assert api.private is False
        assert api.http_proxy == "http://proxy:8080"
        assert api.ssl_verify is False
        assert api.api_base == "https://proxy.bgm.tv"
        assert api.next_base == "https://next-proxy.bgm.tv"
        assert api.host == "https://proxy.bgm.tv/v0"

    def test_init_sets_cache(self):
        api = BangumiApi()
        assert "search" in api._cache
        assert "get_subject" in api._cache
        assert "get_related_subjects" in api._cache
        assert "get_episodes" in api._cache

    def test_init_proxy_failed_flag(self):
        api = BangumiApi()
        assert api._proxy_failed is False

    def test_init_sets_headers(self):
        api = BangumiApi(access_token="test_token")
        assert "Accept" in api.req.client.headers
        assert "User-Agent" in api.req.client.headers

    def test_init_proxy_sets_proxies(self):
        api = BangumiApi(http_proxy="http://proxy:8080")
        # httpx 0.28+ 通过构造函数 proxy 参数设置代理，存储在 api.http_proxy
        assert api.http_proxy == "http://proxy:8080"

    def test_init_no_auth_header_on_not_auth_session(self):
        api = BangumiApi(access_token="test_token")
        # httpx Headers 大小写不敏感，用 lower 比较
        assert "authorization" not in {
            k.lower() for k in api._req_not_auth.client.headers
        }

    def test_cache_clear(self):
        api = BangumiApi()
        api._cache["search"]["test"] = "value"
        api._cache["search"].clear()
        assert api._cache["search"] == {}

    def test_cache_keys(self):
        api = BangumiApi()
        cache_keys = list(api._cache.keys())
        expected_keys = [
            "search",
            "get_subject",
            "get_related_subjects",
            "get_episodes",
        ]
        assert cache_keys == expected_keys


class TestBangumiApiMethods:
    """Bangumi API 方法测试"""

    def test_cache_get(self):
        api = BangumiApi()
        api._cache["search"]["test_key"] = {"data": "test_value"}
        assert api._cache["search"].get("test_key") == {"data": "test_value"}

    def test_cache_set(self):
        api = BangumiApi()
        api._cache["search"]["new_key"] = {"data": "new_value"}
        assert api._cache["search"]["new_key"] == {"data": "new_value"}

    def test_proxy_failed_flag_set(self):
        api = BangumiApi()
        api._proxy_failed = True
        assert api._proxy_failed is True

    def test_proxy_failed_flag_reset(self):
        api = BangumiApi()
        api._proxy_failed = True
        api._proxy_failed = False
        assert api._proxy_failed is False


class TestPutCache:
    """测试 LRU 缓存"""

    def test_put_cache_evicts_oldest(self):
        api = BangumiApi()
        api._max_cache_size = 2
        api._put_cache("search", "k1", "v1")
        api._put_cache("search", "k2", "v2")
        api._put_cache("search", "k3", "v3")
        assert "k1" not in api._cache["search"]
        assert "k3" in api._cache["search"]

    def test_put_cache_updates_existing(self):
        api = BangumiApi()
        api._put_cache("search", "k1", "v1")
        api._put_cache("search", "k1", "v2")
        assert api._cache["search"]["k1"] == "v2"


class TestTryDirectConnection:
    """测试 _try_direct_connection"""

    def _mock_resp(self, status_code=200):
        resp = MagicMock()
        resp.status_code = status_code
        resp.elapsed.total_seconds.return_value = 0.01
        resp.headers = {}
        resp.text = ""
        return resp

    def test_get_success(self):
        api = BangumiApi()
        mock_resp = self._mock_resp(200)
        with patch("app.utils.bangumi_api.httpx.Client") as MockSession:
            mock_session = MagicMock()
            mock_session.request.return_value = mock_resp
            MockSession.return_value = mock_session
            result = api._try_direct_connection("GET", "https://example.com")
            assert result == mock_resp

    def test_post_success(self):
        api = BangumiApi()
        mock_resp = self._mock_resp(200)
        with patch("app.utils.bangumi_api.httpx.Client") as MockSession:
            mock_session = MagicMock()
            mock_session.request.return_value = mock_resp
            MockSession.return_value = mock_session
            result = api._try_direct_connection("POST", "https://example.com", json={})
            assert result == mock_resp

    def test_put_success(self):
        api = BangumiApi()
        mock_resp = self._mock_resp(200)
        with patch("app.utils.bangumi_api.httpx.Client") as MockSession:
            mock_session = MagicMock()
            mock_session.request.return_value = mock_resp
            MockSession.return_value = mock_session
            result = api._try_direct_connection("PUT", "https://example.com")
            assert result == mock_resp

    def test_patch_success(self):
        api = BangumiApi()
        mock_resp = self._mock_resp(200)
        with patch("app.utils.bangumi_api.httpx.Client") as MockSession:
            mock_session = MagicMock()
            mock_session.request.return_value = mock_resp
            MockSession.return_value = mock_session
            result = api._try_direct_connection("PATCH", "https://example.com")
            assert result == mock_resp

    def test_unsupported_method(self):
        api = BangumiApi()
        mock_resp = self._mock_resp(200)
        with patch("app.utils.bangumi_api.httpx.Client") as MockSession:
            mock_session = MagicMock()
            mock_session.request.return_value = mock_resp
            MockSession.return_value = mock_session
            result = api._try_direct_connection("DELETE", "https://example.com")
            assert result == mock_resp
            mock_session.request.assert_called_once()

    def test_error_status_returns_none(self):
        api = BangumiApi()
        mock_resp = self._mock_resp(500)
        with patch("app.utils.bangumi_api.httpx.Client") as MockSession:
            mock_session = MagicMock()
            mock_session.request.return_value = mock_resp
            MockSession.return_value = mock_session
            result = api._try_direct_connection("GET", "https://example.com")
            assert result is None

    def test_exception_reraises(self):
        api = BangumiApi()
        with patch("app.utils.bangumi_api.httpx.Client") as MockSession:
            mock_session = MagicMock()
            mock_session.request.side_effect = ConnectionError("fail")
            MockSession.return_value = mock_session
            with pytest.raises(ConnectionError):
                api._try_direct_connection("GET", "https://example.com")

    def test_removes_proxies_from_kwargs(self):
        api = BangumiApi()
        mock_resp = self._mock_resp(200)
        with patch("app.utils.bangumi_api.httpx.Client") as MockSession:
            mock_session = MagicMock()
            mock_session.request.return_value = mock_resp
            MockSession.return_value = mock_session
            result = api._try_direct_connection(
                "GET", "https://example.com", proxies={"http": "p"}
            )
            assert result == mock_resp


class TestDiagnoseNetworkIssue:
    """测试 _diagnose_network_issue"""

    def test_dns_success_tcp_success(self):
        api = BangumiApi()
        with (
            patch("app.utils.bangumi_api.socket.getaddrinfo") as mock_dns,
            patch("app.utils.bangumi_api.socket.socket") as MockSocket,
        ):
            mock_dns.return_value = [(None, None, None, None, ("1.2.3.4", 443))]
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0
            MockSocket.return_value = mock_sock
            api._diagnose_network_issue("https://example.com/path")

    def test_dns_failure(self):
        api = BangumiApi()
        with patch("app.utils.bangumi_api.socket.getaddrinfo") as mock_dns:
            import socket as _socket

            mock_dns.side_effect = _socket.gaierror("DNS fail")
            api._diagnose_network_issue("https://example.com")

    def test_dns_generic_exception(self):
        api = BangumiApi()
        with patch("app.utils.bangumi_api.socket.getaddrinfo") as mock_dns:
            mock_dns.side_effect = RuntimeError("unexpected")
            api._diagnose_network_issue("https://example.com")

    def test_tcp_failure(self):
        api = BangumiApi()
        with (
            patch("app.utils.bangumi_api.socket.getaddrinfo") as mock_dns,
            patch("app.utils.bangumi_api.socket.socket") as MockSocket,
        ):
            mock_dns.return_value = [(None, None, None, None, ("1.2.3.4", 443))]
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 1
            MockSocket.return_value = mock_sock
            api._diagnose_network_issue("https://example.com")

    def test_tcp_exception(self):
        api = BangumiApi()
        with (
            patch("app.utils.bangumi_api.socket.getaddrinfo") as mock_dns,
            patch("app.utils.bangumi_api.socket.socket") as MockSocket,
        ):
            mock_dns.return_value = [(None, None, None, None, ("1.2.3.4", 443))]
            mock_sock = MagicMock()
            mock_sock.connect_ex.side_effect = OSError("sock err")
            MockSocket.return_value = mock_sock
            api._diagnose_network_issue("https://example.com")

    def test_http_port(self):
        api = BangumiApi()
        with (
            patch("app.utils.bangumi_api.socket.getaddrinfo") as mock_dns,
            patch("app.utils.bangumi_api.socket.socket") as MockSocket,
        ):
            mock_dns.return_value = [(None, None, None, None, ("1.2.3.4", 80))]
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0
            MockSocket.return_value = mock_sock
            api._diagnose_network_issue("http://example.com")


class TestRequestWithRetry:
    """测试 _request_with_retry"""

    def _mock_session(self):
        return MagicMock()

    def test_success_first_try(self):
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.request.return_value = mock_resp
        result = api._request_with_retry("GET", mock_session, "https://example.com")
        assert result == mock_resp

    def test_retry_exhausted_raises(self):
        """重试耗尽后（SyncHttpClient 内部处理），503 响应触发 HTTPStatusError"""
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_session.request.return_value = mock_resp
        with (
            patch("app.services.notification_service.notification_service"),
            pytest.raises(httpx.HTTPStatusError),
        ):
            api._request_with_retry("GET", mock_session, "https://example.com")

    def test_connection_error_exhausted_no_proxy(self):
        """连接异常重试耗尽后（SyncHttpClient 内部处理），直接重新抛出"""
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_session.request.side_effect = httpx.ConnectError("fail")
        with pytest.raises(httpx.ConnectError):
            api._request_with_retry("GET", mock_session, "https://example.com")

    def test_proxy_fallback_to_direct(self):
        """代理请求异常后回退直连"""
        api = BangumiApi(http_proxy="http://proxy:8080")
        mock_session = self._mock_session()
        mock_session.request.side_effect = httpx.ConnectError("proxy fail")
        mock_direct = MagicMock()
        mock_direct.status_code = 200
        with patch.object(api, "_try_direct_connection", return_value=mock_direct):
            result = api._request_with_retry("GET", mock_session, "https://example.com")
        assert result == mock_direct
        assert api._proxy_failed is True

    def test_proxy_fallback_direct_fails(self):
        """代理异常且直连也失败"""
        api = BangumiApi(http_proxy="http://proxy:8080")
        mock_session = self._mock_session()
        mock_session.request.side_effect = httpx.ConnectError("proxy fail")
        with (
            patch.object(
                api,
                "_try_direct_connection",
                side_effect=httpx.ConnectError("direct fail"),
            ),
            pytest.raises(httpx.ConnectError),
        ):
            api._request_with_retry("GET", mock_session, "https://example.com")

    def test_proxy_already_failed_uses_direct(self):
        api = BangumiApi(http_proxy="http://proxy:8080")
        api._proxy_failed = True
        mock_session = self._mock_session()
        mock_direct = MagicMock()
        mock_direct.status_code = 200
        with patch.object(api, "_try_direct_connection", return_value=mock_direct):
            result = api._request_with_retry("GET", mock_session, "https://example.com")
        assert result == mock_direct

    def test_proxy_already_failed_direct_raises(self):
        api = BangumiApi(http_proxy="http://proxy:8080")
        api._proxy_failed = True
        mock_session = self._mock_session()
        with (
            patch.object(
                api,
                "_try_direct_connection",
                side_effect=ConnectionError("fail"),
            ),
            pytest.raises(ConnectionError),
        ):
            api._request_with_retry("GET", mock_session, "https://example.com")

    def test_dns_error_triggers_diagnosis(self):
        """DNS 解析异常触发网络诊断"""
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_session.request.side_effect = httpx.ConnectError(
            "Failed to resolve 'bad.host'"
        )
        with (
            patch.object(api, "_diagnose_network_issue") as mock_diag,
            pytest.raises(httpx.ConnectError),
        ):
            api._request_with_retry("GET", mock_session, "https://bad.host")
        mock_diag.assert_called_once()

    def test_unsupported_method(self):
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.request.return_value = mock_resp
        result = api._request_with_retry("DELETE", mock_session, "https://example.com")
        assert result == mock_resp
        mock_session.request.assert_called_once_with(
            "DELETE", "https://example.com", timeout=15
        )

    def test_post_method(self):
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.request.return_value = mock_resp
        result = api._request_with_retry(
            "POST", mock_session, "https://example.com", json={}
        )
        assert result == mock_resp

    def test_put_method(self):
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.request.return_value = mock_resp
        result = api._request_with_retry(
            "PUT", mock_session, "https://example.com", json={}
        )
        assert result == mock_resp

    def test_patch_method(self):
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.request.return_value = mock_resp
        result = api._request_with_retry(
            "PATCH", mock_session, "https://example.com", json={}
        )
        assert result == mock_resp


class TestCheckAuthError:
    """测试 _check_auth_error"""

    def test_401_raises(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        with (
            patch("app.utils.bangumi_api.httpx.Client"),
            pytest.raises(ValueError, match="认证失败"),
        ):
            api._check_auth_error(mock_resp)

    def test_200_passes(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        result = api._check_auth_error(mock_resp)
        assert result == mock_resp


class TestHttpMethods:
    """测试 HTTP 方法 (get, post, put, patch)"""

    def test_get(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(api, "_request_with_retry", return_value=mock_resp):
            result = api.get("test/path")
            assert result == mock_resp

    def test_post(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(api, "_request_with_retry", return_value=mock_resp):
            result = api.post("test/path", _json={"key": "value"})
            assert result == mock_resp

    def test_put(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(api, "_request_with_retry", return_value=mock_resp):
            result = api.put("test/path", _json={"type": 2})
            assert result == mock_resp

    def test_patch(self):
        """覆盖 patch HTTP 方法"""
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(api, "_request_with_retry", return_value=mock_resp):
            result = api.patch("test/path", _json={"key": "val"})
            assert result == mock_resp


class TestSearchMethods:
    """测试 search"""

    def test_search_cache_hit(self):
        api = BangumiApi()
        api._cache["search"][("title", "2024-01-01", "2024-12-31", 5, True, (2,))] = [
            {"id": 1}
        ]
        result = api.search("title", "2024-01-01", "2024-12-31")
        assert result == [{"id": 1}]

    def test_search_api_returns_non_dict(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [1, 2, 3]
        with patch.object(api, "_request_with_retry", return_value=mock_resp):
            result = api.search("title", "2024-01-01", "2024-12-31")
            assert result == []

    def test_search_api_json_error(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("bad json")
        with patch.object(api, "_request_with_retry", return_value=mock_resp):
            result = api.search("title", "2024-01-01", "2024-12-31")
            assert result == []

    def test_search_list_only_false(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"id": 1}], "total": 1}
        with patch.object(api, "_request_with_retry", return_value=mock_resp):
            result = api.search("title", "2024-01-01", "2024-12-31", list_only=False)
            assert "data" in result

    # search_old 方法已删除（统一走 search），原 search_old 单测随之移除。
    # 缓存命中/非字典响应/JSON 解析错误等行为由上方 search 对应用例覆盖。


class TestGetSubject:
    """测试 get_subject"""

    def test_cache_hit(self):
        api = BangumiApi()
        api._cache["get_subject"][("123", True)] = {"id": 123}
        assert api.get_subject("123") == {"id": 123}

    def test_non_dict_response(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [1, 2]
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_subject("123")
            assert result == {}

    def test_json_error(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("bad")
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_subject("123")
            assert result == {}


class TestGetRelatedSubjects:
    """测试 get_related_subjects"""

    def test_cache_hit(self):
        api = BangumiApi()
        api._cache["get_related_subjects"]["123"] = [{"id": 1}]
        assert api.get_related_subjects("123") == [{"id": 1}]

    def test_list_response(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"relation": "续集", "id": 2}]
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_related_subjects("123")
            assert isinstance(result, list)

    def test_non_dict_or_list(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = "unexpected"
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_related_subjects("123")
            assert result == []

    def test_json_error(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("bad")
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_related_subjects("123")
            assert result == []


class TestGetEpisodes:
    """测试 get_episodes"""

    def test_cache_hit(self):
        api = BangumiApi()
        api._cache["get_episodes"][("123", 0, False)] = {"data": []}
        assert api.get_episodes("123") == {"data": []}

    def test_fetch_all_pagination(self):
        api = BangumiApi()
        page1 = {
            "data": [{"id": i, "sort": i} for i in range(1, 201)],
            "total": 250,
        }
        page2 = {
            "data": [{"id": i, "sort": i} for i in range(201, 251)],
            "total": 250,
        }

        with patch.object(
            api, "_fetch_episodes_page", side_effect=[page1, page2]
        ) as mock_fetch:
            result = api.get_episodes("899", fetch_all=True)

        assert mock_fetch.call_count == 2
        assert result["total"] == 250
        assert len(result["data"]) == 250
        assert result["data"][-1]["sort"] == 250

    def test_non_dict_response(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [1]
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_episodes("123")
            assert result == {"data": [], "total": 0}

    def test_json_error(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("bad")
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_episodes("123")
            assert result == {"data": [], "total": 0}


class TestGetSubjectCollection:
    """测试 get_subject_collection"""

    def test_404_returns_empty(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_subject_collection("123")
            assert result == {}

    def test_success(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"type": 3}
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_subject_collection("123")
            assert result["type"] == 3

    def test_non_dict(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [1]
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_subject_collection("123")
            assert result == {}

    def test_json_error(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("bad")
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_subject_collection("123")
            assert result == {}


class TestGetEpCollection:
    """测试 get_ep_collection"""

    def test_404_returns_empty(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_ep_collection("ep1")
            assert result == {}

    def test_success(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"type": 2}
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_ep_collection("ep1")
            assert result["type"] == 2

    def test_non_dict(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = "str"
        with patch.object(api, "get", return_value=mock_resp):
            result = api.get_ep_collection("ep1")
            assert result == {}


class TestEnsureSubjectWatching:
    """测试 ensure_subject_watching"""

    def test_already_watching(self):
        api = BangumiApi()
        with patch.object(api, "get_subject_collection", return_value={"type": 3}):
            assert api.ensure_subject_watching("123") == 0

    def test_watched_returns_0(self):
        api = BangumiApi()
        with patch.object(api, "get_subject_collection", return_value={"type": 2}):
            assert api.ensure_subject_watching("123") == 0

    def test_not_collected_adds(self):
        api = BangumiApi()
        with (
            patch.object(api, "get_subject_collection", return_value={}),
            patch.object(api, "add_collection_subject"),
        ):
            assert api.ensure_subject_watching("123") == 1

    def test_wish_to_watching(self):
        api = BangumiApi()
        with (
            patch.object(api, "get_subject_collection", return_value={"type": 1}),
            patch.object(api, "change_collection_state"),
        ):
            assert api.ensure_subject_watching("123") == 1

    def test_on_hold_to_watching(self):
        api = BangumiApi()
        with (
            patch.object(api, "get_subject_collection", return_value={"type": 4}),
            patch.object(api, "change_collection_state"),
        ):
            assert api.ensure_subject_watching("123") == 1

    def test_dropped_returns_0(self):
        api = BangumiApi()
        with patch.object(api, "get_subject_collection", return_value={"type": 5}):
            assert api.ensure_subject_watching("123") == 0


class TestMarkEpisodeWatched:
    """测试 mark_episode_watched"""

    def test_not_collected_marks_watching(self):
        api = BangumiApi()
        with (
            patch.object(api, "get_subject_collection", return_value={}),
            patch.object(api, "add_collection_subject"),
            patch.object(api, "change_episode_state"),
        ):
            result = api.mark_episode_watched("s1", "e1")
            assert result == 2

    def test_already_watched_subject(self):
        api = BangumiApi()
        with patch.object(api, "get_subject_collection", return_value={"type": 2}):
            assert api.mark_episode_watched("s1", "e1") == 0

    def test_wish_subject_then_watch_ep(self):
        api = BangumiApi()
        with (
            patch.object(api, "get_subject_collection", return_value={"type": 1}),
            patch.object(api, "change_collection_state"),
            patch.object(api, "get_ep_collection", return_value={"type": 1}),
            patch.object(api, "change_episode_state") as mock_ep,
        ):
            result = api.mark_episode_watched("s1", "e1")
            assert result == 1
            mock_ep.assert_called_once()

    def test_episode_already_watched(self):
        api = BangumiApi()
        with (
            patch.object(api, "get_subject_collection", return_value={"type": 3}),
            patch.object(api, "get_ep_collection", return_value={"type": 2}),
        ):
            assert api.mark_episode_watched("s1", "e1") == 0

    def test_episode_not_watched(self):
        api = BangumiApi()
        with (
            patch.object(api, "get_subject_collection", return_value={"type": 3}),
            patch.object(api, "get_ep_collection", return_value={"type": 1}),
            patch.object(api, "change_episode_state"),
        ):
            result = api.mark_episode_watched("s1", "e1")
            assert result == 1


class TestChangeEpisodeState:
    """测试 change_episode_state"""

    def test_success(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(api, "put", return_value=mock_resp):
            result = api.change_episode_state("ep1", state=2)
            assert result == mock_resp

    def test_334_raises(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 334
        mock_resp.text = "error"
        with (
            patch.object(api, "put", return_value=mock_resp),
            pytest.raises(ValueError),
        ):
            api.change_episode_state("ep1")


class TestSequelNextTvSubjectId:
    """测试 _sequel_next_tv_subject_id"""

    def test_list_with_sequel(self):
        api = BangumiApi()
        with patch.object(
            api,
            "get_related_subjects",
            return_value=[{"relation": "续集", "id": 456}],
        ):
            result = api._sequel_next_tv_subject_id("123")
            assert result == 456

    def test_dict_with_sequel(self):
        api = BangumiApi()
        with patch.object(
            api,
            "get_related_subjects",
            return_value={"data": [{"relation": "续集", "id": 789}]},
        ):
            result = api._sequel_next_tv_subject_id("123")
            assert result == 789

    def test_no_sequel(self):
        api = BangumiApi()
        with patch.object(
            api,
            "get_related_subjects",
            return_value=[{"relation": "前传", "id": 1}],
        ):
            result = api._sequel_next_tv_subject_id("123")
            assert result is None

    def test_unexpected_type(self):
        api = BangumiApi()
        with patch.object(api, "get_related_subjects", return_value="str"):
            result = api._sequel_next_tv_subject_id("123")
            assert result is None


class TestMatchTargetEpRows:
    """测试 _match_target_ep_rows"""

    def test_sort_match(self):
        api = BangumiApi()
        rows = api._match_target_ep_rows(
            [{"sort": 5, "id": 1}, {"sort": 3, "id": 2}], 5
        )
        assert len(rows) == 1
        assert rows[0]["id"] == 1

    def test_ep_fallback(self):
        api = BangumiApi()
        rows = api._match_target_ep_rows(
            [{"ep": 5, "sort": 5, "id": 1}, {"ep": 3, "sort": 10, "id": 2}], 3
        )
        assert len(rows) == 1
        assert rows[0]["id"] == 2

    def test_no_match(self):
        api = BangumiApi()
        rows = api._match_target_ep_rows([{"sort": 1, "id": 1}], 99)
        assert rows == []


class TestGetMovieMainEpisodeId:
    """测试 get_movie_main_episode_id"""

    def test_no_episodes(self):
        api = BangumiApi()
        with patch.object(api, "get_episodes", return_value={"data": []}):
            sid, eid = api.get_movie_main_episode_id("123")
            assert sid == "123"
            assert eid is None

    def test_type0_match(self):
        api = BangumiApi()
        eps = {
            "data": [
                {"type": 0, "sort": 1, "id": "ep1"},
                {"type": 1, "sort": 1, "id": "ep2"},
            ]
        }
        with patch.object(api, "get_episodes", return_value=eps):
            sid, eid = api.get_movie_main_episode_id("123", target_sort=1)
            assert eid == "ep1"

    def test_fallback_sorted(self):
        api = BangumiApi()
        eps = {"data": [{"sort": 2, "id": "ep2"}, {"sort": 1, "id": "ep1"}]}
        with patch.object(api, "get_episodes", return_value=eps):
            sid, eid = api.get_movie_main_episode_id("123", target_sort=99)
            assert eid == "ep1"


class TestGetTargetSeasonEpisodeId:
    """测试 get_target_season_episode_id"""

    _MOCK_SUBJECT = {"id": 123, "type": 2, "name": "test", "name_cn": ""}

    def test_season_gt_limit_returns_none(self):
        api = BangumiApi()
        with patch.object(api, "_get_episode_sync_limits", return_value=(100, 9999)):
            result = api.get_target_season_episode_id("123", 101, 1)
        assert result == (None, None)

    def test_ep_gt_limit_returns_none(self):
        api = BangumiApi()
        with patch.object(api, "_get_episode_sync_limits", return_value=(100, 9999)):
            result = api.get_target_season_episode_id("123", 1, 10000)
        assert result == (None, None)

    def test_ep_100_season1_uses_long_series_path(self):
        api = BangumiApi()
        with (
            patch.object(api, "get_subject", return_value=self._MOCK_SUBJECT),
            patch.object(
                api,
                "_find_episode_by_sort",
                return_value={"id": "ep100", "sort": 100},
            ) as mock_find,
        ):
            result = api.get_target_season_episode_id("899", 1, 100)
        mock_find.assert_called_once_with("899", 100)
        assert result == ("899", "ep100")

    def test_is_season_subject_id_no_target_ep(self):
        api = BangumiApi()
        with patch.object(api, "get_subject", return_value=self._MOCK_SUBJECT):
            result = api.get_target_season_episode_id(
                "123", 1, 0, is_season_subject_id=True
            )
        # P0-3 修复: 返回 tuple (subject_id, None) 保持解包契约
        assert result == ("123", None)

    def test_is_season_subject_id_match_sort(self):
        api = BangumiApi()
        with (
            patch.object(api, "get_subject", return_value=self._MOCK_SUBJECT),
            patch.object(
                api,
                "_find_episode_by_sort",
                return_value={"sort": 3, "id": "ep3"},
            ),
        ):
            result = api.get_target_season_episode_id(
                "123", 1, 3, is_season_subject_id=True
            )
            assert result == ("123", "ep3")

    def test_is_season_subject_id_match_ep(self):
        api = BangumiApi()
        with (
            patch.object(api, "get_subject", return_value=self._MOCK_SUBJECT),
            patch.object(
                api,
                "_find_episode_by_sort",
                return_value={"ep": 3, "sort": 3, "id": "ep3"},
            ),
        ):
            result = api.get_target_season_episode_id(
                "123", 1, 3, is_season_subject_id=True
            )
            assert result == ("123", "ep3")

    def test_is_season_subject_id_no_match_fallback(self):
        """指定季度ID未匹配到集数，回退到传统方法"""
        api = BangumiApi()
        with (
            patch.object(api, "get_subject", return_value=self._MOCK_SUBJECT),
            patch.object(api, "_find_episode_by_sort", return_value=None),
            patch.object(api, "get_episodes", return_value={"data": []}),
            patch.object(api, "get_related_subjects", return_value=[]),
        ):
            result = api.get_target_season_episode_id(
                "123", 1, 5, is_season_subject_id=True
            )
            # 回退后 season==1 且 no ep data → breaks loop → returns (None, None)
            assert result == (None, None)

    def test_season1_no_ep(self):
        api = BangumiApi()
        with patch.object(api, "get_subject", return_value=self._MOCK_SUBJECT):
            result = api.get_target_season_episode_id("123", 1, 0)
        # P0-3 修复: 返回 tuple (subject_id, None) 保持解包契约
        assert result == ("123", None)

    @pytest.fixture
    def archive_517106_db(self, tmp_path):
        """构建 subject 517106（第二期）原始 Archive 库：全局连续 sort 13..24，无 ep 字段"""
        db_path = tmp_path / "archive_517106.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE episode ("
            "id INTEGER, name TEXT, name_cn TEXT, description TEXT, "
            "airdate TEXT, disc INTEGER, duration INTEGER, "
            "subject_id INTEGER, sort INTEGER, type INTEGER)"
        )
        rows = [
            (13, "EP1", "", "", "2025-01-01", 0, 0, 517106, 13, 0),
            (14, "EP2", "", "", "2025-01-08", 0, 0, 517106, 14, 0),
            (15, "EP3", "", "", "2025-01-15", 0, 0, 517106, 15, 0),
            (16, "EP4", "", "", "2025-01-22", 0, 0, 517106, 16, 0),
            (17, "EP5", "", "", "2025-01-29", 0, 0, 517106, 17, 0),
            (18, "EP6", "", "", "2025-02-05", 0, 0, 517106, 18, 0),
            (19, "EP7", "", "", "2025-02-12", 0, 0, 517106, 19, 0),
            (20, "EP8", "", "", "2025-02-19", 0, 0, 517106, 20, 0),
            (21, "EP9", "", "", "2025-02-26", 0, 0, 517106, 21, 0),
            (22, "EP10", "", "", "2025-03-05", 0, 0, 517106, 22, 0),
            (23, "EP11", "", "", "2025-03-12", 0, 0, 517106, 23, 0),
            (24, "EP12", "", "", "2025-03-19", 0, 0, 517106, 24, 0),
            (25, "SP", "", "", "2025-03-26", 0, 0, 517106, 25, 3),
        ]
        conn.executemany(
            "INSERT INTO episode (id,name,name_cn,description,airdate,disc,"
            "duration,subject_id,sort,type) VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        conn.close()
        return db_path

    @pytest.fixture
    def archive_sort_reset_db(self, tmp_path):
        """构建 subject 900002 的 Archive 库：多季合并条目，sort 每季重置为 1"""
        db_path = tmp_path / "archive_sort_reset.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE episode ("
            "id INTEGER, name TEXT, name_cn TEXT, description TEXT, "
            "airdate TEXT, disc INTEGER, duration INTEGER, "
            "subject_id INTEGER, sort INTEGER, type INTEGER)"
        )
        rows = [
            (301, "EP1", "", "", "2025-01-01", 0, 0, 900002, 1, 0),
            (302, "EP2", "", "", "2025-01-08", 0, 0, 900002, 2, 0),
            (303, "EP3", "", "", "2025-01-15", 0, 0, 900002, 3, 0),
            (304, "EP4", "", "", "2025-01-22", 0, 0, 900002, 4, 0),
            (305, "EP5", "", "", "2025-01-29", 0, 0, 900002, 5, 0),
            (306, "EP1", "", "", "2025-02-05", 0, 0, 900002, 1, 0),
            (307, "EP2", "", "", "2025-02-12", 0, 0, 900002, 2, 0),
            (308, "EP3", "", "", "2025-02-19", 0, 0, 900002, 3, 0),
            (309, "EP4", "", "", "2025-02-26", 0, 0, 900002, 4, 0),
            (310, "EP5", "", "", "2025-03-05", 0, 0, 900002, 5, 0),
        ]
        conn.executemany(
            "INSERT INTO episode (id,name,name_cn,description,airdate,disc,"
            "duration,subject_id,sort,type) VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        conn.close()
        return db_path

    def _enable_archive_with_db(self, api, db_path):
        from app.utils.bangumi_archive import _archive as _arch_mod

        orig = (
            _arch_mod.bangumi_archive.db_a_path,
            _arch_mod.bangumi_archive._meta.active,
            api._archive._enabled,
        )
        _arch_mod.bangumi_archive.db_a_path = db_path
        _arch_mod.bangumi_archive._meta.active = "a"
        api._archive._enabled = True
        return orig

    def _restore_archive(self, api, orig):
        from app.utils.bangumi_archive import _archive as _arch_mod

        _arch_mod.bangumi_archive.db_a_path = orig[0]
        _arch_mod.bangumi_archive._meta.active = orig[1]
        api._archive._enabled = orig[2]

    def test_archive_synthesized_ep_resolves_season_local(self, archive_517106_db):
        """Archive 补全 ep 后，is_season_subject_id 按季内话数定位章节

        subject 517106 是第二期（全局连续 sort 13..24，无 sort=6），Archive 原始 dump
        不含 ep，依赖 get_episodes 边界补全 ep=1..12，下游 _match_target_ep_rows 才能
        按 ep 回退匹配到 sort=18（id=18）。本测试走真实 try_get_episodes → get_episodes
        路径，是补全逻辑端到端回归测试。
        """
        api = BangumiApi()
        orig = self._enable_archive_with_db(api, archive_517106_db)
        try:
            with (
                patch.object(
                    api,
                    "get_subject",
                    return_value={
                        "id": 517106,
                        "type": 2,
                        "name": "逃げ上手の若君 第二期",
                    },
                ),
                patch.object(api, "get_related_subjects", return_value=[]),
            ):
                subject_id, episode_id = api.get_target_season_episode_id(
                    "517106", 2, 6, is_season_subject_id=True
                )
            assert subject_id == "517106"
            assert episode_id == 18
        finally:
            self._restore_archive(api, orig)

    def test_archive_synthesized_ep_first_maps_to_sort13(self, archive_517106_db):
        """季内第 1 话应映射到全局 sort=13（id=13）"""
        api = BangumiApi()
        orig = self._enable_archive_with_db(api, archive_517106_db)
        try:
            with (
                patch.object(
                    api,
                    "get_subject",
                    return_value={
                        "id": 517106,
                        "type": 2,
                        "name": "逃げ上手の若君 第二期",
                    },
                ),
                patch.object(api, "get_related_subjects", return_value=[]),
            ):
                subject_id, episode_id = api.get_target_season_episode_id(
                    "517106", 2, 1, is_season_subject_id=True
                )
            assert subject_id == "517106"
            assert episode_id == 13
        finally:
            self._restore_archive(api, orig)

    def test_sort_reset_subject_uses_sort_jump_detection(self, archive_sort_reset_db):
        """多季合并条目（sort 每季重置为 1）不补全 ep，季边界仍由 sort 重置检测定位

        Archive 缺 ep 时上游依赖 sort 由高值跳回 1 判定新季。补全 ep 不得使该检测
        失效，否则同一条目内的第 2 季将无法定位。
        """
        api = BangumiApi()
        orig = self._enable_archive_with_db(api, archive_sort_reset_db)
        try:
            with (
                patch.object(
                    api,
                    "get_subject",
                    return_value={"id": 900002, "type": 2, "name": "多季合并条目"},
                ),
                patch.object(api, "get_related_subjects", return_value=[]),
            ):
                result = api._try_resolve_continuous_season_episode(900002, 2, 3)
            assert result == (900002, 308)
        finally:
            self._restore_archive(api, orig)


class TestTitleDiffRatio:
    """测试 title_diff_ratio"""

    def test_exact_match_name(self):
        data = {"name": "测试标题"}
        ratio = BangumiApi.title_diff_ratio("测试标题", None, data)
        assert ratio == 1.0

    def test_exact_match_name_cn(self):
        data = {"name_cn": "中文标题"}
        ratio = BangumiApi.title_diff_ratio("中文标题", None, data)
        assert ratio == 1.0

    def test_infobox_alias_dict_v(self):
        data = {
            "name": "原名",
            "infobox": [{"key": "别名", "value": [{"v": "别名1"}, {"v": "别名2"}]}],
        }
        ratio = BangumiApi.title_diff_ratio("别名1", None, data)
        assert ratio > 0.9

    def test_infobox_alias_string_list(self):
        data = {
            "name": "原名",
            "infobox": [{"key": "别名", "value": ["别名A", "别名B"]}],
        }
        ratio = BangumiApi.title_diff_ratio("别名A", None, data)
        assert ratio > 0.9

    def test_infobox_alias_string_value(self):
        data = {
            "name": "原名",
            "infobox": [{"key": "别名", "value": "单个别名"}],
        }
        ratio = BangumiApi.title_diff_ratio("单个别名", None, data)
        assert ratio > 0.9

    def test_no_match(self):
        data = {"name": "完全不同的标题"}
        ratio = BangumiApi.title_diff_ratio("随便搜", "also different", data)
        assert ratio < 1.0

    def test_ori_title_used(self):
        data = {"name": "abc"}
        ratio = BangumiApi.title_diff_ratio("xyz", "abc", data)
        assert ratio == 1.0

    def test_empty_candidates(self):
        data = {}
        ratio = BangumiApi.title_diff_ratio("title", None, data)
        assert ratio == 0.0

    # === 后缀剥离 + 核心标题包含检查 ===

    def test_suffix_strip_containment_zhetian(self):
        """遮天动画版 应匹配 遮天 第四季（核心标题包含）"""
        data = {"name": "遮天 第四季", "name_cn": "遮天 第四季"}
        ratio = BangumiApi.title_diff_ratio("遮天动画版", None, data)
        assert ratio > 0.5

    def test_suffix_strip_containment_jianlai(self):
        """剑来动画版 应匹配 剑来（别名包含核心标题）"""
        data = {
            "name": "剑来",
            "name_cn": "剑来",
            "infobox": [
                {
                    "key": "别名",
                    "value": [{"v": "剑来 动画版"}, {"v": "Sword of Coming"}],
                }
            ],
        }
        ratio = BangumiApi.title_diff_ratio("剑来动画版", None, data)
        assert ratio > 0.5

    def test_shared_suffix_blocking(self):
        """遮天动画版 不应匹配 剑来（共享后缀但核心不相关）"""
        data = {
            "name": "剑来",
            "name_cn": "剑来",
            "infobox": [
                {"key": "别名", "value": [{"v": "剑来 动画版"}, {"v": "剑来 第一季"}]}
            ],
        }
        ratio = BangumiApi.title_diff_ratio("遮天动画版", None, data)
        assert ratio <= 0.5

    def test_shared_suffix_blocking_reverse(self):
        """剑来动画版 不应匹配 遮天（反向验证）"""
        data = {"name": "遮天 第四季", "name_cn": "遮天 第四季"}
        ratio = BangumiApi.title_diff_ratio("剑来动画版", None, data)
        assert ratio <= 0.5

    def test_suffix_strip_dongman(self):
        """动漫版后缀剥离"""
        data = {"name": "一念永恒", "name_cn": "一念永恒"}
        ratio = BangumiApi.title_diff_ratio("一念永恒动漫版", None, data)
        assert ratio > 0.5

    def test_no_false_strip_short_title(self):
        """短标题不应被误剥离（如"动画"本身）"""
        data = {"name": "动画", "name_cn": "动画"}
        ratio = BangumiApi.title_diff_ratio("动画", None, data)
        assert ratio == 1.0

    # === partial_ratio 维度 ===

    def test_partial_ratio_boost(self):
        """部分匹配通过 partial_ratio 提升分数"""
        data = {"name": "完美世界剧场版 九劫焚天"}
        ratio = BangumiApi.title_diff_ratio("完美世界", None, data)
        assert ratio > 0.5

    # === 真实数据回归 ===

    def test_real_wanmei_shijie(self):
        """完美世界 匹配 完美世界 第三季"""
        data = {"name": "完美世界 第三季", "name_cn": "完美世界 第三季"}
        ratio = BangumiApi.title_diff_ratio("完美世界", None, data)
        assert ratio > 0.5

    def test_real_fanren(self):
        """凡人修仙传 精确匹配"""
        data = {"name": "凡人修仙传", "name_cn": "凡人修仙传"}
        ratio = BangumiApi.title_diff_ratio("凡人修仙传", None, data)
        assert ratio == 1.0

    def test_real_eva(self):
        """新世纪福音战士 匹配 name_cn"""
        data = {"name": "新世紀エヴァンゲリオン", "name_cn": "新世纪福音战士"}
        ratio = BangumiApi.title_diff_ratio("新世纪福音战士", None, data)
        assert ratio == 1.0

    def test_real_one_piece(self):
        """海贼王 匹配 name_cn"""
        data = {"name": "ONE PIECE", "name_cn": "海贼王"}
        ratio = BangumiApi.title_diff_ratio("海贼王", None, data)
        assert ratio == 1.0

    def test_real_zhetian_season4(self):
        """遮天 第四季 精确匹配"""
        data = {"name": "遮天 第四季", "name_cn": "遮天 第四季"}
        ratio = BangumiApi.title_diff_ratio("遮天 第四季", None, data)
        assert ratio == 1.0

    def test_decorator_and_whitespace_equivalence(self):
        """仅差空格/修饰词（年番）的标题应识别为等价，不被低估误沉淀。

        复现「斗破苍穹年番」类误沉淀：媒体库标题带"年番"修饰词且可能与
        Bangumi 条目的空格写法不同，归一化后应判为完全匹配（>=0.9），
        不再因空格/修饰词差异打出 0.56 这类低于阈值 0.6 的低分。
        """
        data = {"name": "斗破苍穹 年番", "name_cn": "斗破苍穹 年番"}
        # 媒体库写法：修饰词紧贴、无空格
        ratio_a = BangumiApi.title_diff_ratio("斗破苍穹年番", None, data)
        # 媒体库写法：修饰词带空格
        ratio_b = BangumiApi.title_diff_ratio("斗破苍穹 年番", None, data)
        assert ratio_a >= 0.9
        assert ratio_b >= 0.9

    def test_real_kamen_rider(self):
        """假面骑士加布 匹配 name_cn"""
        data = {"name": "仮面ライダーガヴ", "name_cn": "假面骑士加布"}
        ratio = BangumiApi.title_diff_ratio("假面骑士加布", None, data)
        assert ratio == 1.0

    def test_ori_title_core_containment(self):
        """当 ori_title 核心匹配时不应被限制"""
        data = {
            "name": "剑来",
            "name_cn": "剑来",
            "infobox": [{"key": "别名", "value": [{"v": "剑来 动画版"}]}],
        }
        # title="遮天动画版" 核心不匹配，但 ori_title="剑来" 核心匹配
        ratio = BangumiApi.title_diff_ratio("遮天动画版", "剑来", data)
        assert ratio > 0.5


class TestBgmSearch:
    """测试 bgm_search"""

    def test_precise_search_with_date(self):
        api = BangumiApi()
        with (
            patch.object(api, "search", return_value=[{"id": 1, "name": "番剧"}]),
            patch.object(api, "title_diff_ratio", return_value=0.9),
        ):
            result = api.bgm_search("番剧", "original", "2024-01-15")
            assert result is not None

    def test_precise_search_ori_title_first(self):
        api = BangumiApi()
        calls = []

        def mock_search(title, **kwargs):
            calls.append(title)
            if title == "original":
                return [{"id": 1, "name": "orig"}]
            return []

        with (
            patch.object(api, "search", side_effect=mock_search),
            patch.object(api, "title_diff_ratio", return_value=0.9),
        ):
            api.bgm_search("中文", "original", "2024-01-15")
            assert calls[0] == "original"

    def test_movie_wider_date_range(self):
        """覆盖 is_movie=True 分支"""
        api = BangumiApi()
        search_calls = []

        def mock_search(title, start_date, end_date, **kw):
            search_calls.append(end_date)
            if len(search_calls) >= 3:
                return [{"id": 1, "name": "Movie"}]
            return []

        with (
            patch.object(api, "search", side_effect=mock_search),
            patch.object(api, "title_diff_ratio", return_value=0.9),
        ):
            result = api.bgm_search("Movie", "ori", "2024-01-15", is_movie=True)
            assert result is not None

    def test_fallback_to_search(self):
        """精确搜索无结果时降级到兜底 search"""
        api = BangumiApi()

        def mock_search(title, start_date="", end_date="", **kwargs):
            if start_date or end_date:
                return []  # 精确搜索返回空
            return [{"id": 10}]  # 兜底搜索命中

        with (
            patch.object(api, "search", side_effect=mock_search),
            patch.object(api, "title_diff_ratio", return_value=0.9),
        ):
            result = api.bgm_search("番", "", "2024-01-15")
            assert result is not None

    def test_fallback_low_ratio_skips(self):
        """兜底搜索候选相似度低于阈值时跳过"""
        api = BangumiApi()

        def mock_search(title, start_date="", end_date="", **kwargs):
            if start_date or end_date:
                return []
            return [{"id": 10}]

        with (
            patch.object(api, "search", side_effect=mock_search),
            patch.object(api, "title_diff_ratio", return_value=0.1),
        ):
            result = api.bgm_search("完全不同", "", "2024-01-15")
            assert result is None

    def test_no_date_search(self):
        api = BangumiApi()
        with patch.object(api, "search", return_value=[]):
            result = api.bgm_search("title", "", "")
            assert result is None

    def test_invalid_date_fallback(self):
        """无效日期降级到无日期搜索"""
        api = BangumiApi()
        with patch.object(api, "search", return_value=[]):
            result = api.bgm_search("title", "", "bad-date-format")
            assert result is None

    def test_low_similarity_triggers_fallback(self):
        """精确搜索相似度低于0.5时触发兜底搜索（P1-3 修复后保留低相似度候选）

        修复前：精确搜索命中低相似度候选(0.2) → 触发兜底 → 兜底全 miss →
        清空 bgm_data → 返回 None，候选被丢弃无法沉淀。
        修复后：兜底全 miss 时保留低相似度候选 → 返回非空列表，
        供下游 APISearchStep 执行置信度检查并沉淀为 pending_candidate。
        """
        api = BangumiApi()
        with (
            patch.object(api, "search", return_value=[{"id": 1}]),
            patch.object(api, "title_diff_ratio", return_value=0.2),
        ):
            result = api.bgm_search("title", "", "2024-01-15")
            # 兜底被触发（search 被多次调用：日期精确 + 多个变体）
            assert api.search.call_count > 1
            # 修复后保留低相似度候选供沉淀
            assert result is not None
            assert len(result) > 0

    def test_v0_api_tries_stripped_title(self):
        """v0 API 路径：原始 title miss 时应尝试剥离后缀的变体

        场景：fongmi 推送「完美世界 S06E279」，archive 禁用或 miss 后
        降级到 API，原始标题无结果，剥离后用「完美世界」命中。
        """
        api = BangumiApi()
        search_calls = []

        def mock_search(title, **kwargs):
            search_calls.append(title)
            # 只有剥离后缀的「完美世界」命中
            if title == "完美世界":
                return [{"id": 1, "name": "完美世界"}]
            return []

        with (
            patch.object(api, "search", side_effect=mock_search),
            patch.object(api, "title_diff_ratio", return_value=0.9),
        ):
            result = api.bgm_search("完美世界 S06E279", "", "2026-01-15")
            assert result is not None
            # 应依次尝试原始 title 和 stripped title
            assert "完美世界 S06E279" in search_calls
            assert "完美世界" in search_calls

    def test_v0_api_tries_stripped_ori_title(self):
        """v0 API 路径：原始 ori_title miss 时应尝试剥离后缀的变体"""
        api = BangumiApi()
        search_calls = []

        def mock_search(title, **kwargs):
            search_calls.append(title)
            if title == "Original":
                return [{"id": 1, "name": "Original"}]
            return []

        with (
            patch.object(api, "search", side_effect=mock_search),
            patch.object(api, "title_diff_ratio", return_value=0.9),
        ):
            # ori_title 含 S02E10 后缀，剥离后为 "Original"
            result = api.bgm_search("中文", "Original S02E10", "2026-01-15")
            assert result is not None
            assert "Original S02E10" in search_calls
            assert "Original" in search_calls

    def test_fallback_tries_stripped_title(self):
        """兜底搜索路径：应尝试剥离后缀的变体"""
        api = BangumiApi()
        fallback_calls = []

        def mock_search(title, start_date="", end_date="", **kwargs):
            if start_date or end_date:
                return []  # 精确搜索全部 miss
            # 兜底搜索记录调用
            fallback_calls.append(title)
            if title == "完美世界":
                return [{"id": 1, "name": "完美世界"}]
            return []

        with (
            patch.object(api, "search", side_effect=mock_search),
            patch.object(api, "title_diff_ratio", return_value=0.9),
        ):
            result = api.bgm_search("完美世界 S06E279", "", "2026-01-15")
            assert result is not None
            # 兜底搜索应尝试剥离后缀的「完美世界」
            assert "完美世界" in fallback_calls

    def test_no_suffix_skips_stripped_variant(self):
        """标题不含季数后缀时不应尝试 stripped 变体（避免重复查询）"""
        api = BangumiApi()
        search_calls = []

        def mock_search(title, **kwargs):
            search_calls.append(title)
            return [{"id": 1, "name": "番剧"}]

        with (
            patch.object(api, "search", side_effect=mock_search),
            patch.object(api, "title_diff_ratio", return_value=0.9),
        ):
            # 无后缀标题，stripped == title，不应重复查询
            api.bgm_search("普通番剧", "", "2026-01-15")
            # search 只应被调用一次（原始标题命中）
            assert search_calls.count("普通番剧") == 1


class TestParseIsoDateYmd:
    """测试 _parse_iso_date_ymd"""

    def test_valid_date(self):
        assert BangumiApi._parse_iso_date_ymd("2024-01-15") is not None

    def test_short_string(self):
        assert BangumiApi._parse_iso_date_ymd("2024") is None

    def test_none(self):
        assert BangumiApi._parse_iso_date_ymd(None) is None

    def test_empty(self):
        assert BangumiApi._parse_iso_date_ymd("") is None

    def test_invalid_format(self):
        assert BangumiApi._parse_iso_date_ymd("2024-13-01") is None

    def test_bangumi_unpadded_month_day(self):
        d = BangumiApi._parse_iso_date_ymd("1996-01-8")
        assert d.year == 1996 and d.month == 1 and d.day == 8
        d2 = BangumiApi._parse_iso_date_ymd("2008-3-17")
        assert d2.year == 2008 and d2.month == 3 and d2.day == 17

    def test_iso_datetime_prefix(self):
        d = BangumiApi._parse_iso_date_ymd("2024-06-15T12:00:00")
        assert d.year == 2024 and d.month == 6 and d.day == 15


class TestTryResolveSequelByAirdate:
    """测试 _try_resolve_sequel_by_airdate"""

    def test_invalid_release_date(self):
        api = BangumiApi()
        result = api._try_resolve_sequel_by_airdate("123", 1, "bad-date")
        assert result is None

    def test_no_sequel(self):
        api = BangumiApi()
        with patch.object(api, "_sequel_next_tv_subject_id", return_value=None):
            result = api._try_resolve_sequel_by_airdate("123", 1, "2024-01-15")
            assert result is None

    def test_best_match_found(self):
        api = BangumiApi()
        with (
            patch.object(
                api,
                "_sequel_next_tv_subject_id",
                side_effect=[456, None],
            ),
            patch.object(
                api,
                "get_subject",
                return_value={"platform": "TV"},
            ),
            patch.object(
                api,
                "get_episodes",
                return_value={
                    "data": [{"sort": 1, "id": "ep1", "airdate": "2024-01-16"}]
                },
            ),
        ):
            result = api._try_resolve_sequel_by_airdate("123", 1, "2024-01-15")
            assert result is not None
            assert result[0] == 456

    def test_diff_too_large(self):
        api = BangumiApi()
        with (
            patch.object(
                api,
                "_sequel_next_tv_subject_id",
                side_effect=[456, None],
            ),
            patch.object(
                api,
                "get_subject",
                return_value={"platform": "TV"},
            ),
            patch.object(
                api,
                "get_episodes",
                return_value={
                    "data": [{"sort": 1, "id": "ep1", "airdate": "2025-06-01"}]
                },
            ),
        ):
            result = api._try_resolve_sequel_by_airdate("123", 1, "2024-01-15")
            assert result is None


class TestLongSeriesEpisodeSync:
    """超长连载番剧章节匹配"""

    def test_find_episode_by_sort_offset_fast_path(self):
        api = BangumiApi()
        page = {"data": [{"id": 20606, "sort": 500, "ep": 500}], "total": 1328}
        with patch.object(api, "_fetch_episodes_page", return_value=page):
            found = api._find_episode_by_sort("899", 500)
        assert found is not None
        assert found["id"] == 20606

    def test_find_episode_by_sort_offset_mismatch_falls_back(self):
        api = BangumiApi()
        bad_page = {"data": [{"id": 1, "sort": 12, "ep": 12}], "total": 1328}
        full_eps = {
            "data": [{"id": 999, "sort": 500, "ep": 500}],
            "total": 1328,
        }
        with (
            patch.object(api, "_fetch_episodes_page", return_value=bad_page),
            patch.object(api, "get_episodes", return_value=full_eps),
        ):
            found = api._find_episode_by_sort("899", 500)
        assert found is not None
        assert found["id"] == 999

    def test_find_episode_by_sort_archive_short_circuits_api(self):
        """archive 命中时应直接返回，不调用任何 API（_fetch_episodes_page / get_episodes）"""
        from app.utils.bangumi_api._archive_shortcut import ShortcutResult

        api = BangumiApi()
        # archive 短路命中：577198 第六季 sort=279
        archive_data = [{"id": 1552078, "sort": 279, "type": 0, "subject_id": 577198}]
        with patch.object(
            api._archive,
            "try_get_episodes",
            return_value=ShortcutResult(True, archive_data, "archive_hit"),
        ):
            with patch.object(api, "_fetch_episodes_page") as mock_fetch:
                with patch.object(api, "get_episodes") as mock_get:
                    found = api._find_episode_by_sort(577198, 279)
        assert found is not None
        assert found["id"] == 1552078
        # archive 命中：不应调用任何 API
        mock_fetch.assert_not_called()
        mock_get.assert_not_called()

    def test_find_episode_by_sort_archive_miss_falls_back_to_offset(self):
        """archive 未命中时应走 offset 快速路径"""
        from app.utils.bangumi_api._archive_shortcut import ShortcutResult

        api = BangumiApi()
        page = {"data": [{"id": 20606, "sort": 500, "ep": 500}], "total": 1328}
        with patch.object(
            api._archive,
            "try_get_episodes",
            return_value=ShortcutResult(False, None, "archive_miss"),
        ):
            with patch.object(api, "_fetch_episodes_page", return_value=page):
                found = api._find_episode_by_sort("899", 500)
        assert found is not None
        assert found["id"] == 20606

    def test_find_episode_by_sort_archive_hit_but_no_match_falls_back_to_api(self):
        """archive 命中但未找到该 sort 时应降级到 API 全量拉取"""
        from app.utils.bangumi_api._archive_shortcut import ShortcutResult

        api = BangumiApi()
        # archive 命中但数据中没有 sort=500
        archive_data = [{"id": 1, "sort": 1, "type": 0}]
        full_eps = {"data": [{"id": 999, "sort": 500, "ep": 500}], "total": 1328}
        with patch.object(
            api._archive,
            "try_get_episodes",
            return_value=ShortcutResult(True, archive_data, "archive_hit"),
        ):
            with patch.object(api, "_fetch_episodes_page") as mock_fetch:
                with patch.object(
                    api, "get_episodes", return_value=full_eps
                ) as mock_get:
                    found = api._find_episode_by_sort("899", 500)
        assert found is not None
        assert found["id"] == 999
        # archive 命中但未匹配：不应走 offset 快速路径，直接全量拉取
        mock_fetch.assert_not_called()
        mock_get.assert_called_once()

    def test_resolve_episode_by_airdate_in_subject(self):
        api = BangumiApi()
        episodes = {
            "total": 1328,
            "data": [
                {"id": 1, "sort": 1, "type": 0, "airdate": "1996-01-8"},
                {"id": 350, "sort": 350, "type": 0, "airdate": "2008-3-17"},
            ],
        }
        with patch.object(api, "get_episodes", return_value=episodes):
            result = api._resolve_episode_by_airdate_in_subject("899", "2008-03-17")
        assert result == ("899", 350)

    def test_resolve_episode_by_airdate_skips_short_series(self):
        api = BangumiApi()
        episodes = {
            "total": 12,
            "data": [{"id": 1, "sort": 1, "airdate": "2024-01-01"}],
        }
        with patch.object(api, "get_episodes", return_value=episodes):
            result = api._resolve_episode_by_airdate_in_subject("123", "2024-01-01")
        assert result is None

    def test_tvdb_multi_season_airdate_fallback(self):
        api = BangumiApi()
        with (
            patch.object(
                api,
                "get_subject",
                return_value={"id": 899, "type": 2, "name": "test", "name_cn": ""},
            ),
            patch.object(api, "get_related_subjects", return_value=[]),
            patch.object(
                api,
                "_resolve_episode_by_airdate_in_subject",
                return_value=("899", 350),
            ) as mock_air,
        ):
            result = api.get_target_season_episode_id(
                "899",
                15,
                12,
                release_date="2008-05-20",
            )
        mock_air.assert_called_once_with("899", "2008-05-20")
        assert result == ("899", 350)


# ===== 以下自 test_bangumi_api_internals.py 并入的独有断言 =====


def _session_resp_internals(status=200, json_body=None, json_exc=None):
    from unittest.mock import MagicMock

    r = MagicMock()
    r.status_code = status
    r.elapsed.total_seconds.return_value = 0.01
    r.headers = {}
    r.text = ""
    if json_exc:
        r.json.side_effect = json_exc
    else:
        r.json.return_value = json_body if json_body is not None else {}
    return r


class TestTryDirectConnectionInternals:
    """直连时 timeout/proxies/close 等参数细节断言。"""

    def test_get_success_adds_timeout_and_no_proxy(self):
        from unittest.mock import MagicMock, patch

        from app.utils.bangumi_api import BangumiApi

        mock_sess = MagicMock()
        out = _session_resp_internals(200, {})
        mock_sess.request.return_value = out
        with patch("app.utils.bangumi_api.httpx.Client", return_value=mock_sess):
            api = BangumiApi(access_token="tok")
            res = api._try_direct_connection("GET", "https://example.test/api")
        assert res is out
        call_kw = mock_sess.request.call_args.kwargs
        assert call_kw["timeout"] == 15
        assert call_kw.get("proxies") is None
        mock_sess.close.assert_called_once()

    def test_exception_reraises_after_close(self):
        from unittest.mock import MagicMock, patch

        import httpx
        import pytest

        from app.utils.bangumi_api import BangumiApi

        mock_sess = MagicMock()
        mock_sess.request.side_effect = httpx.TimeoutException("t")
        with patch("app.utils.bangumi_api.httpx.Client", return_value=mock_sess):
            api = BangumiApi()
            with pytest.raises(httpx.TimeoutException):
                api._try_direct_connection("GET", "https://example.test/x")
        mock_sess.close.assert_called_once()


class TestRequestWithRetryInternals:
    """重试耗尽通知与 get_me 认证细节。"""

    def test_http_500_exhausts_retries_and_notifies(self):
        from unittest.mock import MagicMock, patch

        import httpx
        import pytest

        from app.utils.bangumi_api import BangumiApi

        api = BangumiApi()
        bad = _session_resp_internals(500)
        api.req.request = MagicMock(return_value=bad)
        with (
            pytest.raises(httpx.HTTPStatusError),
            patch("app.services.notification_service.notification_service") as _notify,
        ):
            api._request_with_retry("GET", api.req, "https://bgm.test/r")
        _notify.notify.assert_called_once()

    def test_get_me_client_403_raises(self):
        from unittest.mock import MagicMock, patch

        import pytest

        from app.utils.bangumi_api import BangumiApi

        r = MagicMock()
        r.status_code = 403
        r.json.side_effect = AssertionError("should not json")
        with (
            patch.object(BangumiApi, "get", return_value=r),
            patch("app.services.notification_service.notification_service"),
            patch("os.name", "posix"),
        ):
            api = BangumiApi(username="u")
            with pytest.raises(ValueError, match="未授权"):
                api.get_me()


class TestSearchJsonBranchesInternals:
    """缓存命中时不应发起请求的断言。"""

    def test_search_cache_hit_skips_request(self):
        from unittest.mock import patch

        from app.utils.bangumi_api import BangumiApi

        api = BangumiApi()
        key = ("kw", "2020-01-01", "2020-02-01", 5, True, (2,))
        api._cache["search"][key] = [{"id": 9}]
        with patch.object(api, "_request_with_retry") as m:
            out = api.search("kw", "2020-01-01", "2020-02-01", limit=5, list_only=True)
        m.assert_not_called()
        assert out == [{"id": 9}]

    def test_parse_iso_date_edges(self):
        from app.utils.bangumi_api import BangumiApi

        assert BangumiApi._parse_iso_date_ymd("") is None
        assert BangumiApi._parse_iso_date_ymd("2020-01") is None
        assert BangumiApi._parse_iso_date_ymd("2020-13-40") is None
        d = BangumiApi._parse_iso_date_ymd("2024-06-15T12:00:00")
        assert d.year == 2024 and d.month == 6 and d.day == 15
