"""
Bangumi API 工具测试
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.utils.bangumi_api import BangumiApi


class TestBangumiApi:
    """Bangumi API 测试"""

    def test_init_default(self):
        api = BangumiApi()
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
        )
        assert api.username == "testuser"
        assert api.access_token == "test_token"
        assert api.private is False
        assert api.http_proxy == "http://proxy:8080"
        assert api.ssl_verify is False

    def test_init_sets_cache(self):
        api = BangumiApi()
        assert "search" in api._cache
        assert "search_old" in api._cache
        assert "get_subject" in api._cache
        assert "get_related_subjects" in api._cache
        assert "get_episodes" in api._cache

    def test_init_proxy_failed_flag(self):
        api = BangumiApi()
        assert api._proxy_failed is False

    def test_init_sets_headers(self):
        api = BangumiApi(access_token="test_token")
        assert "Accept" in api.req.headers
        assert "User-Agent" in api.req.headers

    def test_init_proxy_sets_proxies(self):
        api = BangumiApi(http_proxy="http://proxy:8080")
        assert api.req.proxies == {
            "http": "http://proxy:8080",
            "https": "http://proxy:8080",
        }

    def test_init_no_auth_header_on_not_auth_session(self):
        api = BangumiApi(access_token="test_token")
        assert "Authorization" not in api._req_not_auth.headers

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
            "search_old",
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

    def test_get_success(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("app.utils.bangumi_api.requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.get.return_value = mock_resp
            MockSession.return_value = mock_session
            result = api._try_direct_connection("GET", "https://example.com")
            assert result == mock_resp

    def test_post_success(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("app.utils.bangumi_api.requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.post.return_value = mock_resp
            MockSession.return_value = mock_session
            result = api._try_direct_connection("POST", "https://example.com", json={})
            assert result == mock_resp

    def test_put_success(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("app.utils.bangumi_api.requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.put.return_value = mock_resp
            MockSession.return_value = mock_session
            result = api._try_direct_connection("PUT", "https://example.com")
            assert result == mock_resp

    def test_patch_success(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("app.utils.bangumi_api.requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.patch.return_value = mock_resp
            MockSession.return_value = mock_session
            result = api._try_direct_connection("PATCH", "https://example.com")
            assert result == mock_resp

    def test_unsupported_method(self):
        api = BangumiApi()
        with patch("app.utils.bangumi_api.requests.Session") as MockSession:
            mock_session = MagicMock()
            MockSession.return_value = mock_session
            with pytest.raises(ValueError, match="不支持的HTTP方法"):
                api._try_direct_connection("DELETE", "https://example.com")

    def test_error_status_returns_none(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("app.utils.bangumi_api.requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.get.return_value = mock_resp
            MockSession.return_value = mock_session
            result = api._try_direct_connection("GET", "https://example.com")
            assert result is None

    def test_exception_reraises(self):
        api = BangumiApi()
        with patch("app.utils.bangumi_api.requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.get.side_effect = ConnectionError("fail")
            MockSession.return_value = mock_session
            with pytest.raises(ConnectionError):
                api._try_direct_connection("GET", "https://example.com")

    def test_removes_proxies_from_kwargs(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("app.utils.bangumi_api.requests.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.get.return_value = mock_resp
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
            mock_sock.connect_ex.side_effect = RuntimeError("sock err")
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
        mock_session.get.return_value = mock_resp
        result = api._request_with_retry("GET", mock_session, "https://example.com")
        assert result == mock_resp

    def test_retry_on_500_then_success(self):
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_resp1 = MagicMock()
        mock_resp1.status_code = 500
        mock_resp2 = MagicMock()
        mock_resp2.status_code = 200
        mock_session.get.side_effect = [mock_resp1, mock_resp2]
        with patch("app.utils.bangumi_api.time.sleep"):
            result = api._request_with_retry(
                "GET", mock_session, "https://example.com", max_retries=1
            )
        assert result == mock_resp2

    def test_retry_exhausted_raises(self):
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_session.get.return_value = mock_resp
        with (
            patch("app.utils.bangumi_api.time.sleep"),
            pytest.raises(requests.exceptions.HTTPError),
        ):
            api._request_with_retry(
                "GET", mock_session, "https://example.com", max_retries=0
            )

    def test_connection_error_retry_then_success(self):
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.get.side_effect = [
            requests.exceptions.ConnectionError("fail"),
            mock_resp,
        ]
        with patch("app.utils.bangumi_api.time.sleep"):
            result = api._request_with_retry(
                "GET", mock_session, "https://example.com", max_retries=1
            )
        assert result == mock_resp

    def test_connection_error_exhausted_no_proxy(self):
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_session.get.side_effect = requests.exceptions.ConnectionError("fail")
        with (
            patch("app.utils.bangumi_api.time.sleep"),
            pytest.raises(requests.exceptions.ConnectionError),
        ):
            api._request_with_retry(
                "GET", mock_session, "https://example.com", max_retries=0
            )

    def test_proxy_fallback_to_direct(self):
        api = BangumiApi(http_proxy="http://proxy:8080")
        mock_session = self._mock_session()
        mock_session.get.side_effect = requests.exceptions.ConnectionError("proxy fail")
        mock_direct = MagicMock()
        mock_direct.status_code = 200
        with (
            patch("app.utils.bangumi_api.time.sleep"),
            patch.object(api, "_try_direct_connection", return_value=mock_direct),
        ):
            result = api._request_with_retry(
                "GET", mock_session, "https://example.com", max_retries=0
            )
        assert result == mock_direct
        assert api._proxy_failed is True

    def test_proxy_fallback_direct_fails(self):
        api = BangumiApi(http_proxy="http://proxy:8080")
        mock_session = self._mock_session()
        mock_session.get.side_effect = requests.exceptions.ConnectionError("proxy fail")
        with (
            patch("app.utils.bangumi_api.time.sleep"),
            patch.object(
                api,
                "_try_direct_connection",
                side_effect=ConnectionError("direct fail"),
            ),
            pytest.raises(requests.exceptions.ConnectionError),
        ):
            api._request_with_retry(
                "GET", mock_session, "https://example.com", max_retries=0
            )

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
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_session.get.side_effect = requests.exceptions.ConnectionError(
            "Failed to resolve 'bad.host'"
        )
        with (
            patch("app.utils.bangumi_api.time.sleep"),
            patch.object(api, "_diagnose_network_issue") as mock_diag,
            pytest.raises(requests.exceptions.ConnectionError),
        ):
            api._request_with_retry(
                "GET", mock_session, "https://bad.host", max_retries=0
            )
        mock_diag.assert_called_once()

    def test_unsupported_method(self):
        api = BangumiApi()
        mock_session = self._mock_session()
        with pytest.raises(ValueError, match="不支持的HTTP方法"):
            api._request_with_retry("DELETE", mock_session, "https://example.com")

    def test_post_method(self):
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.post.return_value = mock_resp
        result = api._request_with_retry(
            "POST", mock_session, "https://example.com", json={}
        )
        assert result == mock_resp

    def test_put_method(self):
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.put.return_value = mock_resp
        result = api._request_with_retry(
            "PUT", mock_session, "https://example.com", json={}
        )
        assert result == mock_resp

    def test_patch_method(self):
        api = BangumiApi()
        mock_session = self._mock_session()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.patch.return_value = mock_resp
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
            patch("app.utils.bangumi_api.requests.Session"),
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
    """测试 search / search_old"""

    def test_search_cache_hit(self):
        api = BangumiApi()
        api._cache["search"][("title", "2024-01-01", "2024-12-31", 5, True)] = [
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

    def test_search_old_cache_hit(self):
        api = BangumiApi()
        api._cache["search_old"][("title", True)] = [{"id": 1}]
        result = api.search_old("title")
        assert result == [{"id": 1}]

    def test_search_old_non_dict(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.json.return_value = "not a dict"
        with patch.object(api, "_request_with_retry", return_value=mock_resp):
            result = api.search_old("title")
            assert result == []

    def test_search_old_json_error(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("bad")
        with patch.object(api, "_request_with_retry", return_value=mock_resp):
            result = api.search_old("title")
            assert result == []

    def test_search_old_list_only_false(self):
        api = BangumiApi()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": 1, "list": [{"id": 1}]}
        with patch.object(api, "_request_with_retry", return_value=mock_resp):
            result = api.search_old("title", list_only=False)
            assert "results" in result


class TestGetSubject:
    """测试 get_subject"""

    def test_cache_hit(self):
        api = BangumiApi()
        api._cache["get_subject"]["123"] = {"id": 123}
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
        api._cache["get_episodes"][("123", 0)] = {"data": []}
        assert api.get_episodes("123") == {"data": []}

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

    def test_season_gt_5_returns_none(self):
        api = BangumiApi()
        result = api.get_target_season_episode_id("123", 6, 1)
        assert result == (None, None)

    def test_ep_gt_99_returns_none(self):
        api = BangumiApi()
        result = api.get_target_season_episode_id("123", 1, 100)
        assert result == (None, None)

    def test_is_season_subject_id_no_target_ep(self):
        api = BangumiApi()
        result = api.get_target_season_episode_id(
            "123", 1, 0, is_season_subject_id=True
        )
        assert result == "123"

    def test_is_season_subject_id_match_sort(self):
        api = BangumiApi()
        with patch.object(
            api,
            "get_episodes",
            return_value={"data": [{"sort": 3, "id": "ep3"}, {"sort": 1, "id": "ep1"}]},
        ):
            result = api.get_target_season_episode_id(
                "123", 1, 3, is_season_subject_id=True
            )
            assert result == ("123", "ep3")

    def test_is_season_subject_id_match_ep(self):
        api = BangumiApi()
        with patch.object(
            api,
            "get_episodes",
            return_value={"data": [{"ep": 3, "sort": 3, "id": "ep3"}]},
        ):
            result = api.get_target_season_episode_id(
                "123", 1, 3, is_season_subject_id=True
            )
            assert result == ("123", "ep3")

    def test_is_season_subject_id_no_match_fallback(self):
        """指定季度ID未匹配到集数，回退到传统方法"""
        api = BangumiApi()
        with patch.object(
            api,
            "get_episodes",
            return_value={"data": []},
        ):
            result = api.get_target_season_episode_id(
                "123", 1, 5, is_season_subject_id=True
            )
            # 回退后 season==1 且 no ep data → breaks loop → returns (None, None)
            assert result == (None, None)

    def test_season1_no_ep(self):
        api = BangumiApi()
        result = api.get_target_season_episode_id("123", 1, 0)
        assert result == "123"


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

    def test_fallback_to_search_old(self):
        api = BangumiApi()
        with (
            patch.object(api, "search", return_value=[]),
            patch.object(api, "search_old", return_value=[{"id": 10}]),
            patch.object(api, "get_subject", return_value={"id": 10, "name": "番"}),
            patch.object(api, "title_diff_ratio", return_value=0.9),
        ):
            result = api.bgm_search("番", "", "2024-01-15")
            assert result is not None

    def test_search_old_low_ratio_skips(self):
        api = BangumiApi()
        with (
            patch.object(api, "search", return_value=[]),
            patch.object(api, "search_old", return_value=[{"id": 10}]),
            patch.object(api, "get_subject", return_value={"id": 10, "name": "x"}),
            patch.object(api, "title_diff_ratio", return_value=0.1),
        ):
            result = api.bgm_search("完全不同", "", "2024-01-15")
            assert result is None

    def test_no_date_search(self):
        api = BangumiApi()
        with (
            patch.object(api, "search", return_value=[]),
            patch.object(api, "search_old", return_value=[]),
        ):
            result = api.bgm_search("title", "", "")
            assert result is None

    def test_invalid_date_fallback(self):
        """无效日期降级到无日期搜索"""
        api = BangumiApi()
        with (
            patch.object(api, "search", return_value=[]),
            patch.object(api, "search_old", return_value=[]),
        ):
            result = api.bgm_search("title", "", "bad-date-format")
            assert result is None

    def test_low_similarity_triggers_old_search(self):
        """精确搜索相似度低于0.5时触发旧版搜索"""
        api = BangumiApi()
        with (
            patch.object(api, "search", return_value=[{"id": 1}]),
            patch.object(api, "title_diff_ratio", return_value=0.2),
            patch.object(api, "search_old", return_value=[]),
        ):
            result = api.bgm_search("title", "", "2024-01-15")
            assert result is None


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
