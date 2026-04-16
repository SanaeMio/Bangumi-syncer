"""BangumiApi 内部路径：直连、诊断、重试、JSON 容错、静态工具等（不依赖外网）。"""

import socket
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.utils.bangumi_api import BangumiApi


def _session_resp(status=200, json_body=None, json_exc=None):
    r = MagicMock()
    r.status_code = status
    if json_exc:
        r.json.side_effect = json_exc
    else:
        r.json.return_value = json_body if json_body is not None else {}
    return r


class TestTryDirectConnection:
    def test_get_success_adds_timeout_and_no_proxy(self):
        mock_sess = MagicMock()
        out = _session_resp(200, {})
        mock_sess.get.return_value = out

        with patch("app.utils.bangumi_api.requests.Session", return_value=mock_sess):
            api = BangumiApi(access_token="tok")
            res = api._try_direct_connection("GET", "https://example.test/api")

        assert res is out
        call_kw = mock_sess.get.call_args.kwargs
        assert call_kw["timeout"] == 15
        assert call_kw.get("proxies") is None
        mock_sess.close.assert_called_once()

    def test_post_put_patch_and_high_status_returns_none(self):
        for method, attr in (("POST", "post"), ("PUT", "put"), ("PATCH", "patch")):
            mock_sess = MagicMock()
            bad = _session_resp(502)
            getattr(mock_sess, attr).return_value = bad
            with patch(
                "app.utils.bangumi_api.requests.Session", return_value=mock_sess
            ):
                api = BangumiApi()
                res = api._try_direct_connection(
                    method, "https://example.test/x", verify=True
                )
            assert res is None

    def test_unsupported_method_raises(self):
        mock_sess = MagicMock()
        with patch("app.utils.bangumi_api.requests.Session", return_value=mock_sess):
            api = BangumiApi()
            with pytest.raises(ValueError, match="不支持的HTTP方法"):
                api._try_direct_connection("DELETE", "https://example.test/x")

    def test_exception_reraises_after_close(self):
        mock_sess = MagicMock()
        mock_sess.get.side_effect = requests.exceptions.Timeout("t")
        with patch("app.utils.bangumi_api.requests.Session", return_value=mock_sess):
            api = BangumiApi()
            with pytest.raises(requests.exceptions.Timeout):
                api._try_direct_connection("GET", "https://example.test/x")
        mock_sess.close.assert_called_once()

    def test_strips_proxies_from_kwargs(self):
        mock_sess = MagicMock()
        mock_sess.get.return_value = _session_resp(200)
        with patch("app.utils.bangumi_api.requests.Session", return_value=mock_sess):
            api = BangumiApi()
            api._try_direct_connection(
                "GET",
                "https://example.test/x",
                proxies={"http": "bad"},
                timeout=99,
            )
        assert "proxies" not in mock_sess.get.call_args.kwargs
        assert mock_sess.get.call_args.kwargs["timeout"] == 99


class TestDiagnoseNetworkIssue:
    def test_dns_gaierror_returns_early(self):
        api = BangumiApi()
        with patch(
            "app.utils.bangumi_api.socket.getaddrinfo",
            side_effect=socket.gaierror("nx"),
        ):
            api._diagnose_network_issue("https://unreachable.invalid/")

    def test_dns_generic_error_returns_early(self):
        api = BangumiApi()
        with patch(
            "app.utils.bangumi_api.socket.getaddrinfo",
            side_effect=RuntimeError("dns"),
        ):
            api._diagnose_network_issue("https://example.test/")

    def test_tcp_connect_ex_nonzero_logs(self):
        api = BangumiApi()
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 1
        with patch(
            "app.utils.bangumi_api.socket.getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.1.1.1", 443))
            ],
        ):
            with patch("app.utils.bangumi_api.socket.socket", return_value=mock_sock):
                api._diagnose_network_issue("https://example.test/")
        mock_sock.close.assert_called_once()


