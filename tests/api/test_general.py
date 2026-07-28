"""
更多 API 测试
"""

from app.api import health, logs, pages


class TestPagesMore:
    """更多页面测试"""

    def test_router_prefix(self):
        """测试路由器前缀"""
        assert pages.router.prefix == ""

    def test_router_tags(self):
        """测试路由器标签"""
        assert "pages" in pages.router.tags


class TestLogsMore:
    """更多日志测试"""

    def test_logs_router_prefix(self):
        """测试日志路由器前缀"""
        assert logs.router.prefix == "/api"

    def test_logs_router_tags(self):
        """测试日志路由器标签"""
        assert "logs" in logs.router.tags


class TestHealthMore:
    """更多健康检查测试"""

    def test_health_router_tags(self):
        """测试健康检查路由器标签"""
        assert "health" in health.router.tags


class TestMappings:
    """映射 API 测试"""

    def test_mappings_router_prefix(self):
        """测试映射路由前缀"""
        from app.api import mappings

        assert mappings.router.prefix == "/api"

    def test_mappings_router_tags(self):
        """测试映射路由标签"""
        from app.api import mappings

        assert "mappings" in mappings.router.tags
