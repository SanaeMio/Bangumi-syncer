"""
页面路由测试
"""

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
