"""OAuthService 的 state 消费行为测试，重点关注 TOCTOU 竞态防护。"""

from unittest.mock import patch

import pytest

from app.services.oauth.provider import OAuthProvider, OAuthProviderRegistry
from app.services.oauth.service import OAuthService


@pytest.fixture
def db(temp_dir, reset_singletons):
    with patch("app.core.database.logger"):
        from app.core.database import DatabaseManager

        db_path = temp_dir / "oauth.db"
        yield DatabaseManager(str(db_path))


@pytest.fixture
def service(db, monkeypatch):
    # OAuthService 内部引用模块级 database_manager 单例，
    # 替换为测试用真实 DatabaseManager 实例，验证 rowcount 行为
    from app.services.oauth import service as oauth_service_mod

    monkeypatch.setattr(oauth_service_mod, "database_manager", db)

    registry = OAuthProviderRegistry()
    registry.register(
        OAuthProvider(
            name="bangumi",
            authorize_url="https://bgm.tv/oauth/authorize",
            token_url="https://bgm.tv/oauth/access_token",
            redirect_path="/api/bangumi/auth/callback",
            scopes=[],
            extra_auth_params={},
        )
    )
    return OAuthService(registry)


def test_consume_state_success(service, db):
    state = service.create_state("bangumi", "bangumi-alpha")
    assert service.consume_state("bangumi", state) == "bangumi-alpha"


def test_consume_state_rejects_double_consume(service, db):
    """同一 state 第二次消费必须返回 None（CSRF 防护核心）。

    模拟并发场景：两个请求都通过了 SELECT，但只有第一个 DELETE 命中行，
    第二个 DELETE 返回 0 行 → consume_state 必须返回 None。
    """
    state = service.create_state("bangumi", "bangumi-alpha")
    first = service.consume_state("bangumi", state)
    second = service.consume_state("bangumi", state)
    assert first == "bangumi-alpha"
    assert second is None


def test_consume_state_invalid_returns_none(service, db):
    assert service.consume_state("bangumi", "nonexistent-state") is None


def test_consume_state_provider_mismatch_returns_none(service, db):
    state = service.create_state("bangumi", "bangumi-alpha")
    # 用不同 provider 消费应失败，state 保留供正确 provider 重试
    assert service.consume_state("trakt", state) is None
    # 正确 provider 仍可消费
    assert service.consume_state("bangumi", state) == "bangumi-alpha"
