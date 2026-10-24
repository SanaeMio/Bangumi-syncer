"""
页面路由测试
"""

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import pages


class TestPages:
    """测试页面路由"""

    def test_index_page(self):
        """测试首页"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/")

        # 首页应该返回 HTML
        assert response.status_code == 200

    def test_dashboard_page(self):
        """测试仪表板页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/dashboard")

        assert response.status_code == 200

    def test_login_page(self):
        """测试登录页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/login")

        assert response.status_code == 200

    def test_config_page(self):
        """测试配置页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/config")

        assert response.status_code == 200

    def test_records_page(self):
        """测试记录页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/records")

        assert response.status_code == 200

    def test_mappings_page(self):
        """测试映射页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/mappings")

        assert response.status_code == 200

    def test_debug_page(self):
        """测试调试页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/debug")

        assert response.status_code == 200

    def test_logs_page(self):
        """测试日志页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/logs")

        assert response.status_code == 200

    def test_trakt_config_page(self):
        """测试 Trakt 配置页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/trakt/config")

        assert response.status_code == 200

    def test_trakt_auth_page(self):
        """测试 Trakt 授权页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/trakt/auth")

        assert response.status_code == 200

    def test_trakt_auth_success_page(self):
        """测试 Trakt 授权成功页面"""
        app = FastAPI()
        app.include_router(pages.router)

        client = TestClient(app)
        response = client.get("/trakt/auth/success")

        assert response.status_code == 200


class TestPagesAuthenticated:
    """测试已认证用户的页面渲染"""

    @staticmethod
    def _make_app():
        app = FastAPI()
        app.include_router(pages.router)
        return app

    def test_dashboard_authenticated(self):
        """已认证用户访问仪表板"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 200

    def test_config_authenticated(self):
        """已认证用户访问配置页"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/config", follow_redirects=False)
        assert r.status_code == 200

    def test_records_authenticated(self):
        """已认证用户访问记录页"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/records", follow_redirects=False)
        assert r.status_code == 200

    def test_match_records_redirects_to_records(self):
        """匹配记录页重定向至同步记录"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/match-records", follow_redirects=False)
        assert r.status_code == 302
        assert "/records" in (r.headers.get("location") or "")

    def test_match_records_redirects_with_id_param(self):
        """旧链接 ?id= 重定向至同步记录并打开匹配 Tab"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/match-records?id=42", follow_redirects=False)
        assert r.status_code == 302
        location = r.headers.get("location") or ""
        assert "open=42" in location
        assert "tab=match" in location

    def test_mappings_authenticated(self):
        """已认证用户访问映射页"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/mappings", follow_redirects=False)
        assert r.status_code == 200

    def test_debug_authenticated(self):
        """已认证用户访问调试页"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/debug", follow_redirects=False)
        assert r.status_code == 200

    def test_logs_authenticated(self):
        """已认证用户访问日志页"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/logs", follow_redirects=False)
        assert r.status_code == 200

    def test_trakt_config_authenticated(self):
        """已认证用户访问Trakt配置页"""
        app = self._make_app()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            client = TestClient(app)
            r = client.get("/trakt/config", follow_redirects=False)
        assert r.status_code == 200


class TestPagesGuestAndRedirects:
    """未登录重定向与登录页渲染（patch cookie 用户）"""

    @pytest.mark.parametrize(
        "path",
        [
            "/dashboard",
            "/config",
            "/records",
            "/match-records",
            "/mappings",
            "/debug",
            "/logs",
            "/trakt/config",
        ],
    )
    def test_page_redirects_to_login_when_no_user(self, path):
        app = FastAPI()
        app.include_router(pages.router)
        with patch.object(pages, "get_current_user_from_cookie", return_value=None):
            client = TestClient(app)
            r = client.get(path, follow_redirects=False)
        assert r.status_code == 302
        assert "login" in (r.headers.get("location") or "").lower()

    def test_login_page_renders_when_no_user(self):
        app = FastAPI()
        app.include_router(pages.router)
        with patch.object(pages, "get_current_user_from_cookie", return_value=None):
            client = TestClient(app)
            r = client.get("/login")
        assert r.status_code == 200

    def test_login_page_redirects_when_already_logged_in(self):
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

    def test_trakt_auth_error_page_passes_query_params(self):
        app = FastAPI()
        app.include_router(pages.router)
        client = TestClient(app)
        r = client.get("/trakt/auth?status=err&message=bad+callback")
        assert r.status_code == 200
        assert len(r.content) > 0


class TestSharedModals:
    """同步详情 / 重试日志弹窗与 Toast 的层级约束。"""

    @staticmethod
    def _client() -> TestClient:
        app = FastAPI()
        app.include_router(pages.router)
        return TestClient(app)

    def test_retry_log_modal_available_on_dashboard_and_records(self):
        """重试日志弹窗由 base.html 提供：仪表板与记录页均可用、仅一份。

        详情弹窗同属 base.html，在 DOM 顺序上排在日志弹窗前面，使同时打开时日志弹窗位于上层。
        """
        client = self._client()
        with patch.object(
            pages, "get_current_user_from_cookie", return_value={"username": "u"}
        ):
            for path in ("/dashboard", "/records"):
                html = client.get(path).text
                assert html.count('id="retryLogModal"') == 1, path
                assert html.index('id="recordDetailModal"') < html.index(
                    'id="retryLogModal"'
                ), path

    def test_toast_container_above_modal(self):
        """Toast 容器层级须高于 Bootstrap modal（1055）。"""
        css = (Path(__file__).resolve().parents[2] / "static/css/pages.css").read_text(
            encoding="utf-8"
        )
        block = css.split(".toast-container {", 1)[1].split("}", 1)[0]
        match = re.search(r"z-index:\s*(\d+)", block)
        assert match, "toast-container 未声明 z-index"
        assert int(match.group(1)) > 1055

    def test_retry_sync_closes_detail_modal_first(self):
        """重试入口先关闭同步详情弹窗，避免日志弹窗被其遮挡。"""
        js = (
            Path(__file__).resolve().parents[2] / "static/js/sync-retry.js"
        ).read_text(encoding="utf-8")
        body = js.split("async function retrySync", 1)[1].split(
            "function startRetrySSE", 1
        )[0]
        assert "recordDetailModal" in body
        assert "hidden.bs.modal" in body
