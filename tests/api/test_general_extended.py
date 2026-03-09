"""
更多 API 通用测试
"""


class TestProxyAPI:
    """代理 API 测试"""

    def test_proxy_router_exists(self):
        """测试代理路由器存在"""
        from app.api import proxy

        assert proxy.router is not None


class TestNotificationAPI:
    """通知 API 测试"""

    def test_notification_router_exists(self):
        """测试通知路由器存在"""
        from app.api import notification

        assert notification.router is not None


class TestMappingsAPI:
    """映射 API 测试"""

    def test_mappings_router_exists(self):
        """测试映射路由器存在"""
        from app.api import mappings

        assert mappings.router is not None


class TestTraktAPI:
    """Trakt API 测试"""

    def test_trakt_router_exists(self):
        """测试 Trakt 路由器存在"""
        from app.api import trakt

        assert trakt.router is not None


class TestSyncAPI:
    """同步 API 测试"""

    def test_sync_router_exists(self):
        """测试同步路由器存在"""
        from app.api import sync

        assert sync.router is not None

    def test_root_router_exists(self):
        """测试根路由器存在"""
        from app.api import sync

        assert sync.root_router is not None
