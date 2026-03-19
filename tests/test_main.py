"""
FastAPI 主应用测试
"""

from unittest.mock import patch


class TestMainApp:
    """主应用测试"""

    def test_app_creation(self):
        """测试应用创建"""
        # 避免在导入时触发启动事件
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.trakt_scheduler"):
                        from app.main import app

                        assert app is not None

    def test_app_title(self):
        """测试应用标题"""
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.trakt_scheduler"):
                        from app.main import app

                        assert app.title is not None

    def test_app_version(self):
        """测试应用版本"""
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.trakt_scheduler"):
                        from app.main import app

                        assert app.version is not None

    def test_app_has_routes(self):
        """测试应用有路由"""
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.trakt_scheduler"):
                        from app.main import app

                        # 检查应用有路由
                        assert len(app.routes) > 0, "No routes registered"

    def test_app_description(self):
        """测试应用描述"""
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.trakt_scheduler"):
                        from app.main import app

                        assert app.description is not None
