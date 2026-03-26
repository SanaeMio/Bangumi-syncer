"""
更多 API 测试
"""

from app.api import health, logs, pages


class TestPagesMore:
    """更多页面测试"""

    def test_router_exists(self):
        """测试路由器存在"""
        assert pages.router is not None

    def test_router_prefix(self):
        """测试路由器前缀"""
        assert pages.router.prefix == ""


class TestLogsMore:
    """更多日志测试"""

    def test_logs_router_exists(self):
        """测试日志路由器存在"""
        assert logs.router is not None


class TestHealthMore:
    """更多健康检查测试"""

    def test_health_router_exists(self):
        """测试健康检查路由器存在"""
        assert health.router is not None


class TestMappings:
    """映射 API 测试"""

    def test_mappings_router(self):
        """测试映射路由"""
        from app.api import mappings

        assert mappings.router is not None
