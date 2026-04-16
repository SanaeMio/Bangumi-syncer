"""pages：未登录重定向与登录页渲染（patch cookie 用户）。"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import pages


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/config",
        "/records",
        "/mappings",
        "/debug",
        "/logs",
        "/trakt/config",
    ],
)
def test_page_redirects_to_login_when_no_user(path):
    app = FastAPI()
    app.include_router(pages.router)
    with patch.object(pages, "get_current_user_from_cookie", return_value=None):
        client = TestClient(app)
        r = client.get(path, follow_redirects=False)
    assert r.status_code == 302
    assert "login" in (r.headers.get("location") or "").lower()


def test_login_page_renders_when_no_user():
    app = FastAPI()
    app.include_router(pages.router)
    with patch.object(pages, "get_current_user_from_cookie", return_value=None):
        client = TestClient(app)
        r = client.get("/login")
    assert r.status_code == 200


def test_login_page_redirects_when_already_logged_in():
    app = FastAPI()
    app.include_router(pages.router)
    with patch.object(
        pages,
        "get_current_user_from_cookie",
        return_value={"username": "u"},
    ):
        client = TestClient(app)
        r = client.get("/login", follow_redirects=False)
    assert r.status_code == 302
    assert "dashboard" in (r.headers.get("location") or "").lower()


def test_trakt_auth_error_page_passes_query_params():
    app = FastAPI()
    app.include_router(pages.router)
    client = TestClient(app)
    r = client.get("/trakt/auth?status=err&message=bad+callback")
    assert r.status_code == 200
    assert len(r.content) > 0
