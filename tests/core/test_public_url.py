"""public_url 子路径前缀工具测试"""

from unittest.mock import patch

from fastapi.responses import RedirectResponse

from app.core import public_url as pu


def test_normalize_public_base_path_empty():
    assert pu.normalize_public_base_path("") == ""
    assert pu.normalize_public_base_path("   ") == ""


def test_normalize_public_base_path_slash_and_backslash():
    assert pu.normalize_public_base_path("/Bangumi") == "/Bangumi"
    assert pu.normalize_public_base_path("/Bangumi/") == "/Bangumi"
    assert pu.normalize_public_base_path("Bangumi") == "/Bangumi"
    assert pu.normalize_public_base_path("\\Bangumi\\") == "/Bangumi"


def test_join_public_no_prefix(monkeypatch):
    monkeypatch.setattr("app.core.public_url.get_public_base_path", lambda: "")
    assert pu.join_public("/api/foo") == "/api/foo"
    assert pu.join_public("/static/x.css") == "/static/x.css"


def test_join_public_with_prefix(monkeypatch):
    monkeypatch.setattr("app.core.public_url.get_public_base_path", lambda: "/Bangumi")
    assert pu.join_public("/api/foo") == "/Bangumi/api/foo"
    assert pu.join_public("/trakt/auth?a=b") == "/Bangumi/trakt/auth?a=b"


def test_get_public_base_path_env_priority(monkeypatch):
    monkeypatch.setenv("APPLICATION_ROOT", "/Proxy")
    assert pu.get_public_base_path() == "/Proxy"
    monkeypatch.delenv("APPLICATION_ROOT", raising=False)
    monkeypatch.setenv("BASE_PATH", "/FromBase")
    assert pu.get_public_base_path() == "/FromBase"


def test_get_public_base_path_config_error_returns_empty(monkeypatch):
    monkeypatch.delenv("APPLICATION_ROOT", raising=False)
    monkeypatch.delenv("BASE_PATH", raising=False)
    with patch("app.core.config.config_manager.get", side_effect=RuntimeError("ini")):
        assert pu.get_public_base_path() == ""


def test_join_public_empty_path(monkeypatch):
    monkeypatch.setattr("app.core.public_url.get_public_base_path", lambda: "")
    assert pu.join_public("") == "/"
    monkeypatch.setattr("app.core.public_url.get_public_base_path", lambda: "/App")
    assert pu.join_public("") == "/App"


def test_join_public_adds_leading_slash(monkeypatch):
    monkeypatch.setattr("app.core.public_url.get_public_base_path", lambda: "")
    assert pu.join_public("rel\\path") == "/rel/path"


def test_redirect_public(monkeypatch):
    monkeypatch.setattr("app.core.public_url.get_public_base_path", lambda: "/x")
    r = pu.redirect_public("/login", status_code=301)
    assert isinstance(r, RedirectResponse)
    assert r.status_code == 301
    assert r.headers["location"] == "/x/login"