class TestRequestWithRetry:
    def test_proxy_failed_uses_direct_connection(self):
        api = BangumiApi(http_proxy="http://127.0.0.1:9")
        api._proxy_failed = True
        ok = _session_resp(200, {"ok": True})
        with patch.object(api, "_try_direct_connection", return_value=ok) as td:
            res = api._request_with_retry("GET", api.req, "https://bgm.test/me")
        td.assert_called_once()
        assert res is ok

    def test_proxy_failed_direct_raises(self):
        api = BangumiApi(http_proxy="http://127.0.0.1:9")
        api._proxy_failed = True
        with patch.object(api, "_try_direct_connection", side_effect=OSError("x")):
            with pytest.raises(OSError):
                api._request_with_retry("GET", api.req, "https://bgm.test/me")

    def test_session_post_put_patch_paths(self):
        for method, name in (("POST", "post"), ("PUT", "put"), ("PATCH", "patch")):
            api = BangumiApi()
            m = MagicMock(return_value=_session_resp(200))
            setattr(api.req, name, m)
            api._request_with_retry(
                method, api.req, "https://bgm.test/x", json={"a": 1}
            )
            m.assert_called_once()

    def test_unsupported_method_in_retry_raises(self):
        api = BangumiApi()
        with pytest.raises(ValueError, match="不支持的HTTP方法"):
            api._request_with_retry("DELETE", api.req, "https://bgm.test/x")

    @patch("app.utils.notifier.send_notify")
    @patch("app.utils.bangumi_api.time.sleep")
    def test_http_500_exhausts_retries_and_notifies(
        self, _sleep, _notify, max_retries=2
    ):
        api = BangumiApi()
        bad = _session_resp(500)
        api.req.get = MagicMock(return_value=bad)
        with pytest.raises(requests.exceptions.HTTPError):
            api._request_with_retry(
                "GET", api.req, "https://bgm.test/r", max_retries=max_retries
            )
        assert api.req.get.call_count == max_retries + 1

    @patch("app.utils.bangumi_api.time.sleep")
    def test_connection_error_then_success(self, _sleep):
        api = BangumiApi()
        ok = _session_resp(200)
        api.req.get = MagicMock(
            side_effect=[
                requests.exceptions.ConnectionError(
                    "Temporary failure in name resolution"
                ),
                ok,
            ]
        )
        res = api._request_with_retry(
            "GET", api.req, "https://bgm.test/r", max_retries=3
        )
        assert res is ok

    @patch("app.utils.bangumi_api.time.sleep")
    @patch.object(BangumiApi, "_diagnose_network_issue")
    @patch.object(BangumiApi, "_try_direct_connection", return_value=None)
    def test_dns_style_error_falls_through_to_diagnose_when_no_proxy(
        self, _direct, diag, _sleep
    ):
        api = BangumiApi(http_proxy=None)
        api.req.get = MagicMock(
            side_effect=requests.exceptions.ConnectionError("Failed to resolve 'x'")
        )
        with pytest.raises(requests.exceptions.ConnectionError):
            api._request_with_retry("GET", api.req, "https://bgm.test/r", max_retries=0)
        diag.assert_called_once()

    @patch("app.utils.bangumi_api.time.sleep")
    def test_proxy_retry_then_direct_success_sets_flag(self, _sleep):
        api = BangumiApi(http_proxy="http://127.0.0.1:9")
        ok = _session_resp(200)
        api.req.get = MagicMock(
            side_effect=requests.exceptions.ConnectionError("proxy dead")
        )
        with patch.object(api, "_try_direct_connection", return_value=ok):
            res = api._request_with_retry(
                "GET", api.req, "https://bgm.test/r", max_retries=0
            )
        assert res is ok
        assert api._proxy_failed is True


class TestCheckAuthAndGetMe:
    @patch("app.utils.notifier.send_notify")
    def test_check_auth_error_401_raises(self, _notify):
        api = BangumiApi(username="u")
        res = MagicMock(status_code=401)
        with pytest.raises(ValueError, match="access_token"):
            api._check_auth_error(res)

    @patch("app.utils.notifier.send_notify")
    @patch.object(BangumiApi, "get")
    def test_get_me_client_error_not_nt(self, mock_get, _notify):
        r = MagicMock()
        r.status_code = 403
        r.json.side_effect = AssertionError("should not json")
        mock_get.return_value = r
        with patch("os.name", "posix"):
            api = BangumiApi(username="u")
            with pytest.raises(ValueError, match="未授权"):
                api.get_me()


