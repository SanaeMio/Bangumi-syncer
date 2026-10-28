"""MCP 装配唯一性防回退护栏。

守卫两条约束，防止它们在未来被再次破坏：

1. 生产模块 ``app.mcp.provider`` 不得再导出测试专用的装配工厂
   （历史上曾有 ``create_auth_server``，它是 sidecar 原型搬运进来的
   测试工厂，会与生产装配逻辑产生漂移）。
2. ``/consent`` 路由的注册逻辑集中在生产唯一装配入口
   ``app.mcp.server.create_mcp_app``；无论走默认 provider 还是注入
   provider，返回的 app 都必须注册 ``/consent``。

这些护栏只断言稳定的对外行为（模块导出、路由路径、helper 的调用
依赖），不断言行号或私有属性，避免未来误报。
"""

from __future__ import annotations

import types

from starlette.routing import Route


def _route_paths(app) -> set[str]:
    """提取 Starlette app 中所有已注册路由路径。"""
    return {route.path for route in app.routes if isinstance(route, Route)}


def test_provider模块不再导出测试工厂():
    """app.mcp.provider 不应再暴露测试专用装配工厂 create_auth_server。"""
    import app.mcp.provider as provider_module

    assert not hasattr(provider_module, "create_auth_server"), (
        "app.mcp.provider 不应再导出测试专用工厂 create_auth_server；"
        "测试装配应复用 app.mcp.server.create_mcp_app。"
    )


def test_装配入口默认路径注册consent():
    """create_mcp_app()（默认 provider）返回的 app 必须注册 /consent。"""
    from app.mcp.server import create_mcp_app

    paths = _route_paths(create_mcp_app())

    assert "/consent" in paths, f"默认装配路径应注册 /consent，实际路径: {paths}"


def test_装配入口注入provider路径注册consent(tmp_path):
    """create_mcp_app(provider=...) 返回的 app 同样必须注册 /consent。"""
    from app.mcp.keys import RSAKeyManager
    from app.mcp.provider import BangumiOAuthProvider
    from app.mcp.server import create_mcp_app

    rsa_manager = RSAKeyManager(
        private_key_path=str(tmp_path / "private.pem"),
        public_key_path=str(tmp_path / "public.pem"),
    )
    rsa_manager.load_or_generate()
    provider = BangumiOAuthProvider(
        base_url="https://custom-issuer.test:9000",
        rsa_manager=rsa_manager,
        issuer="https://custom-issuer.test:9000",
        audience="bangumi-syncer",
        auth_enabled=False,
        auth_username="admin",
    )

    paths = _route_paths(create_mcp_app(provider=provider))

    assert "/consent" in paths, f"注入 provider 路径应注册 /consent，实际路径: {paths}"


def test_测试helper复用生产装配入口(tmp_path, monkeypatch):
    """build_test_mcp_app 必须委托 create_mcp_app 装配，不得自造一份。

    通过替换 ``tests.mcp_helpers.create_mcp_app`` 为探针，确认 helper 确实
    经由生产唯一装配入口构造 app；若哪天 helper 又复制一套装配逻辑，
    探针不会被调用，本用例即失败。
    """
    import tests.mcp_helpers as helpers

    called_kwargs: dict[str, object] = {}

    class _FakeApp:
        def __init__(self) -> None:
            self.state = types.SimpleNamespace()

    def _spy_create_mcp_app(**kwargs):
        called_kwargs.update(kwargs)
        return _FakeApp()

    monkeypatch.setattr(helpers, "create_mcp_app", _spy_create_mcp_app)

    app = helpers.build_test_mcp_app(
        private_key_path=str(tmp_path / "private.pem"),
        public_key_path=str(tmp_path / "public.pem"),
        issuer="https://custom-issuer.test:9000",
        audience="bangumi-syncer",
    )

    assert "provider" in called_kwargs, (
        "build_test_mcp_app 应经 create_mcp_app(provider=...) 装配，"
        f"实际调用参数: {called_kwargs}"
    )
    # helper 会把自建的 provider 挂到返回 app 的 state.provider 上；
    # 校验透传给 create_mcp_app 的正是该对象，而非另造的 provider。
    assert called_kwargs["provider"] is app.state.provider, (
        "build_test_mcp_app 应把自建 provider 原样透传给 create_mcp_app，"
        f"实际调用参数与 app.state.provider 不一致: {called_kwargs}"
    )
