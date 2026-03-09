"""
日志 API 扩展测试
"""


class TestLogsAPIExtended:
    """日志 API 扩展测试"""

    def test_logs_router_prefix(self):
        """测试日志路由器前缀"""
        from app.api import logs

        assert logs.router.prefix == "/api"

    def test_logs_router_routes(self):
        """测试日志路由有路由"""
        from app.api import logs

        # 检查路由器有路径
        assert len(logs.router.routes) > 0


class TestDepsAPI:
    """依赖注入 API 测试"""

    def test_deps_module_import(self):
        """测试导入 deps 模块"""
        from app.api import deps

        assert deps is not None


class TestHealthAPIExtended:
    """健康检查 API 扩展测试"""

    def test_health_router_exists(self):
        """测试健康检查路由器存在"""
        from app.api import health

        assert health.router is not None

    def test_health_router_routes(self):
        """测试健康检查路由有路由"""
        from app.api import health

        # 检查路由器有路径
        assert len(health.router.routes) > 0