class TestSearchAndSubjectJsonBranches:
    def test_search_cache_hit_skips_request(self):
        api = BangumiApi()
        key = ("kw", "2020-01-01", "2020-02-01", 5, True)
        api._cache["search"][key] = [{"id": 9}]
        with patch.object(api, "_request_with_retry") as m:
            out = api.search("kw", "2020-01-01", "2020-02-01", limit=5, list_only=True)
        m.assert_not_called()
        assert out == [{"id": 9}]

    def test_search_non_dict_json_normalizes(self):
        api = BangumiApi()
        raw = MagicMock()
        raw.status_code = 200
        raw.json.return_value = ["not", "dict"]
        with patch.object(api, "_request_with_retry", return_value=raw):
            out = api.search("x", "2020-01-01", "2020-01-02", limit=3, list_only=True)
        assert out == []

    def test_search_json_error_normalizes(self):
        api = BangumiApi()
        raw = MagicMock(status_code=200)
        raw.json.side_effect = ValueError("bad json")
        with patch.object(api, "_request_with_retry", return_value=raw):
            out = api.search("x", "2020-01-01", "2020-01-02", limit=3, list_only=True)
        assert out == []

    def test_search_list_only_false_returns_full_dict(self):
        api = BangumiApi()
        raw = MagicMock(status_code=200)
        raw.json.return_value = {"data": [1], "extra": True}
        with patch.object(api, "_request_with_retry", return_value=raw):
            out = api.search("x", "2020-01-01", "2020-01-02", list_only=False)
        assert out["extra"] is True

    def test_search_old_non_dict_and_json_error(self):
        for idx, payload in enumerate((["x"], ValueError("e"))):
            api = BangumiApi()
            raw = MagicMock(status_code=200)
            if isinstance(payload, Exception):
                raw.json.side_effect = payload
            else:
                raw.json.return_value = payload
            with patch.object(api, "_request_with_retry", return_value=raw):
                out = api.search_old(f"title-{idx}", list_only=True)
            assert out == []

    def test_get_subject_non_dict_and_json_error(self):
        api = BangumiApi()
        raw = MagicMock(status_code=200)
        raw.json.return_value = []
        with patch.object(api, "get", return_value=raw):
            assert api.get_subject(1) == {}
        raw2 = MagicMock(status_code=200)
        raw2.json.side_effect = OSError("read")
        with patch.object(api, "get", return_value=raw2):
            assert api.get_subject(2) == {}

    def test_get_related_subjects_dict_list_and_bad_type(self):
        cases = (
            (1, {"data": [{"id": 1}]}, {"data": [{"id": 1}]}),
            (2, [{"id": 2}], [{"id": 2}]),
            (3, "bad", []),
        )
        for sid, body, expected in cases:
            api = BangumiApi()
            raw = MagicMock(status_code=200)
            raw.json.return_value = body
            with patch.object(api, "get", return_value=raw):
                assert api.get_related_subjects(sid) == expected

    def test_get_related_json_error(self):
        api = BangumiApi()
        raw = MagicMock(status_code=200)
        raw.json.side_effect = ValueError("x")
        with patch.object(api, "get", return_value=raw):
            assert api.get_related_subjects(3) == []

    def test_get_episodes_non_dict_and_json_error(self):
        api = BangumiApi()
        raw = MagicMock(status_code=200)
        raw.json.return_value = []
        with patch.object(api, "get", return_value=raw):
            assert api.get_episodes(1) == {"data": [], "total": 0}
        raw2 = MagicMock(status_code=200)
        raw2.json.side_effect = RuntimeError("x")
        with patch.object(api, "get", return_value=raw2):
            assert api.get_episodes(2) == {"data": [], "total": 0}

    def test_get_subject_collection_404_and_bad_json(self):
        api = BangumiApi()
        r404 = MagicMock(status_code=404)
        with patch.object(api, "get", return_value=r404):
            assert api.get_subject_collection(1) == {}
        raw = MagicMock(status_code=200)
        raw.json.return_value = []
        with patch.object(api, "get", return_value=raw):
            assert api.get_subject_collection(2) == {}

    def test_get_ep_collection_404_and_bad_json(self):
        api = BangumiApi()
        r404 = MagicMock(status_code=404)
        with patch.object(api, "get", return_value=r404):
            assert api.get_ep_collection(9) == {}
        raw = MagicMock(status_code=200)
        raw.json.side_effect = ValueError("x")
        with patch.object(api, "get", return_value=raw):
            assert api.get_ep_collection(8) == {}


class TestParseIsoDateAndTitleDiff:
    def test_parse_iso_date_edges(self):
        assert BangumiApi._parse_iso_date_ymd("") is None
        assert BangumiApi._parse_iso_date_ymd("2020-01") is None
        assert BangumiApi._parse_iso_date_ymd("2020-13-40") is None
        d = BangumiApi._parse_iso_date_ymd("2024-06-15T12:00:00")
        assert d.year == 2024 and d.month == 6 and d.day == 15

    def test_title_diff_ratio_infobox_variants(self):
        base = {"name": "A", "name_cn": "甲"}
        assert BangumiApi.title_diff_ratio("甲", "A", base) > 0
        item = {
            "name": "X",
            "infobox": [
                {
                    "key": "别名",
                    "value": [{"v": "别名一"}, "plain-alias"],
                }
            ],
        }
        r = BangumiApi.title_diff_ratio("别名一", "Alias", item)
        assert r >= 0.0


class TestChangeEpisodeState:
    def test_status_between_333_and_444_raises(self):
        api = BangumiApi()
        res = MagicMock(status_code=400, text="err")
        with patch.object(api, "put", return_value=res):
            with pytest.raises(ValueError, match="status_code"):
                api.change_episode_state(1, state=2)
