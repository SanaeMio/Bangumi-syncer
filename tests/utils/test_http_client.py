"""http_client 工厂函数的单元测试

覆盖 create_sync_client 与 create_async_client 的参数构造逻辑，
通过 patch httpx.Client / httpx.AsyncClient 来检查传入的 kwargs。
"""

from unittest.mock import MagicMock, patch

import httpx

from app.utils.http_client import create_async_client, create_sync_client

# ------------------------------------------------------------------
# create_sync_client
# ------------------------------------------------------------------


def test_create_sync_client_defaults_kwargs():
    """默认参数：verify=True、timeout=30.0、follow_redirects=True"""
    with patch("app.utils.http_client.httpx.Client") as mock_client_cls:
        create_sync_client()
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["verify"] is True
        assert kwargs["timeout"] == 30.0
        assert kwargs["follow_redirects"] is True


def test_create_sync_client_without_proxy_omits_proxy_key():
    """无 proxy 时不传入 proxy 关键字"""
    with patch("app.utils.http_client.httpx.Client") as mock_client_cls:
        create_sync_client()
        kwargs = mock_client_cls.call_args.kwargs
        assert "proxy" not in kwargs


def test_create_sync_client_with_proxy():
    """传入 proxy 时 kwargs 中包含 proxy"""
    with patch("app.utils.http_client.httpx.Client") as mock_client_cls:
        create_sync_client(proxy="http://127.0.0.1:7890")
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["proxy"] == "http://127.0.0.1:7890"


def test_create_sync_client_custom_verify_false():
    """自定义 verify=False"""
    with patch("app.utils.http_client.httpx.Client") as mock_client_cls:
        create_sync_client(verify=False)
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["verify"] is False


def test_create_sync_client_custom_timeout():
    """自定义 timeout=60.0"""
    with patch("app.utils.http_client.httpx.Client") as mock_client_cls:
        create_sync_client(timeout=60.0)
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["timeout"] == 60.0


def test_create_sync_client_follow_redirects_false():
    """自定义 follow_redirects=False"""
    with patch("app.utils.http_client.httpx.Client") as mock_client_cls:
        create_sync_client(follow_redirects=False)
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["follow_redirects"] is False


def test_create_sync_client_with_headers():
    """传入 headers dict"""
    with patch("app.utils.http_client.httpx.Client") as mock_client_cls:
        headers = {"Authorization": "Bearer token"}
        create_sync_client(headers=headers)
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["headers"] == headers


def test_create_sync_client_without_headers_omits_headers_key():
    """无 headers 时不传入 headers 关键字"""
    with patch("app.utils.http_client.httpx.Client") as mock_client_cls:
        create_sync_client()
        kwargs = mock_client_cls.call_args.kwargs
        assert "headers" not in kwargs


def test_create_sync_client_returns_httpx_client_instance():
    """返回值应为 httpx.Client 实例"""
    client_cls = httpx.Client
    mock_instance = MagicMock(spec=client_cls)
    with patch("app.utils.http_client.httpx.Client", return_value=mock_instance):
        client = create_sync_client()
        assert isinstance(client, client_cls)


# ------------------------------------------------------------------
# create_async_client
# ------------------------------------------------------------------


def test_create_async_client_defaults_kwargs():
    """默认参数：verify=True、timeout=30.0、follow_redirects=True"""
    with patch("app.utils.http_client.httpx.AsyncClient") as mock_client_cls:
        create_async_client()
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["verify"] is True
        assert kwargs["timeout"] == 30.0
        assert kwargs["follow_redirects"] is True


def test_create_async_client_without_proxy_omits_proxy_key():
    """无 proxy 时不传入 proxy 关键字"""
    with patch("app.utils.http_client.httpx.AsyncClient") as mock_client_cls:
        create_async_client()
        kwargs = mock_client_cls.call_args.kwargs
        assert "proxy" not in kwargs


def test_create_async_client_with_proxy():
    """传入 proxy 时 kwargs 中包含 proxy"""
    with patch("app.utils.http_client.httpx.AsyncClient") as mock_client_cls:
        create_async_client(proxy="http://127.0.0.1:7890")
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["proxy"] == "http://127.0.0.1:7890"


def test_create_async_client_custom_verify_false():
    """自定义 verify=False"""
    with patch("app.utils.http_client.httpx.AsyncClient") as mock_client_cls:
        create_async_client(verify=False)
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["verify"] is False


def test_create_async_client_custom_timeout():
    """自定义 timeout=60.0"""
    with patch("app.utils.http_client.httpx.AsyncClient") as mock_client_cls:
        create_async_client(timeout=60.0)
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["timeout"] == 60.0


def test_create_async_client_follow_redirects_false():
    """自定义 follow_redirects=False"""
    with patch("app.utils.http_client.httpx.AsyncClient") as mock_client_cls:
        create_async_client(follow_redirects=False)
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["follow_redirects"] is False


def test_create_async_client_with_headers():
    """传入 headers dict"""
    with patch("app.utils.http_client.httpx.AsyncClient") as mock_client_cls:
        headers = {"Authorization": "Bearer token"}
        create_async_client(headers=headers)
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["headers"] == headers


def test_create_async_client_without_headers_omits_headers_key():
    """无 headers 时不传入 headers 关键字"""
    with patch("app.utils.http_client.httpx.AsyncClient") as mock_client_cls:
        create_async_client()
        kwargs = mock_client_cls.call_args.kwargs
        assert "headers" not in kwargs


def test_create_async_client_returns_httpx_async_client_instance():
    """返回值应为 httpx.AsyncClient 实例"""
    client_cls = httpx.AsyncClient
    mock_instance = MagicMock(spec=client_cls)
    with patch("app.utils.http_client.httpx.AsyncClient", return_value=mock_instance):
        client = create_async_client()
        assert isinstance(client, client_cls)
