"""
main.py MCP 集成测试

覆盖 BDD 场景：
1. 路由注册：app 不包含 /api/mcp/* 可达端点（已删除死路由）
2. MCP OAuth 路由存在：/.well-known、/authorize、/token、/mcp 在根路径可达
"""

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# 1. 路由注册：/api/mcp 已删除
# ---------------------------------------------------------------------------


class TestApiMcpRoutesRemoved:
    """验证 app.main.app 不再注册可达的 /api/mcp 端点（死路由已删除）。"""

    def test_main_app_does_not_contain_api_mcp_routes(self, embed_mocks):
        """请求 /api/mcp 不应命中正经端点（返回 404 或 405）。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/api/mcp")
            assert response.status_code in (404, 405), (
                f"/api/mcp 不应为可达端点，实际: {response.status_code}"
            )


# ---------------------------------------------------------------------------
# 2. MCP OAuth 路由存在
# ---------------------------------------------------------------------------


class TestMcpOAuthRoutesExist:
    """验证 MCP OAuth 相关路由在根路径可达（/.well-known、/authorize、/token、/mcp）。"""

    def test_main_app_contains_mcp_endpoint(self, embed_mocks):
        """main app 应注册 /mcp 端点（访问不 404）。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/mcp")
            assert response.status_code != 404, "/mcp 不应返回 404"

    def test_main_app_contains_well_known_routes(self, embed_mocks):
        """main app 应注册 /.well-known/oauth-authorization-server 端点（返回 200）。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/.well-known/oauth-authorization-server")
            assert response.status_code == 200, (
                f"/.well-known/oauth-authorization-server 应返回 200，"
                f"实际: {response.status_code}"
            )

    def test_main_app_contains_authorize_route(self, embed_mocks):
        """main app 应注册 /authorize 端点（访问不 404）。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/authorize")
            assert response.status_code != 404, "/authorize 不应返回 404"

    def test_main_app_contains_token_route(self, embed_mocks):
        """main app 应注册 /token 端点（GET 访问不 404）。"""
        with embed_mocks():
            from app.main import app

            with TestClient(app) as client:
                response = client.get("/token")
            assert response.status_code != 404, "/token 不应返回 404"
