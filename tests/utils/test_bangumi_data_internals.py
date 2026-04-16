"""bangumi_data 模块级重试与 BangumiData 缓存/下载边界（mock，不依赖外网）。"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.utils import bangumi_data


@patch("app.utils.bangumi_data.time.sleep")
@patch("app.utils.bangumi_data.requests.get")
def test_module_request_with_retry_raises_after_exhausted(mock_get, _sleep):
    mock_get.side_effect = requests.exceptions.ConnectionError("down")
    with pytest.raises(requests.exceptions.ConnectionError):
        bangumi_data._request_with_retry(
            "https://example.test/data.json", max_retries=1, ssl_verify=True
        )
    assert mock_get.call_count == 2


@patch("app.utils.bangumi_data.time.sleep")
@patch("app.utils.bangumi_data.requests.get")
def test_module_request_with_retry_connection_error_then_success(mock_get, _sleep):
    ok = MagicMock()
    ok.status_code = 200
    ok.raise_for_status = MagicMock()
    mock_get.side_effect = [
        requests.exceptions.ConnectionError("down"),
        ok,
    ]
    out = bangumi_data._request_with_retry(
        "https://example.test/retry.json", max_retries=2, ssl_verify=True
    )
    assert out is ok
    assert mock_get.call_count == 2


@patch("app.utils.bangumi_data.time.sleep")
@patch("app.utils.bangumi_data.requests.get")
def test_module_request_with_retry_ssl_verify_false_sets_warnings(mock_get, _sleep):
    ok = MagicMock()
    ok.status_code = 200
    ok.raise_for_status = MagicMock()
    mock_get.return_value = ok
    bangumi_data._request_with_retry(
        "https://example.test/x", max_retries=0, ssl_verify=False
    )
    mock_get.assert_called_once()


@patch.object(bangumi_data.BangumiData, "_build_tmdb_mapping", lambda self: None)
@patch.object(bangumi_data.BangumiData, "_preload_data_to_memory", lambda self: None)
@patch.object(
    bangumi_data.BangumiData,
    "_check_and_download_cache_on_startup",
    lambda self: None,
)
class TestBangumiDataCacheHelpers:
    def test_is_cache_valid_false_when_missing(self):
        data = bangumi_data.BangumiData()
        with patch("os.path.exists", return_value=False):
            assert data._is_cache_valid() is False

    def test_is_cache_valid_false_on_mtime_error(self):
        data = bangumi_data.BangumiData()
        with patch("os.path.exists", return_value=True):
            with patch("os.path.getmtime", side_effect=OSError("no mtime")):
                assert data._is_cache_valid() is False

    def test_download_data_returns_false_on_request_failure(self):
        data = bangumi_data.BangumiData()
        with patch(
            "app.utils.bangumi_data._request_with_retry",
            side_effect=requests.exceptions.RequestException("fail"),
        ):
            assert data._download_data() is False
