"""deps：Bearer / Cookie / flexible 认证分支。"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api import deps


@pytest.fixture
def mock_security():
    with patch.object(deps, "security_manager") as sm:
        sm.cleanup_expired_sessions.return_value = None
        yield sm


def test_get_current_user_auth_disabled(mock_security):
    mock_security.get_auth_config.return_value = {"enabled": False}
    out = deps.get_current_user(credentials=None)
    assert out == {"username": "admin", "auth_disabled": True}
    mock_security.cleanup_expired_sessions.assert_not_called()


def test_get_current_user_missing_credentials_raises(mock_security):
    mock_security.get_auth_config.return_value = {"enabled": True}
    with pytest.raises(HTTPException) as ei:
        deps.get_current_user(credentials=None)
    assert ei.value.status_code == 401
    assert "令牌" in ei.value.detail


def test_get_current_user_invalid_session_raises(mock_security):
    mock_security.get_auth_config.return_value = {"enabled": True}
    cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad")
    mock_security.validate_session.return_value = None
    with pytest.raises(HTTPException) as ei:
        deps.get_current_user(credentials=cred)
    assert ei.value.status_code == 401


def test_get_current_user_valid(mock_security):
    mock_security.get_auth_config.return_value = {"enabled": True}
    cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
    mock_security.validate_session.return_value = {"uid": "1"}
    assert deps.get_current_user(credentials=cred) == {"uid": "1"}
    mock_security.validate_session.assert_called_once_with("tok")


def test_get_current_user_from_cookie_auth_disabled(mock_security):
    mock_security.get_auth_config.return_value = {"enabled": False}
    req = MagicMock()
    assert deps.get_current_user_from_cookie(req) == {
        "username": "admin",
        "auth_disabled": True,
    }


def test_get_current_user_from_cookie_no_token(mock_security):
    mock_security.get_auth_config.return_value = {"enabled": True}
    req = MagicMock()
    req.cookies.get.return_value = None
    assert deps.get_current_user_from_cookie(req) is None


def test_get_current_user_from_cookie_valid(mock_security):
    mock_security.get_auth_config.return_value = {"enabled": True}
    req = MagicMock()
    req.cookies.get.return_value = "sess"
    mock_security.validate_session.return_value = {"s": 1}
    assert deps.get_current_user_from_cookie(req) == {"s": 1}
    mock_security.validate_session.assert_called_once_with("sess")


def test_get_current_user_from_cookie_invalid_returns_none(mock_security):
    mock_security.get_auth_config.return_value = {"enabled": True}
    req = MagicMock()
    req.cookies.get.return_value = "sess"
    mock_security.validate_session.return_value = None
    assert deps.get_current_user_from_cookie(req) is None


@pytest.mark.asyncio
async def test_get_current_user_flexible_auth_disabled(mock_security):
    mock_security.get_auth_config.return_value = {"enabled": False}
    req = MagicMock()
    out = await deps.get_current_user_flexible(req, credentials=None)
    assert out["auth_disabled"] is True


@pytest.mark.asyncio
async def test_get_current_user_flexible_cookie_wins(mock_security):
    mock_security.get_auth_config.return_value = {"enabled": True}
    req = MagicMock()
    req.cookies.get.return_value = "c"
    cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials="b")
    mock_security.validate_session.side_effect = lambda t: (
        {"via": "cookie"} if t == "c" else {"via": "bearer"}
    )
    out = await deps.get_current_user_flexible(req, cred)
    assert out == {"via": "cookie"}


@pytest.mark.asyncio
async def test_get_current_user_flexible_bearer_when_cookie_invalid(
    mock_security,
):
    mock_security.get_auth_config.return_value = {"enabled": True}
    req = MagicMock()
    req.cookies.get.return_value = "badcookie"
    cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials="good")
    mock_security.validate_session.side_effect = lambda t: (
        None if t == "badcookie" else {"via": "bearer"}
    )
    out = await deps.get_current_user_flexible(req, cred)
    assert out == {"via": "bearer"}


@pytest.mark.asyncio
async def test_get_current_user_flexible_no_auth_raises(mock_security):
    mock_security.get_auth_config.return_value = {"enabled": True}
    req = MagicMock()
    req.cookies.get.return_value = None
    with pytest.raises(HTTPException) as ei:
        await deps.get_current_user_flexible(req, credentials=None)
    assert ei.value.status_code == 401
    assert "有效" in ei.value.detail


@pytest.mark.asyncio
async def test_get_current_user_optional_auth_disabled(mock_security):
    """认证禁用时返回admin用户"""
    mock_security.get_auth_config.return_value = {"enabled": False}
    req = MagicMock()
    out = await deps.get_current_user_optional(req, credentials=None)
    assert out == {"username": "admin", "auth_disabled": True}


@pytest.mark.asyncio
async def test_get_current_user_optional_cookie_valid(mock_security):
    """Cookie有效时返回会话"""
    mock_security.get_auth_config.return_value = {"enabled": True}
    req = MagicMock()
    req.cookies.get.return_value = "valid_token"
    mock_security.validate_session.return_value = {"uid": "1"}
    out = await deps.get_current_user_optional(req, credentials=None)
    assert out == {"uid": "1"}


@pytest.mark.asyncio
async def test_get_current_user_optional_bearer_valid(mock_security):
    """Bearer token有效时返回会话"""
    mock_security.get_auth_config.return_value = {"enabled": True}
    req = MagicMock()
    req.cookies.get.return_value = None
    cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid")
    mock_security.validate_session.return_value = {"uid": "2"}
    out = await deps.get_current_user_optional(req, cred)
    assert out == {"uid": "2"}


@pytest.mark.asyncio
async def test_get_current_user_optional_no_auth_returns_none(mock_security):
    """无有效认证时返回None"""
    mock_security.get_auth_config.return_value = {"enabled": True}
    req = MagicMock()
    req.cookies.get.return_value = None
    out = await deps.get_current_user_optional(req, credentials=None)
    assert out is None


@pytest.mark.asyncio
async def test_get_current_user_optional_cookie_invalid_bearer_valid(mock_security):
    """Cookie无效但Bearer有效时返回Bearer会话"""
    mock_security.get_auth_config.return_value = {"enabled": True}
    req = MagicMock()
    req.cookies.get.return_value = "invalid"
    cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid")
    mock_security.validate_session.side_effect = lambda t: (
        None if t == "invalid" else {"via": "bearer"}
    )
    out = await deps.get_current_user_optional(req, cred)
    assert out == {"via": "bearer"}
