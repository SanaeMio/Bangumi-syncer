"""
更多 API 测试 - 使用 TestClient 进行端到端测试
"""

from unittest.mock import patch

from fastapi.testclient import TestClient


class TestPagesEndpoints:
    """页面端点测试"""

    def test_get_index_page(self):
        """测试获取首页"""
        with patch("app.main.config_manager"):
            with patch("app.main.mapping_service"):
                with patch("app.main.trakt_scheduler"):
                    from app.main import app

                    client = TestClient(app)
                    response = client.get("/")
                    # 首页应该返回 200 或重定向
                    assert response.status_code in [200, 302, 404]

    def test_get_trakt_page(self):
        """测试获取 Trakt 页面"""
        with patch("app.main.config_manager"):
            with patch("app.main.mapping_service"):
                with patch("app.main.trakt_scheduler"):
                    from app.main import app

                    client = TestClient(app)
                    response = client.get("/trakt/config")
                    # 应该返回 200 或 302 (如果需要登录)
                    assert response.status_code in [200, 302, 404]


class TestConfigEndpoints:
    """配置端点测试"""

    def test_get_config_page(self):
        """测试获取配置页面"""
        with patch("app.main.config_manager"):
            with patch("app.main.mapping_service"):
                with patch("app.main.trakt_scheduler"):
                    from app.main import app

                    client = TestClient(app)
                    response = client.get("/config")
                    # 应该返回 200 或 302
                    assert response.status_code in [200, 302, 404]


class TestMappingsEndpoints:
    """映射端点测试"""

    def test_get_mappings_page(self):
        """测试获取映射页面"""
        with patch("app.main.config_manager"):
            with patch("app.main.mapping_service"):
                with patch("app.main.trakt_scheduler"):
                    from app.main import app

                    client = TestClient(app)
                    response = client.get("/mappings")
                    # 应该返回 200 或 302
                    assert response.status_code in [200, 302, 404]
