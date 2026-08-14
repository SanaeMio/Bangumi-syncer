"""Trakt 邮箱登录服务测试（邮箱 + 验证码 → 自动获取 Bearer 凭证）。"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.services.trakt import email_login
from app.services.trakt.email_login import (
    _oidc_authorize,
    complete_email_login,
    start_email_login,
)


class _FakeResp:
    """模拟 httpx.Response（仅提供 _oidc_authorize 用到的属性）。"""

    def __init__(self, status=200, location=None, payload=None):
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHttp:
    """模拟 AsyncHttpClient（链式配置 + request + aclose）。"""

    def __init__(self, resp):
        self._resp = resp

    def prefix(self, *a, **k):
        return self

    def success_tpl(self, *a, **k):
        return self

    def failure_tpl(self, *a, **k):
        return self

    async def request(self, *a, **k):
        return self._resp

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def _clean_pending():
    """每个用例前清空模块级 pending 会话，避免用例间污染。"""
    email_login._pending.clear()
    yield
    email_login._pending.clear()


def _token_response(**over):
    d = {
        "access_token": "newaccess32characterslongenough123",
        "refresh_token": "newrefresh32characterslongenough456",
        "expires_in": 604800,
        "expires_at": int(time.time()) + 604800,
        "token_type": "bearer",
        "id_token": "jwt",
    }
    d.update(over)
    return d


def _make_config_dict(**over):
    d = {
        "user_id": "u1",
        "access_token": "",
        "refresh_token": None,
        "expires_at": None,
        "enabled": 1,
        "sync_interval": "0 */6 * * *",
        "sync_filter_enabled": 1,
        "last_sync_time": None,
        "auth_type": "oauth",
        "created_at": 1,
        "updated_at": 1,
    }
    d.update(over)
    return d


class TestStartEmailLogin:
    @pytest.mark.asyncio
    async def test_invalid_email_rejected(self):
        """邮箱格式无效：不发信，直接失败"""
        with patch(
            "app.services.trakt.email_login._send_magic", new_callable=AsyncMock
        ) as mock_send:
            result = await start_email_login("u1", "not-an-email")

            assert result["success"] is False
            assert "邮箱格式无效" in result["message"]
            mock_send.assert_not_called()
            assert "u1" not in email_login._pending

    @pytest.mark.asyncio
    async def test_send_success_creates_pending(self):
        """发信成功：建立 pending 会话并返回成功"""
        with patch(
            "app.services.trakt.email_login._send_magic",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            result = await start_email_login("u1", "  User@Example.COM ")

            assert result["success"] is True
            assert "验证码已发送" in result["message"]
            pending = email_login._pending["u1"]
            assert pending.email == "user@example.com"  # 已 trim + 小写
            assert pending.verifier
            assert pending.state

    @pytest.mark.asyncio
    async def test_send_failure_no_pending(self):
        """发信失败：不建立 pending"""
        with patch(
            "app.services.trakt.email_login._send_magic",
            new_callable=AsyncMock,
            return_value=(False, "HTTP 500"),
        ):
            result = await start_email_login("u1", "user@example.com")

            assert result["success"] is False
            assert "发送验证码失败" in result["message"]
            assert "u1" not in email_login._pending

    @pytest.mark.asyncio
    async def test_rate_limited_within_cooldown(self):
        """60 秒内重复发信：限流拒绝，返回 retry_after"""
        with patch(
            "app.services.trakt.email_login._send_magic",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            first = await start_email_login("u1", "user@example.com")
            assert first["success"] is True

            second = await start_email_login("u1", "user@example.com")

            assert second["success"] is False
            assert "过于频繁" in second["message"]
            assert second["retry_after"] is not None
            assert second.get("rate_limited") is True
            # 发信应只被调用一次（第二次被限流拦截）
            assert email_login._send_magic.await_count == 1

    @pytest.mark.asyncio
    async def test_resend_after_cooldown_overwrites_pending(self):
        """冷却结束后重发：覆盖旧 pending（新 verifier/state）"""
        with patch(
            "app.services.trakt.email_login._send_magic",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            first = await start_email_login("u1", "user@example.com")
            assert first["success"] is True
            old = email_login._pending["u1"]
            # 伪造时间流逝，越过冷却窗口
            email_login._pending["u1"].created_at = time.time() - 120

            second = await start_email_login("u1", "other@example.com")

            assert second["success"] is True
            new_pending = email_login._pending["u1"]
            assert new_pending.email == "other@example.com"
            assert new_pending.verifier != old.verifier


class TestOidcAuthorize:
    @pytest.mark.asyncio
    async def test_json_redirect_payload_returns_code(self):
        """200 + JSON 跳转负载（SvelteKit 实测）：从 url 提取 code 并校验 state"""
        state = "state123"
        resp = _FakeResp(
            status=200,
            payload={
                "redirect": True,
                "url": f"https://app.trakt.tv/callback?code=CODE123&state={state}",
            },
        )
        with patch(
            "app.services.trakt.email_login.AsyncHttpClient",
            return_value=_FakeHttp(resp),
        ):
            code, err = await _oidc_authorize(
                "__Secure-better-auth.session_token=x", state, "verifier"
            )

            assert code == "CODE123"
            assert err == ""

    @pytest.mark.asyncio
    async def test_json_redirect_state_mismatch_rejected(self):
        """JSON 跳转负载中 state 不匹配：拒绝"""
        resp = _FakeResp(
            status=200,
            payload={
                "redirect": True,
                "url": "https://app.trakt.tv/callback?code=CODE123&state=WRONG",
            },
        )
        with patch(
            "app.services.trakt.email_login.AsyncHttpClient",
            return_value=_FakeHttp(resp),
        ):
            code, err = await _oidc_authorize("cookie", "state123", "verifier")

            assert code == ""
            assert "状态校验失败" in err

    @pytest.mark.asyncio
    async def test_json_redirect_missing_state_rejected(self):
        """JSON 跳转负载缺 state（空值）：fail-closed 拒绝"""
        resp = _FakeResp(
            status=200,
            payload={
                "redirect": True,
                "url": "https://app.trakt.tv/callback?code=CODE123",
            },
        )
        with patch(
            "app.services.trakt.email_login.AsyncHttpClient",
            return_value=_FakeHttp(resp),
        ):
            code, err = await _oidc_authorize("cookie", "state123", "verifier")

            assert code == ""
            assert "状态校验失败" in err

    @pytest.mark.asyncio
    async def test_redirect_302_missing_state_rejected(self):
        """302 Location 缺 state：fail-closed 拒绝"""
        resp = _FakeResp(
            status=302,
            location="https://app.trakt.tv/callback?code=CODE302",
        )
        with patch(
            "app.services.trakt.email_login.AsyncHttpClient",
            return_value=_FakeHttp(resp),
        ):
            code, err = await _oidc_authorize("cookie", "state123", "verifier")

            assert code == ""
            assert "状态校验失败" in err

    @pytest.mark.asyncio
    async def test_redirect_302_location_returns_code(self):
        """302 + Location 带 code（兼容路径）"""
        state = "state123"
        resp = _FakeResp(
            status=302,
            location=f"https://app.trakt.tv/callback?code=CODE302&state={state}",
        )
        with patch(
            "app.services.trakt.email_login.AsyncHttpClient",
            return_value=_FakeHttp(resp),
        ):
            code, err = await _oidc_authorize("cookie", state, "verifier")

            assert code == "CODE302"
            assert err == ""

    @pytest.mark.asyncio
    async def test_no_code_returns_error(self):
        """既无 302 也无可用 JSON：返回错误"""
        resp = _FakeResp(status=500, payload={})
        with patch(
            "app.services.trakt.email_login.AsyncHttpClient",
            return_value=_FakeHttp(resp),
        ):
            code, err = await _oidc_authorize("cookie", "state123", "verifier")

            assert code == ""
            assert "未返回授权码" in err


class TestCompleteEmailLogin:
    @pytest.mark.asyncio
    async def test_no_pending_session(self):
        """无进行中会话：提示重新发送验证码"""
        result = await complete_email_login("u1", "123456")

        assert result["success"] is False
        assert "已过期" in result["message"] or "重新发送" in result["message"]

    @pytest.mark.asyncio
    async def test_pending_expired(self):
        """会话超过 5 分钟：过期并清理"""
        with patch(
            "app.services.trakt.email_login._send_magic",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            await start_email_login("u1", "user@example.com")
            email_login._pending["u1"].created_at = time.time() - 301

            result = await complete_email_login("u1", "123456")

            assert result["success"] is False
            assert "已过期" in result["message"]
            assert "u1" not in email_login._pending

    @pytest.mark.asyncio
    async def test_otp_format_invalid(self):
        """OTP 非 6 位数字：校验失败，保留 pending"""
        with patch(
            "app.services.trakt.email_login._send_magic",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            await start_email_login("u1", "user@example.com")

            result = await complete_email_login("u1", "12ab")

            assert result["success"] is False
            assert "6 位数字" in result["message"]
            assert "u1" in email_login._pending

    @pytest.mark.asyncio
    async def test_otp_invalid_keeps_pending(self):
        """OTP 无效（401）：失败但保留 pending 供重试"""
        with (
            patch(
                "app.services.trakt.email_login._send_magic",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "app.services.trakt.email_login._submit_otp",
                new_callable=AsyncMock,
                return_value=(False, "验证码无效或已过期，请重新输入或重新发送", ""),
            ),
        ):
            await start_email_login("u1", "user@example.com")

            result = await complete_email_login("u1", "000000")

            assert result["success"] is False
            assert "无效" in result["message"]
            assert "u1" in email_login._pending  # 保留，可重试

    @pytest.mark.asyncio
    async def test_otp_attempt_limit_invalidates_session(self):
        """OTP 连续失败超过上限：会话作废，需重新发送验证码。

        名额在网络调用前占用（并发提交也无法绕过上限），因此第
        MAX+1 次提交会被拒绝。
        """
        from app.services.trakt.email_login import MAX_OTP_ATTEMPTS

        with (
            patch(
                "app.services.trakt.email_login._send_magic",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "app.services.trakt.email_login._submit_otp",
                new_callable=AsyncMock,
                return_value=(False, "验证码无效或已过期，请重新输入或重新发送", ""),
            ),
        ):
            await start_email_login("u1", "user@example.com")

            # 前 MAX 次：均打到上游（无效 OTP，保留会话）
            for _ in range(MAX_OTP_ATTEMPTS):
                result = await complete_email_login("u1", "000000")
                assert "次数过多" not in result["message"]

            # 第 MAX+1 次：达到上限，拒绝并作废会话
            result = await complete_email_login("u1", "000000")

            assert result["success"] is False
            assert "次数过多" in result["message"]
            assert "u1" not in email_login._pending  # 会话已作废

    @pytest.mark.asyncio
    async def test_concurrent_otp_submit_cannot_exceed_limit(self):
        """并发提交 OTP：名额在网络调用前占用，上游调用次数不超过上限。"""
        from app.services.trakt.email_login import MAX_OTP_ATTEMPTS

        with (
            patch(
                "app.services.trakt.email_login._send_magic",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "app.services.trakt.email_login._submit_otp",
                new_callable=AsyncMock,
                return_value=(False, "验证码无效或已过期，请重新输入或重新发送", ""),
            ) as mock_submit,
        ):
            await start_email_login("u1", "user@example.com")

            # 并发发起远超上限的提交，全部并发执行
            results = await asyncio.gather(
                *[
                    complete_email_login("u1", "000000")
                    for _ in range(MAX_OTP_ATTEMPTS * 3)
                ]
            )

            # 打到上游的调用不超过上限
            assert mock_submit.await_count <= MAX_OTP_ATTEMPTS
            # 至少有一次「次数过多」拒绝
            assert any("次数过多" in r["message"] for r in results)
            # 会话最终作废
            assert "u1" not in email_login._pending

    @pytest.mark.asyncio
    async def test_otp_attempts_below_limit_keeps_pending(self):
        """OTP 失败未达上限：会话保留，可继续重试"""
        with (
            patch(
                "app.services.trakt.email_login._send_magic",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "app.services.trakt.email_login._submit_otp",
                new_callable=AsyncMock,
                return_value=(False, "验证码无效或已过期，请重新输入或重新发送", ""),
            ),
        ):
            await start_email_login("u1", "user@example.com")

            result = await complete_email_login("u1", "000000")

            assert result["success"] is False
            assert email_login._pending["u1"].attempts == 1
            assert "u1" in email_login._pending

    @pytest.mark.asyncio
    async def test_full_success_saves_bearer(self):
        """完整成功链路：提交 OTP → OIDC → 落库 + 追加媒体用户名 + 清理 pending"""
        with (
            patch(
                "app.services.trakt.email_login._send_magic",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "app.services.trakt.email_login._submit_otp",
                new_callable=AsyncMock,
                return_value=(True, "", "__Secure-better-auth.session_token=abc123"),
            ),
            patch(
                "app.services.trakt.email_login._oidc_authorize",
                new_callable=AsyncMock,
                return_value=("code123", ""),
            ) as mock_oidc,
            patch(
                "app.services.trakt.email_login._exchange_code",
                new_callable=AsyncMock,
                return_value=(_token_response(), ""),
            ),
            patch("app.services.trakt.email_login.database_manager") as mock_db,
            patch(
                "app.services.trakt.email_login._ensure_media_server_username"
            ) as mock_ensure,
        ):
            mock_db.get_trakt_config.return_value = None
            await start_email_login("u1", "user@example.com")
            pending = email_login._pending["u1"]

            result = await complete_email_login("u1", "123456")

            assert result["success"] is True
            assert "Bearer 凭证已保存" in result["message"]
            assert result["expires_at"] is not None
            # OIDC 拿到的是当前会话的 state
            mock_oidc.assert_awaited_once_with(
                "__Secure-better-auth.session_token=abc123",
                pending.state,
                pending.verifier,
            )
            # 落库：auth_type=bearer + 新 token
            saved = mock_db.save_trakt_config.call_args[0][0]
            assert saved["auth_type"] == "bearer"
            assert saved["access_token"] == "newaccess32characterslongenough123"
            assert saved["refresh_token"] == "newrefresh32characterslongenough456"
            assert saved["expires_at"] is not None
            mock_ensure.assert_called_once_with("u1")
            # pending 已清理
            assert "u1" not in email_login._pending

    @pytest.mark.asyncio
    async def test_oidc_failure_clears_pending(self):
        """OTP 成功后 OIDC 失败：报错并清理 pending（会话已消费）"""
        with (
            patch(
                "app.services.trakt.email_login._send_magic",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "app.services.trakt.email_login._submit_otp",
                new_callable=AsyncMock,
                return_value=(True, "", "__Secure-better-auth.session_token=abc123"),
            ),
            patch(
                "app.services.trakt.email_login._oidc_authorize",
                new_callable=AsyncMock,
                return_value=("", "授权请求未返回授权码 (HTTP 401)"),
            ),
            patch("app.services.trakt.email_login.database_manager") as mock_db,
        ):
            await start_email_login("u1", "user@example.com")

            result = await complete_email_login("u1", "123456")

            assert result["success"] is False
            assert "授权" in result["message"]
            mock_db.save_trakt_config.assert_not_called()
            assert "u1" not in email_login._pending

    @pytest.mark.asyncio
    async def test_exchange_response_missing_tokens_not_saved(self):
        """token 响应缺 access/refresh 字段：不落库，返回明确错误"""
        with (
            patch(
                "app.services.trakt.email_login._send_magic",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "app.services.trakt.email_login._submit_otp",
                new_callable=AsyncMock,
                return_value=(True, "", "__Secure-better-auth.session_token=abc123"),
            ),
            patch(
                "app.services.trakt.email_login._oidc_authorize",
                new_callable=AsyncMock,
                return_value=("code123", ""),
            ),
            patch(
                "app.services.trakt.email_login._exchange_code",
                new_callable=AsyncMock,
                return_value=({"expires_in": 3600}, ""),
            ),
            patch("app.services.trakt.email_login.database_manager") as mock_db,
        ):
            await start_email_login("u1", "user@example.com")

            result = await complete_email_login("u1", "123456")

            assert result["success"] is False
            assert "缺少令牌字段" in result["message"]
            mock_db.save_trakt_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_persist_tokens_uses_refresh_lock(self):
        """邮箱登录落库与刷新共用 per-user 锁（防止并发旋转互相覆盖）"""
        lock = asyncio.Lock()
        with (
            patch("app.services.trakt.email_login.database_manager") as mock_db,
            patch(
                "app.services.trakt.email_login._get_refresh_lock",
                new_callable=AsyncMock,
                return_value=lock,
            ) as mock_lock,
            patch("app.services.trakt.email_login._ensure_media_server_username"),
        ):
            mock_db.get_trakt_config.return_value = None

            expires_at = await email_login._persist_tokens("u1", _token_response())

            assert expires_at is not None
            mock_lock.assert_awaited_once_with("u1")
            assert not lock.locked()  # 落库后已释放
            saved = mock_db.save_trakt_config.call_args[0][0]
            assert saved["auth_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_exchange_failure_clears_pending(self):
        """换取 token 失败：报错并清理 pending，不落库"""
        with (
            patch(
                "app.services.trakt.email_login._send_magic",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "app.services.trakt.email_login._submit_otp",
                new_callable=AsyncMock,
                return_value=(True, "", "__Secure-better-auth.session_token=abc123"),
            ),
            patch(
                "app.services.trakt.email_login._oidc_authorize",
                new_callable=AsyncMock,
                return_value=("code123", ""),
            ),
            patch(
                "app.services.trakt.email_login._exchange_code",
                new_callable=AsyncMock,
                return_value=(None, "HTTP 500: oops"),
            ),
            patch("app.services.trakt.email_login.database_manager") as mock_db,
        ):
            await start_email_login("u1", "user@example.com")

            result = await complete_email_login("u1", "123456")

            assert result["success"] is False
            assert "HTTP 500" in result["message"]
            mock_db.save_trakt_config.assert_not_called()
            assert "u1" not in email_login._pending

    @pytest.mark.asyncio
    async def test_updates_existing_config(self):
        """已有配置：覆盖保存为 bearer 模式"""
        with (
            patch(
                "app.services.trakt.email_login._send_magic",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            patch(
                "app.services.trakt.email_login._submit_otp",
                new_callable=AsyncMock,
                return_value=(True, "", "__Secure-better-auth.session_token=abc123"),
            ),
            patch(
                "app.services.trakt.email_login._oidc_authorize",
                new_callable=AsyncMock,
                return_value=("code123", ""),
            ),
            patch(
                "app.services.trakt.email_login._exchange_code",
                new_callable=AsyncMock,
                return_value=(_token_response(), ""),
            ),
            patch("app.services.trakt.email_login.database_manager") as mock_db,
            patch("app.services.trakt.email_login._ensure_media_server_username"),
        ):
            # 既有 oauth 配置
            mock_db.get_trakt_config.return_value = _make_config_dict(
                auth_type="oauth", access_token="old_access"
            )
            await start_email_login("u1", "user@example.com")

            result = await complete_email_login("u1", "123456")

            assert result["success"] is True
            saved = mock_db.save_trakt_config.call_args[0][0]
            assert saved["auth_type"] == "bearer"
            assert saved["access_token"] == "newaccess32characterslongenough123"
            assert saved["refresh_token"] == "newrefresh32characterslongenough456"
