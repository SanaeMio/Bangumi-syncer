"""
Auth models tests
"""



class TestAuthModels:
    """Test auth-related Pydantic models"""

    def test_login_request_model(self):
        """Test LoginRequest model"""
        from app.models.auth import LoginRequest

        # Valid request
        req = LoginRequest(username="admin", password="password123")
        assert req.username == "admin"
        assert req.password == "password123"

    def test_login_response_model(self):
        """Test LoginResponse model"""
        from app.models.auth import LoginResponse

        # With data
        resp = LoginResponse(
            status="success", message="Login successful", data={"token": "abc"}
        )
        assert resp.status == "success"
        assert resp.data == {"token": "abc"}

        # Without data
        resp = LoginResponse(status="error", message="Login failed")
        assert resp.status == "error"
        assert resp.data is None

    def test_auth_status_model(self):
        """Test AuthStatus model"""
        from app.models.auth import AuthStatus

        # Full status
        status = AuthStatus(
            authenticated=True,
            username="admin",
            auth_enabled=True,
            session_timeout=3600,
        )
        assert status.authenticated is True
        assert status.username == "admin"

        # Without username (not authenticated)
        status = AuthStatus(
            authenticated=False, auth_enabled=True, session_timeout=3600
        )
        assert status.authenticated is False
        assert status.username is None

    def test_logout_response_model(self):
        """Test LogoutResponse model"""
        from app.models.auth import LogoutResponse

        resp = LogoutResponse(status="success", message="Logged out")
        assert resp.status == "success"
        assert "Logged out" in resp.message
