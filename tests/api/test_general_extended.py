"""
更多 API 通用测试
"""


class TestProxyAPI:
    """代理 API 测试"""

    def test_proxy_router_prefix(self):
        """测试代理路由器前缀"""
        from app.api import proxy

        assert proxy.router.prefix == "/api"


class TestNotificationAPI:
    """通知 API 测试"""

    def test_notification_router_prefix(self):
        """测试通知路由器前缀"""
        from app.api import notification

        assert notification.router.prefix == "/api"


class TestMappingsAPI:
    """映射 API 测试"""

    def test_mappings_router_prefix(self):
        """测试映射路由器前缀"""
        from app.api import mappings

        assert mappings.router.prefix == "/api"

    def test_mappings_router_tags(self):
        """测试映射路由器标签"""
        from app.api import mappings

        assert "mappings" in mappings.router.tags


class TestTraktAPI:
    """Trakt API 测试"""

    def test_trakt_router_prefix(self):
        """测试 Trakt 路由器前缀"""
        from app.api import trakt

        assert trakt.router.prefix == "/api/trakt"

    def test_trakt_router_tags(self):
        """测试 Trakt 路由器标签"""
        from app.api import trakt

        assert "trakt" in trakt.router.tags


class TestSyncAPI:
    """同步 API 测试"""

    def test_sync_router_prefix(self):
        """测试同步路由器前缀"""
        from app.api import sync

        assert sync.router.prefix == "/api"

    def test_sync_router_tags(self):
        """测试同步路由器标签"""
        from app.api import sync

        assert "sync" in sync.router.tags

    def test_root_router_tags(self):
        """测试根路由器标签"""
        from app.api import sync

        assert "sync" in sync.root_router.tags
