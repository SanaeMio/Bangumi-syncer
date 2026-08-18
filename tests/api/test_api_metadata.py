"""路由元数据断言（prefix/tags/routes：合并自 test_general/extended/endpoints/logs_extended）。

只做 router 元数据与路由存在性检查；实际请求类端点测试见 test_more_endpoints 等文件。
"""


class TestRouterPrefixes:
    """各 router 的 URL 前缀"""

    def test_pages_router_prefix(self):
        from app.api import pages

        assert pages.router.prefix == ""

    def test_logs_router_prefix(self):
        from app.api import logs

        assert logs.router.prefix == "/api"

    def test_mappings_router_prefix(self):
        from app.api import mappings

        assert mappings.router.prefix == "/api"

    def test_proxy_router_prefix(self):
        from app.api import proxy

        assert proxy.router.prefix == "/api"

    def test_notification_router_prefix(self):
        from app.api import notification

        assert notification.router.prefix == "/api"

    def test_trakt_router_prefix(self):
        from app.api import trakt

        assert trakt.router.prefix == "/api/trakt"

    def test_sync_router_prefix(self):
        from app.api import sync

        assert sync.router.prefix == "/api"


class TestRouterTags:
    """各 router 的 OpenAPI 标签"""

    def test_pages_router_tags(self):
        from app.api import pages

        assert "pages" in pages.router.tags

    def test_logs_router_tags(self):
        from app.api import logs

        assert "logs" in logs.router.tags

    def test_health_router_tags(self):
        from app.api import health

        assert "health" in health.router.tags

    def test_mappings_router_tags(self):
        from app.api import mappings

        assert "mappings" in mappings.router.tags

    def test_trakt_router_tags(self):
        from app.api import trakt

        assert "trakt" in trakt.router.tags

    def test_sync_router_tags(self):
        from app.api import sync

        assert "sync" in sync.router.tags

    def test_root_router_tags(self):
        from app.api import sync

        assert "sync" in sync.root_router.tags


class TestRouterRoutes:
    """各 router 已注册路由"""

    def test_sync_router_has_routes(self):
        from app.api import sync

        assert len(sync.router.routes) > 0

    def test_auth_router_has_routes(self):
        from app.api import auth

        assert len(auth.router.routes) > 0

    def test_notification_router_has_routes(self):
        from app.api import notification

        assert len(notification.router.routes) > 0

    def test_logs_router_routes(self):
        from app.api import logs

        assert len(logs.router.routes) > 0

    def test_health_router_routes(self):
        from app.api import health

        assert len(health.router.routes) > 0

    def test_health_router_exists(self):
        from app.api import health

        assert health.router is not None

    def test_deps_module_import(self):
        from app.api import deps

        assert deps is not None


class TestConfigAPIEndpoints:
    """config router 存在性（含认证依赖覆盖的旧冒烟用例）"""

    def test_get_config_endpoint(self):
        from unittest.mock import patch

        from app.api import config

        with patch("app.api.config.config_manager") as mock_config:
            mock_config.get.return_value = "test_value"
            mock_config.get_config_parser.return_value.has_section.return_value = True
            mock_config.get_config_parser.return_value.sections.return_value = ["test"]
            assert config.router is not None
