"""
Summary API 模型验证测试与端点集成测试。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.models.summary import (
    LLMConfigResponse,
    LLMConfigUpdate,
    LLMTestResponse,
    LLMUsageStatsResponse,
    SummaryJobCreate,
    SummaryJobResponse,
    SummaryJobTestResponse,
    SummaryJobUpdate,
)

# ========== LLMConfigResponse ==========


class TestLLMConfigResponse:
    """LLMConfigResponse 模型测试。"""

    def test_default_values(self):
        """验证构造时的默认字段值。"""
        model = LLMConfigResponse()
        assert model.api_base == "https://api.openai.com/v1"
        assert model.api_key == ""
        assert model.model == "gpt-4o-mini"
        assert model.max_tokens == 2000
        assert model.temperature == 0.7
        assert model.timeout == 60
        assert model.provider == "openai_compat"
        assert model.thinking_level == "off"

    def test_override_values(self):
        """验证显式字段值可被接受。"""
        model = LLMConfigResponse(
            api_base="https://custom.api/v1",
            api_key="***sk-abc123",
            model="gpt-4",
            max_tokens=4000,
            temperature=0.3,
            timeout=120,
        )
        assert model.api_base == "https://custom.api/v1"
        assert model.api_key == "***sk-abc123"
        assert model.model == "gpt-4"
        assert model.max_tokens == 4000
        assert model.temperature == 0.3
        assert model.timeout == 120

    def test_model_dump(self):
        """验证 model_dump() 可正确序列化。"""
        model = LLMConfigResponse(api_key="***hidden")
        data = model.model_dump()
        assert data["api_key"] == "***hidden"


# ========== LLMConfigUpdate ==========


class TestLLMConfigUpdate:
    """LLMConfigUpdate 模型测试。"""

    def test_all_none_is_valid(self):
        """验证空的局部更新是合法的。"""
        model = LLMConfigUpdate()
        assert model.api_base is None
        assert model.api_key is None
        assert model.model is None
        assert model.max_tokens is None
        assert model.temperature is None
        assert model.timeout is None

    def test_single_field_update(self):
        """验证仅设置单个字段（局部更新）。"""
        model = LLMConfigUpdate(model="gpt-4")
        assert model.model == "gpt-4"
        assert model.api_base is None
        assert model.api_key is None

    def test_multiple_fields_update(self):
        """验证设置多个字段。"""
        model = LLMConfigUpdate(
            api_base="https://new.api/v1",
            temperature=0.1,
        )
        assert model.api_base == "https://new.api/v1"
        assert model.temperature == 0.1
        assert model.max_tokens is None

    def test_model_dump_excludes_none(self):
        """验证 model_dump(exclude_none=True) 会省略 None 字段。"""
        model = LLMConfigUpdate(model="gpt-4")
        data = model.model_dump(exclude_none=True)
        assert "model" in data
        assert data["model"] == "gpt-4"
        assert "api_base" not in data

    def test_valid_enum_values_accepted(self):
        """provider / thinking_level 的合法枚举值可被接受。"""
        model = LLMConfigUpdate(provider="anthropic_compat", thinking_level="high")
        assert model.provider == "anthropic_compat"
        assert model.thinking_level == "high"

    def test_invalid_provider_rejected(self):
        """非法 provider 在模型层抛 ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LLMConfigUpdate(provider="banana")

    def test_invalid_thinking_level_rejected(self):
        """非法 thinking_level 在模型层抛 ValidationError（不静默兜底为 off）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LLMConfigUpdate(thinking_level="banana")


# ========== LLMTestResponse ==========


class TestLLMTestResponse:
    """LLMTestResponse 模型测试。"""

    def test_minimal_creation(self):
        """验证仅设置必填字段。"""
        model = LLMTestResponse(success=True, message="OK")
        assert model.success is True
        assert model.message == "OK"
        assert model.model is None
        assert model.latency_ms is None

    def test_full_creation(self):
        """验证所有字段。"""
        model = LLMTestResponse(
            success=True,
            message="Connected",
            model="gpt-4o-mini",
            latency_ms=350,
        )
        assert model.model == "gpt-4o-mini"
        assert model.latency_ms == 350


# ========== SummaryJobCreate ==========


class TestSummaryJobCreate:
    """SummaryJobCreate 模型测试。"""

    def test_default_values(self):
        """验证默认字段值。"""
        model = SummaryJobCreate()
        assert model.name == "New Summary"
        assert model.cron == "0 21 * * *"
        assert model.lookback_days == 1
        assert model.user_name == ""
        assert model.system_prompt == ""
        assert model.max_records == -1
        assert model.enabled is True

    def test_all_fields_set(self):
        """验证可显式设置所有字段。"""
        model = SummaryJobCreate(
            name="Daily Anime Summary",
            cron="0 9 * * *",
            lookback_days=3,
            user_name="dad",
            system_prompt="You are a helpful anime analyst.",
            max_records=500,
            enabled=False,
        )
        assert model.name == "Daily Anime Summary"
        assert model.cron == "0 9 * * *"
        assert model.lookback_days == 3
        assert model.user_name == "dad"
        assert model.system_prompt == "You are a helpful anime analyst."
        assert model.max_records == 500
        assert model.enabled is False

    def test_memory_limit_negative_rejected(self):
        """memory_limit=-1 在模型层抛 ValidationError（ge=0）。"""
        with pytest.raises(ValidationError):
            SummaryJobCreate(memory_limit=-1)

    def test_related_limit_negative_rejected(self):
        """related_limit=-1 在模型层抛 ValidationError（ge=0）。"""
        with pytest.raises(ValidationError):
            SummaryJobCreate(related_limit=-1)

    def test_memory_limit_too_large_rejected(self):
        """memory_limit=1001 在模型层抛 ValidationError（le=1000）。"""
        with pytest.raises(ValidationError):
            SummaryJobCreate(memory_limit=1001)

    def test_related_limit_too_large_rejected(self):
        """related_limit=1001 在模型层抛 ValidationError（le=1000）。"""
        with pytest.raises(ValidationError):
            SummaryJobCreate(related_limit=1001)

    def test_boundary_values_accepted(self):
        """边界值 memory_limit=0 与 related_limit=1000 构造成功。"""
        model = SummaryJobCreate(memory_limit=0, related_limit=1000)
        assert model.memory_limit == 0
        assert model.related_limit == 1000


# ========== SummaryJobUpdate ==========


class TestSummaryJobUpdate:
    """SummaryJobUpdate 模型测试。"""

    def test_all_none_is_valid(self):
        """验证空的局部更新是合法的。"""
        model = SummaryJobUpdate()
        assert model.name is None
        assert model.cron is None
        assert model.lookback_days is None
        assert model.user_name is None
        assert model.system_prompt is None
        assert model.max_records is None
        assert model.enabled is None

    def test_single_field_update(self):
        """验证仅设置单个字段。"""
        model = SummaryJobUpdate(enabled=False)
        assert model.enabled is False
        assert model.name is None
        assert model.cron is None

    def test_multiple_fields_update(self):
        """验证设置多个字段。"""
        model = SummaryJobUpdate(
            name="Updated Job",
            lookback_days=7,
        )
        assert model.name == "Updated Job"
        assert model.lookback_days == 7
        assert model.cron is None

    def test_model_dump_excludes_none(self):
        """验证 model_dump(exclude_none=True) 会省略 None 字段。"""
        model = SummaryJobUpdate(enabled=False)
        data = model.model_dump(exclude_none=True)
        assert "enabled" in data
        assert "name" not in data

    def test_memory_limit_negative_rejected(self):
        """memory_limit=-1 在模型层抛 ValidationError（ge=0）。"""
        with pytest.raises(ValidationError):
            SummaryJobUpdate(memory_limit=-1)

    def test_related_limit_negative_rejected(self):
        """related_limit=-1 在模型层抛 ValidationError（ge=0）。"""
        with pytest.raises(ValidationError):
            SummaryJobUpdate(related_limit=-1)

    def test_memory_limit_too_large_rejected(self):
        """memory_limit=1001 在模型层抛 ValidationError（le=1000）。"""
        with pytest.raises(ValidationError):
            SummaryJobUpdate(memory_limit=1001)

    def test_related_limit_too_large_rejected(self):
        """related_limit=1001 在模型层抛 ValidationError（le=1000）。"""
        with pytest.raises(ValidationError):
            SummaryJobUpdate(related_limit=1001)

    def test_boundary_values_accepted(self):
        """边界值 memory_limit=0 与 related_limit=1000 构造成功。"""
        model = SummaryJobUpdate(memory_limit=0, related_limit=1000)
        assert model.memory_limit == 0
        assert model.related_limit == 1000


# ========== SummaryJobResponse ==========


class TestSummaryJobResponse:
    """SummaryJobResponse 模型测试。"""

    def test_creation_with_all_fields(self):
        """验证使用所有字段创建模型。"""
        model = SummaryJobResponse(
            id=1,
            name="Test Job",
            cron="0 21 * * *",
            lookback_days=1,
            user_name="dad",
            system_prompt="Be concise.",
            max_records=200,
            enabled=True,
            notification_type="watching_summary_Test Job",
        )
        assert model.name == "Test Job"
        assert model.notification_type == "watching_summary_Test Job"

    def test_notification_type_empty_by_default(self):
        """验证 notification_type 默认为空字符串。"""
        model = SummaryJobResponse(
            id=1,
            name="Test Job",
            cron="0 21 * * *",
            lookback_days=1,
            user_name="dad",
            system_prompt="Be concise.",
            max_records=200,
            enabled=True,
        )
        assert model.notification_type == ""

    # ========== from_config_dict 测试 ==========

    def test_from_config_dict_empty_user_name(self):
        """notification_type 使用任务名称。"""
        data = {
            "id": 1,
            "name": "Test",
            "cron": "0 21 * * *",
            "lookback_days": 1,
            "user_name": "",
            "system_prompt": "",
            "max_records": 200,
            "enabled": True,
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.notification_type == "watching_summary_Test"
        assert model.name == "Test"

    def test_from_config_dict_with_user_name(self):
        """notification_type 使用任务名称，而非 user_name。"""
        data = {
            "id": 2,
            "name": "Dad's Summary",
            "cron": "0 9 * * *",
            "lookback_days": 3,
            "user_name": "dad",
            "system_prompt": "You are dad.",
            "max_records": 300,
            "enabled": True,
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.notification_type == "watching_summary_Dad's Summary"
        assert model.name == "Dad's Summary"
        assert model.user_name == "dad"

    def test_from_config_dict_missing_optional_keys(self):
        """验证当字典中缺少键时，会应用默认值。"""
        data = {
            "id": 3,
            "name": "Minimal Job",
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.name == "Minimal Job"
        assert model.cron == "0 21 * * *"
        assert model.lookback_days == 1
        assert model.user_name == ""
        assert model.system_prompt == ""
        assert model.max_records == -1
        assert model.enabled is True
        assert model.notification_type == "watching_summary_Minimal Job"

    def test_from_config_dict_user_name_none(self):
        """notification_type 使用任务名称，不受 user_name=None 影响。"""
        data = {
            "id": 4,
            "name": "None User",
            "user_name": None,
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.user_name == ""
        assert model.notification_type == "watching_summary_None User"

    def test_from_config_dict_disabled_job(self):
        """验证 enabled=False 被正确保留。"""
        data = {
            "id": 5,
            "name": "Disabled Job",
            "enabled": False,
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.enabled is False

    def test_from_config_dict_enabled_int_zero(self):
        """验证 enabled=0 被当作 False。"""
        data = {
            "id": 6,
            "name": "Int Zero Job",
            "enabled": 0,
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.enabled is False

    def test_from_config_dict_enabled_int_one(self):
        """验证 enabled=1 被当作 True。"""
        data = {
            "id": 7,
            "name": "Int One Job",
            "enabled": 1,
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.enabled is True

    def test_from_config_dict_with_extra_keys(self):
        """验证字典中的额外键会被忽略。"""
        data = {
            "id": 8,
            "name": "Extra Keys",
            "unknown_field": "should be ignored",
            "another_extra": 42,
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.name == "Extra Keys"

    def test_from_config_dict_invalid_int_falls_back_to_default(self):
        """F3（H2 同源）：lookback_days/max_records 为非法字符串时回落默认而非抛 500。

        config.ini 写 `lookback_days=abc`/`max_records=abc` 不得让整个端点 500。
        """
        data = {
            "id": 9,
            "name": "Bad Int Job",
            "lookback_days": "abc",
            "max_records": "abc",
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.lookback_days == 1  # 默认 1
        assert model.max_records == -1  # 默认 -1（不限制）


# ========== SummaryJobTestResponse ==========


class TestSummaryJobTestResponse:
    """SummaryJobTestResponse 模型测试。"""

    def test_minimal_creation(self):
        """验证仅设置必填字段，其余使用默认值。"""
        model = SummaryJobTestResponse(success=True, job_name="Test Job")
        assert model.success is True
        assert model.job_name == "Test Job"
        assert model.summary_text == ""
        assert model.model == ""
        assert model.prompt_tokens == 0
        assert model.completion_tokens == 0
        assert model.total_tokens == 0
        assert model.latency_ms == 0
        assert model.record_count == 0
        assert model.error_message == ""

    def test_failure_response(self):
        """验证错误字段被正确填充。"""
        model = SummaryJobTestResponse(
            success=False,
            job_name="Failing Job",
            error_message="LLM timeout after 60s",
        )
        assert model.success is False
        assert model.error_message == "LLM timeout after 60s"

    def test_success_response_with_tokens(self):
        """验证成功时 token 数量和延迟信息被正确填充。"""
        model = SummaryJobTestResponse(
            success=True,
            job_name="Daily Summary",
            summary_text="Today you watched 3 episodes...",
            model="gpt-4o-mini",
            prompt_tokens=500,
            completion_tokens=200,
            total_tokens=700,
            latency_ms=1500,
            record_count=3,
        )
        assert model.summary_text == "Today you watched 3 episodes..."
        assert model.prompt_tokens == 500
        assert model.completion_tokens == 200
        assert model.total_tokens == 700
        assert model.latency_ms == 1500
        assert model.record_count == 3


# ========== LLMUsageStatsResponse ==========


# ========== API 端点测试 ==========


class TestGetLLMConfig:
    """GET /llm 端点测试。"""

    @pytest.fixture
    def _setup(self):
        with patch("app.api.llm.config_manager") as mock_cm:
            mock_cm.get_llm_config.return_value = {
                "api_base": "https://custom.api/v1",
                "api_key": "sk-very-long-api-key-12345",
                "model": "gpt-4",
                "max_tokens": 4000,
                "temperature": 0.3,
                "timeout": 120,
            }
            yield mock_cm

    @pytest.mark.asyncio
    async def test_returns_config_with_masked_api_key(self, _setup):
        """GET /llm 应返回带有脱敏 api_key 的配置。"""
        from httpx import ASGITransport, AsyncClient

        app = self._create_test_app()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/llm/conf")
            assert response.status_code == 200
            data = response.json()
            assert data["api_base"] == "https://custom.api/v1"
            assert data["api_key"] == "***2345"
            assert data["model"] == "gpt-4"
            assert data["max_tokens"] == 4000
            assert data["temperature"] == 0.3
            assert data["timeout"] == 120

    @pytest.mark.asyncio
    async def test_masks_short_api_key(self):
        """GET /llm 应将短 api_key 脱敏为 '***'。"""
        from httpx import ASGITransport, AsyncClient

        app = self._create_test_app()
        with patch("app.api.llm.config_manager") as mock_cm:
            mock_cm.get_llm_config.return_value = {
                "api_base": "",
                "api_key": "sk",
                "model": "",
                "max_tokens": 2000,
                "temperature": 0.7,
                "timeout": 60,
            }
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/llm/conf")
                assert response.json()["api_key"] == "***"

    @pytest.mark.asyncio
    async def test_handles_empty_api_key(self):
        """GET /llm 应优雅处理空的 api_key。"""
        from httpx import ASGITransport, AsyncClient

        app = self._create_test_app()
        with patch("app.api.llm.config_manager") as mock_cm:
            mock_cm.get_llm_config.return_value = {
                "api_base": "",
                "api_key": "",
                "model": "",
                "max_tokens": 2000,
                "temperature": 0.7,
                "timeout": 60,
            }
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/llm/conf")
                assert response.json()["api_key"] == ""

    @pytest.mark.asyncio
    async def test_returns_provider_and_thinking_level(self):
        """Scenario 6.2: GET /llm 返回 provider 与 thinking_level。"""
        from httpx import ASGITransport, AsyncClient

        app = self._create_test_app()
        with patch("app.api.llm.config_manager") as mock_cm:
            mock_cm.get_llm_config.return_value = {
                "api_base": "",
                "api_key": "",
                "model": "",
                "max_tokens": 2000,
                "temperature": 0.7,
                "timeout": 60,
                "provider": "anthropic_compat",
                "thinking_level": "high",
            }
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/llm/conf")
                data = response.json()
                assert data["provider"] == "anthropic_compat"
                assert data["thinking_level"] == "high"

    @pytest.mark.asyncio
    async def test_returns_thinking_level_default(self):
        """Scenario 6.3: 未配置 provider/thinking_level 时返回缺省值。"""
        from httpx import ASGITransport, AsyncClient

        app = self._create_test_app()
        with patch("app.api.llm.config_manager") as mock_cm:
            mock_cm.get_llm_config.return_value = {
                "api_base": "",
                "api_key": "",
                "model": "",
                "max_tokens": 2000,
                "temperature": 0.7,
                "timeout": 60,
            }
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/llm/conf")
                data = response.json()
                assert data["provider"] == "openai_compat"
                assert data["thinking_level"] == "off"

    def _create_test_app(self):
        """创建一个带有认证覆盖的 FastAPI 测试应用。"""
        from fastapi import FastAPI

        from app.api.deps import get_current_user_flexible
        from app.api.llm import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth
        return app


class TestUpdateLLMConfig:
    """PUT /llm 端点测试。"""

    @pytest.mark.asyncio
    async def test_updates_config_and_calls_reload(self):
        """PUT /llm 应更新配置并重新加载。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.llm import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with patch("app.api.llm.config_manager") as mock_cm:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                payload = {
                    "api_base": "https://new.api/v1",
                    "temperature": 0.1,
                    "model": "gpt-4o",
                }
                response = await client.put("/api/llm/conf", json=payload)
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                assert mock_cm.set_config.call_count == 3
                mock_cm.reload_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_provider_and_thinking_level(self):
        """Scenario 6.1: PUT 保存 provider 与 thinking_level。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.llm import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with patch("app.api.llm.config_manager") as mock_cm:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                payload = {"provider": "anthropic_compat", "thinking_level": "medium"}
                response = await client.put("/api/llm/conf", json=payload)
                assert response.status_code == 200
                assert response.json()["status"] == "success"

                assert mock_cm.set_config.call_count == 2
                options = [c.args[1] for c in mock_cm.set_config.call_args_list]
                assert set(options) == {"provider", "thinking_level"}
                # 均写入 [llm] 段
                assert all(
                    c.args[0] == "llm" for c in mock_cm.set_config.call_args_list
                )
                mock_cm.reload_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_update_is_accepted(self):
        """PUT /llm 传入空 body 仍应成功。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.llm import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with patch("app.api.llm.config_manager") as mock_cm:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.put("/api/llm/conf", json={})
                assert response.status_code == 200
                # 没有需要更新的字段，所以不应调用 set_config
                mock_cm.set_config.assert_not_called()
                mock_cm.reload_config.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {"provider": "banana"},
            {"thinking_level": "banana"},
        ],
    )
    async def test_invalid_enum_values_rejected(self, payload):
        """非法 provider / thinking_level 应在 API 边界被拒绝（422），不写入配置。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.llm import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with patch("app.api.llm.config_manager") as mock_cm:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.put("/api/llm/conf", json=payload)
                assert response.status_code == 422
                mock_cm.set_config.assert_not_called()


class TestTestLLMConnection:
    """POST /api/llm/test 端点测试。"""

    @pytest.mark.asyncio
    async def test_successful_llm_connection(self):
        """POST /api/llm/test 在 LLM 有效时应返回成功。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.llm import router
        from app.services.llm.models import ChatResponse, Usage

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        mock_client = MagicMock()
        mock_client.chat = AsyncMock(
            return_value=ChatResponse(
                content="Hello! How can I help you?",
                model="gpt-4o-mini",
                usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )
        )

        with patch("app.api.llm.get_llm_client", return_value=mock_client):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/llm/test")
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                # 响应不含回复正文（S12：message 为固定文案，短回复截停无展示价值）
                assert data["message"] == "连接成功"
                assert "Hello! How can I help you?" not in data["message"]
                assert data["model"] == "gpt-4o-mini"
                assert data["latency_ms"] is not None
                # 连通性 ping 应限制生成长度（max_tokens=8）且 prompt 极简
                call_kwargs = mock_client.chat.await_args.kwargs
                assert call_kwargs["max_tokens"] == 8
                assert call_kwargs["job_name"] == "llm_test"
                msgs = mock_client.chat.await_args.args[0]
                assert len(msgs) == 1
                assert msgs[0].content == "ping"

    @pytest.mark.asyncio
    async def test_llm_connection_failure(self):
        """POST /api/llm/test 在 LLM 出错时应返回失败。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.llm import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        mock_client = MagicMock()
        mock_client.chat = AsyncMock(side_effect=Exception("Connection refused"))

        with patch("app.api.llm.get_llm_client", return_value=mock_client):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/llm/test")
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is False
                assert "Connection refused" in data["message"]

    @pytest.mark.asyncio
    async def test_llm_connection_empty_content_with_model_fails(self):
        """F2（H1 同步）：content 为空但 model 存在 → 仍视为失败（仅判 not content）。

        旧逻辑 `not response.model and not response.content` 在 model 存在时会误判为
        成功；修复后与 summary 侧一致，仅 `not content` 即失败。
        """
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.llm import router
        from app.services.llm.models import ChatResponse, Usage

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        mock_client = MagicMock()
        mock_client.chat = AsyncMock(
            return_value=ChatResponse(
                content="",
                model="gpt-4o-mini",
                usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            )
        )

        with patch("app.api.llm.get_llm_client", return_value=mock_client):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/llm/test")
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is False


class TestGetLLMStats:
    """GET /api/llm/stats 端点测试。"""

    @pytest.mark.asyncio
    async def test_returns_aggregate_stats(self):
        """GET /api/llm/stats 应返回使用统计信息。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.llm import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with patch("app.api.llm.database_manager") as mock_db:
            from app.core.database.llm_usage import LLMUsageStats

            mock_db.llm_usage.get_stats.return_value = LLMUsageStats(
                total_calls=10,
                total_tokens=5000,
                error_count=1,
                avg_latency_ms=350,
            )
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/llm/stats")
                assert response.status_code == 200
                data = response.json()
                assert data["total_calls"] == 10
                assert data["total_tokens"] == 5000
                assert data["error_count"] == 1
                assert data["avg_latency_ms"] == 350

    @pytest.mark.asyncio
    async def test_passes_scope_and_days_params(self):
        """GET /api/llm/stats 应转发 scope 和 days 参数。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.llm import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with patch("app.api.llm.database_manager") as mock_db:
            from app.core.database.llm_usage import LLMUsageStats

            mock_db.llm_usage.get_stats.return_value = LLMUsageStats()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/llm/stats?scope=detailed&days=7")
                assert response.status_code == 200
                mock_db.llm_usage.get_stats.assert_called_once_with(
                    scope="detailed", days=7
                )


class TestListSummaryJobs:
    """GET /api/summary/jobs 端点测试。"""

    @pytest.mark.asyncio
    async def test_returns_list_of_jobs(self):
        """GET /api/summary/jobs 应返回序列化的任务配置列表。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.summary_jobs import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with patch("app.api.summary_jobs.config_manager") as mock_cm:
            mock_cm.get_summary_configs.return_value = [
                {
                    "id": 1,
                    "name": "Daily Summary",
                    "cron": "0 21 * * *",
                    "lookback_days": 1,
                    "user_name": "",
                    "system_prompt": "",
                    "max_records": 200,
                    "enabled": True,
                },
                {
                    "id": 2,
                    "name": "Dad Summary",
                    "cron": "0 9 * * *",
                    "lookback_days": 3,
                    "user_name": "dad",
                    "system_prompt": "Be dad.",
                    "max_records": 300,
                    "enabled": False,
                },
            ]
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/summary/jobs")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                assert len(data["data"]) == 2
                assert data["data"][0]["name"] == "Daily Summary"
                assert (
                    data["data"][1]["notification_type"]
                    == "watching_summary_Dad Summary"
                )

    @pytest.mark.asyncio
    async def test_returns_200_with_invalid_int_config(self):
        """F3（API 级）：config.ini 含非法整型字段时整体返回 200 而非 500。

        旧实现裸 `int()` 会让 `lookback_days=abc`/`max_records=abc` 直接 500。
        """
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.summary_jobs import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with patch("app.api.summary_jobs.config_manager") as mock_cm:
            mock_cm.get_summary_configs.return_value = [
                {
                    "id": 1,
                    "name": "Bad Int Job",
                    "cron": "0 21 * * *",
                    "lookback_days": "abc",
                    "user_name": "",
                    "system_prompt": "",
                    "max_records": "abc",
                    "enabled": True,
                },
            ]
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/summary/jobs")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                assert len(data["data"]) == 1
                # 回落默认：lookback_days=1, max_records=-1
                assert data["data"][0]["lookback_days"] == 1
                assert data["data"][0]["max_records"] == -1

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_configs(self):
        """GET /api/summary/jobs 在没有配置时应返回空列表。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.summary_jobs import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with patch("app.api.summary_jobs.config_manager") as mock_cm:
            mock_cm.get_summary_configs.return_value = []
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/summary/jobs")
                assert response.status_code == 200
                data = response.json()
                assert data["data"] == []


class TestCreateSummaryJob:
    """POST /api/summary/jobs 端点测试。"""

    @pytest.mark.asyncio
    async def test_creates_job_and_calls_scheduler(self):
        """POST /api/summary/jobs 应保存配置并应用调度器。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.summary_jobs import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with (
            patch("app.api.summary_jobs.config_manager") as mock_cm,
            patch("app.api.summary_jobs.summary_scheduler") as mock_scheduler,
        ):
            mock_scheduler.apply_config_after_save = AsyncMock()

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                payload = {
                    "name": "New Test Job",
                    "cron": "0 8 * * *",
                    "lookback_days": 2,
                }
                response = await client.post("/api/summary/jobs", json=payload)
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                mock_cm.save_summary_config.assert_called_once()
                mock_cm.reload_config.assert_called_once()
                mock_scheduler.apply_config_after_save.assert_awaited_once()


class TestUpdateSummaryJob:
    """PUT /api/summary/jobs/{id} 端点测试。"""

    @pytest.mark.asyncio
    async def test_updates_existing_job(self):
        """PUT /api/summary/jobs/{id} 应使用任务 id 更新配置。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.summary_jobs import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with (
            patch("app.api.summary_jobs.config_manager") as mock_cm,
            patch("app.api.summary_jobs.summary_scheduler") as mock_scheduler,
        ):
            mock_scheduler.apply_config_after_save = AsyncMock()

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                payload = {"name": "Updated Job", "enabled": False}
                response = await client.put("/api/summary/jobs/3", json=payload)
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                mock_cm.save_summary_config.assert_called_once()
                # 验证更新字典中包含 job_id
                call_args = mock_cm.save_summary_config.call_args[0][0]
                assert call_args["name"] == "Updated Job"
                mock_cm.reload_config.assert_called_once()
                mock_scheduler.apply_config_after_save.assert_awaited_once()


class TestDeleteSummaryJob:
    """DELETE /api/summary/jobs/{id} 端点测试。"""

    @pytest.mark.asyncio
    async def test_deletes_job_and_calls_scheduler(self):
        """DELETE /api/summary/jobs/{id} 应删除配置并应用调度器。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.summary_jobs import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with (
            patch("app.api.summary_jobs.config_manager") as mock_cm,
            patch("app.api.summary_jobs.summary_scheduler") as mock_scheduler,
            patch("app.api.summary_jobs.memory_service") as mock_memory,
        ):
            mock_scheduler.apply_config_after_save = AsyncMock()

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.delete("/api/summary/jobs/Dad%20Summary")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                mock_cm.delete_summary_config.assert_called_once_with("Dad Summary")
                # 记忆清理联动：避免孤儿记忆 + 悬挂 consumed_run_id
                mock_memory.clear_task.assert_called_once_with(
                    "summary", "summary-Dad Summary"
                )
                mock_cm.reload_config.assert_called_once()
                mock_scheduler.apply_config_after_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_memory_clear_failure_returns_500(self):
        """记忆清理异常 → 500（不得假成功：配置已删但记忆残留需用户重试）。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.summary_scheduler") as mock_scheduler,
                patch("app.api.summary_jobs.memory_service") as mock_memory,
            ):
                mock_scheduler.apply_config_after_save = AsyncMock()
                mock_memory.clear_task.side_effect = RuntimeError("db down")
                response = await client.delete("/api/summary/jobs/Dad%20Summary")
                assert response.status_code == 500
                assert (
                    response.json()["detail"]
                    == "删除任务失败：记忆清理异常，请稍后重试"
                )
                # 顺序前置证据：clear 失败时配置尚未删除，任务仍可寻址重试
                mock_cm.delete_summary_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_retry_self_heals_after_clear_failure(self):
        """首次 clear 失败 → 500；重试 clear 成功 → 配置删除、200（幂等自愈）。

        顺序前置（clear → delete_config）保证失败时旧配置仍在，用户重试即可收敛。
        """
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.summary_scheduler") as mock_scheduler,
                patch("app.api.summary_jobs.memory_service") as mock_memory,
            ):
                mock_scheduler.apply_config_after_save = AsyncMock()
                # 第一次 clear 抛异常，第二次成功
                mock_memory.clear_task.side_effect = [RuntimeError("db down"), None]

                first = await client.delete("/api/summary/jobs/Dad%20Summary")
                assert first.status_code == 500
                mock_cm.delete_summary_config.assert_not_called()

                second = await client.delete("/api/summary/jobs/Dad%20Summary")
                assert second.status_code == 200
                assert second.json()["status"] == "success"
                mock_cm.delete_summary_config.assert_called_once_with("Dad Summary")

    @pytest.mark.asyncio
    async def test_delete_clears_memory_before_deleting_config(self):
        """成功路径顺序：clear_task 先于 delete_summary_config（旧名保持可寻址）。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.summary_scheduler") as mock_scheduler,
                patch("app.api.summary_jobs.memory_service") as mock_memory,
            ):
                mock_scheduler.apply_config_after_save = AsyncMock()
                # 把 memory_service.clear_task 挂到 config_manager 上以统一记录调用顺序
                mock_cm.attach_mock(mock_memory.clear_task, "clear_task")

                response = await client.delete("/api/summary/jobs/Dad%20Summary")
                assert response.status_code == 200

                call_names = [c[0] for c in mock_cm.mock_calls]
                assert "clear_task" in call_names
                assert "delete_summary_config" in call_names
                assert call_names.index("clear_task") < call_names.index(
                    "delete_summary_config"
                )


class TestTestSummaryJob:
    """POST /api/summary/jobs/{id}/test 端点测试。"""

    @pytest.mark.asyncio
    async def test_returns_test_result(self):
        """POST /api/summary/jobs/{id}/test 应运行生成并返回结果。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.summary_jobs import router
        from app.services.llm.models import Usage

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with (
            patch("app.api.summary_jobs.config_manager") as mock_cm,
            patch("app.api.summary_jobs.summary_service") as mock_service,
        ):
            mock_cm.get_summary_configs.return_value = [
                {
                    "id": 1,
                    "name": "Test Job",
                    "cron": "0 21 * * *",
                    "lookback_days": 1,
                    "user_name": "",
                    "system_prompt": "",
                    "max_records": 200,
                    "enabled": True,
                },
            ]
            mock_service.generate_summary = AsyncMock(
                return_value={
                    "summary_text": "Today you watched 3 episodes.",
                    "model": "gpt-4o-mini",
                    "usage": Usage(
                        prompt_tokens=100, completion_tokens=50, total_tokens=150
                    ),
                    "record_count": 3,
                    "date_from": "2024-01-01",
                    "date_to": "2024-01-02",
                }
            )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/summary/jobs/Test%20Job/test")
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["job_name"] == "Test Job"
                assert "Today you watched 3 episodes" in data["summary_text"]
                assert data["model"] == "gpt-4o-mini"
                assert data["prompt_tokens"] == 100
                assert data["completion_tokens"] == 50
                assert data["total_tokens"] == 150
                assert data["record_count"] == 3

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent_job(self):
        """POST /api/summary/jobs/{id}/test 对不存在的任务应返回 404。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.summary_jobs import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with patch("app.api.summary_jobs.config_manager") as mock_cm:
            mock_cm.get_summary_configs.return_value = [
                {
                    "id": 1,
                    "name": "Other Job",
                    "cron": "0 21 * * *",
                    "lookback_days": 1,
                    "user_name": "",
                    "system_prompt": "",
                    "max_records": 200,
                    "enabled": True,
                },
            ]
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/summary/jobs/Nonexistent/test")
                assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_summary_text_with_usage_fails(self):
        """F6（H1-API 回归）：summary_text 为空但 usage 存在 → success=False + error_message。

        H1 修复后判定条件为仅 `not summary_text`；此前缺此缺陷场景测试。
        """
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.summary_jobs import router
        from app.services.llm.models import Usage

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with (
            patch("app.api.summary_jobs.config_manager") as mock_cm,
            patch("app.api.summary_jobs.summary_service") as mock_service,
        ):
            mock_cm.get_summary_configs.return_value = [
                {
                    "id": 1,
                    "name": "Empty Job",
                    "cron": "0 21 * * *",
                    "lookback_days": 1,
                    "user_name": "",
                    "system_prompt": "",
                    "max_records": 200,
                    "enabled": True,
                },
            ]
            mock_service.generate_summary = AsyncMock(
                return_value={
                    "summary_text": "",  # 空正文（如重试耗尽）
                    "model": "gpt-4o-mini",
                    "usage": Usage(
                        prompt_tokens=100, completion_tokens=0, total_tokens=100
                    ),
                    "record_count": 3,
                    "date_from": "2024-01-01",
                    "date_to": "2024-01-02",
                }
            )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/summary/jobs/Empty%20Job/test")
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is False
                assert data["error_message"]
                assert data["record_count"] == 3


class TestTriggerSummaryJob:
    """POST /api/summary/jobs/{id}/trigger 端点测试。"""

    @pytest.mark.asyncio
    async def test_triggers_job_execution(self):
        """POST /api/summary/jobs/{id}/trigger 应执行该任务。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.summary_jobs import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with (
            patch("app.api.summary_jobs.config_manager") as mock_cm,
            patch("app.api.summary_jobs.summary_service") as mock_service,
        ):
            mock_cm.get_summary_configs.return_value = [
                {
                    "id": 1,
                    "name": "Trigger Job",
                    "cron": "0 21 * * *",
                    "lookback_days": 1,
                    "user_name": "dad",
                    "system_prompt": "",
                    "max_records": 200,
                    "enabled": True,
                },
            ]
            mock_service.execute_job = AsyncMock()

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/summary/jobs/Trigger%20Job/trigger")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                assert "Trigger Job" in data["message"]
                mock_service.execute_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_trigger_returns_skipped_when_already_running(self):
        """execute_job 返回 False（任务已执行中）→ 200 + status=skipped + 中文提示。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.summary_jobs import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with (
            patch("app.api.summary_jobs.config_manager") as mock_cm,
            patch("app.api.summary_jobs.summary_service") as mock_service,
        ):
            mock_cm.get_summary_configs.return_value = [
                {
                    "id": 1,
                    "name": "Busy Job",
                    "cron": "0 21 * * *",
                    "lookback_days": 1,
                    "user_name": "",
                    "system_prompt": "",
                    "max_records": 200,
                    "enabled": True,
                },
            ]
            mock_service.execute_job = AsyncMock(return_value=False)

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/summary/jobs/Busy%20Job/trigger")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "skipped"
                assert "任务正在执行中" in data["message"]
                assert "已跳过" in data["message"]
                mock_service.execute_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent_job(self):
        """POST /api/summary/jobs/{id}/trigger 对不存在的任务应返回 404。"""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.deps import get_current_user_flexible
        from app.api.summary_jobs import router

        app = FastAPI()
        app.include_router(router)

        async def mock_auth(request=None, credentials=None):
            return {"username": "testuser"}

        app.dependency_overrides[get_current_user_flexible] = mock_auth

        with patch("app.api.summary_jobs.config_manager") as mock_cm:
            mock_cm.get_summary_configs.return_value = []
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/summary/jobs/Nonexistent/trigger")
                assert response.status_code == 404


class TestLLMUsageStatsResponse:
    """LLMUsageStatsResponse 模型测试。"""

    def test_all_defaults(self):
        """验证所有字段都有正确的默认值。"""
        model = LLMUsageStatsResponse()
        assert model.total_calls == 0
        assert model.total_tokens == 0
        assert model.total_prompt_tokens == 0
        assert model.total_completion_tokens == 0
        assert model.error_count == 0
        assert model.avg_latency_ms == 0
        assert model.by_model == []
        assert model.by_job == []
        assert model.daily == []

    def test_with_data(self):
        """Verify fields can be populated with actual data."""
        model = LLMUsageStatsResponse(
            total_calls=42,
            total_tokens=15000,
            total_prompt_tokens=10000,
            total_completion_tokens=5000,
            error_count=2,
            avg_latency_ms=1200,
            by_model=[{"model": "gpt-4o-mini", "calls": 30, "tokens": 10000}],
            by_job=[{"job_name": "Daily Summary", "calls": 20, "tokens": 7000}],
            daily=[{"date": "2024-01-15", "calls": 10, "tokens": 3000}],
        )
        assert model.total_calls == 42
        assert len(model.by_model) == 1
        assert model.by_model[0]["model"] == "gpt-4o-mini"
        assert len(model.by_job) == 1
        assert len(model.daily) == 1


# ========== 记忆清理与改名联动（Phase 2.0.3，S1/S3/S5） ==========


def _make_summary_app():
    """构建只挂 summary_jobs router 的测试 app（mock 认证）。"""
    from fastapi import FastAPI

    from app.api.deps import get_current_user_flexible
    from app.api.summary_jobs import router

    app = FastAPI()
    app.include_router(router)

    async def mock_auth(request=None, credentials=None):
        return {"username": "testuser"}

    app.dependency_overrides[get_current_user_flexible] = mock_auth
    return app


def _assert_422_detail_contract(response, loc_field):
    """断言 422 响应体 detail 结构契约（供前端 apiFetch 解析）。

    - detail 必须是列表（不是对象），否则前端 `[object Object]` 解析失败
    - 列表项含 "msg" 字符串键
    - 至少一项定位到 ["body", loc_field]
    """
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert all(
        isinstance(item, dict) and isinstance(item.get("msg"), str) for item in detail
    )
    assert any(item.get("loc") == ["body", loc_field] for item in detail)


class TestSummaryLimitValidation:
    """记忆/关联条数越界契约测试（T1）。

    锁定后端对 memory_limit / related_limit 的校验契约：
    非法值 → 422 且 detail 为对象数组（含 msg 与 loc），且不得入库；
    边界合法值 → 200 且正常保存。
    """

    @pytest.mark.asyncio
    async def test_create_memory_limit_negative_rejected_and_not_saved(self):
        """POST memory_limit=-1 → 422，save_summary_config 不被调用。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("app.api.summary_jobs.config_manager") as mock_cm:
                response = await client.post(
                    "/api/summary/jobs", json={"memory_limit": -1}
                )
                assert response.status_code == 422
                mock_cm.save_summary_config.assert_not_called()
                _assert_422_detail_contract(response, "memory_limit")

    @pytest.mark.asyncio
    async def test_create_related_limit_too_large_rejected(self):
        """POST related_limit=1001 → 422（le=1000）。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("app.api.summary_jobs.config_manager") as mock_cm:
                response = await client.post(
                    "/api/summary/jobs", json={"related_limit": 1001}
                )
                assert response.status_code == 422
                mock_cm.save_summary_config.assert_not_called()
                _assert_422_detail_contract(response, "related_limit")

    @pytest.mark.asyncio
    async def test_create_boundary_values_accepted_and_saved(self):
        """POST memory_limit=0, related_limit=1000（边界）→ 200 且保存调用。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.summary_scheduler") as mock_scheduler,
            ):
                mock_scheduler.apply_config_after_save = AsyncMock()
                response = await client.post(
                    "/api/summary/jobs",
                    json={"memory_limit": 0, "related_limit": 1000},
                )
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                mock_cm.save_summary_config.assert_called_once()
                mock_cm.reload_config.assert_called_once()
                mock_scheduler.apply_config_after_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_memory_limit_negative_rejected(self):
        """PUT /api/summary/jobs/foo memory_limit=-1 → 422（update 路径同样收口）。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("app.api.summary_jobs.config_manager") as mock_cm:
                response = await client.put(
                    "/api/summary/jobs/foo", json={"memory_limit": -1}
                )
                assert response.status_code == 422
                mock_cm.save_summary_config.assert_not_called()
                _assert_422_detail_contract(response, "memory_limit")

    @pytest.mark.asyncio
    async def test_update_related_limit_too_large_rejected(self):
        """PUT /api/summary/jobs/foo related_limit=1001 → 422（update 路径同样收口）。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("app.api.summary_jobs.config_manager") as mock_cm:
                response = await client.put(
                    "/api/summary/jobs/foo", json={"related_limit": 1001}
                )
                assert response.status_code == 422
                mock_cm.save_summary_config.assert_not_called()
                _assert_422_detail_contract(response, "related_limit")


class TestClearMemoryApi:
    @pytest.mark.asyncio
    async def test_requires_confirm(self):
        """C4/S3：无 confirm → 422，不删除。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("app.api.summary_jobs.config_manager") as mock_cm:
                mock_cm.get_summary_configs.return_value = [{"name": "daily"}]
                response = await client.post(
                    "/api/summary/jobs/daily/clear-memory", json={}
                )
                assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_clear_memory_success(self):
        """S3：confirm=true → success + deleted_records，委托 MemoryService。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.memory_service") as mock_memory,
            ):
                mock_cm.get_summary_configs.return_value = [{"name": "daily"}]
                mock_memory.clear_task.return_value = 42
                response = await client.post(
                    "/api/summary/jobs/daily/clear-memory", json={"confirm": True}
                )
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                assert data["deleted_records"] == 42
                mock_memory.clear_task.assert_called_once_with(
                    "summary", "summary-daily"
                )

    @pytest.mark.asyncio
    async def test_job_not_found_404(self):
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("app.api.summary_jobs.config_manager") as mock_cm:
                mock_cm.get_summary_configs.return_value = []
                response = await client.post(
                    "/api/summary/jobs/nonexist/clear-memory", json={"confirm": True}
                )
                assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_clear_failure_returns_error(self):
        """S5：清空失败返回错误响应（不影响任务配置）。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.memory_service") as mock_memory,
            ):
                mock_cm.get_summary_configs.return_value = [{"name": "daily"}]
                mock_memory.clear_task.side_effect = RuntimeError("db down")
                response = await client.post(
                    "/api/summary/jobs/daily/clear-memory", json={"confirm": True}
                )
                assert response.status_code == 500
                assert response.json()["detail"] == "清空任务记忆失败，请稍后重试"


class TestRenameMemoryLinkage:
    @pytest.mark.asyncio
    async def test_rename_migrates_memory(self):
        """S1：改名 PUT → MemoryService.rename_task 联动（记忆跟随）。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.memory_service") as mock_memory,
                patch("app.api.summary_jobs.summary_scheduler") as mock_sched,
            ):
                mock_cm.get_summary_configs.return_value = [{"name": "daily"}]
                mock_sched.apply_config_after_save = AsyncMock()
                response = await client.put(
                    "/api/summary/jobs/daily",
                    json={"name": "daily2"},
                )
                assert response.status_code == 200
                mock_memory.rename_task.assert_called_once_with(
                    "summary", "summary-daily", "summary-daily2"
                )

    @pytest.mark.asyncio
    async def test_rename_memory_failure_returns_500(self):
        """记忆迁移异常 → 500（不得假成功：记忆未跟随改名需用户重试）。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.memory_service") as mock_memory,
                patch("app.api.summary_jobs.summary_scheduler") as mock_sched,
            ):
                mock_cm.get_summary_configs.return_value = [{"name": "daily"}]
                mock_sched.apply_config_after_save = AsyncMock()
                mock_memory.rename_task.side_effect = RuntimeError("db down")
                response = await client.put(
                    "/api/summary/jobs/daily",
                    json={"name": "daily2"},
                )
                assert response.status_code == 500
                assert (
                    response.json()["detail"]
                    == "任务改名失败：记忆迁移异常，请稍后重试"
                )
                # 顺序前置证据：rename 失败时旧名配置仍在，任务仍可寻址重试
                mock_cm.rename_notification_type.assert_not_called()
                mock_cm.save_summary_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_rename_migrates_memory_before_config_rename(self):
        """成功路径顺序：rename_task 先于 rename_notification_type（旧名保持可寻址）。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.memory_service") as mock_memory,
                patch("app.api.summary_jobs.summary_scheduler") as mock_sched,
            ):
                mock_cm.get_summary_configs.return_value = [{"name": "daily"}]
                mock_sched.apply_config_after_save = AsyncMock()
                mock_cm.attach_mock(mock_memory.rename_task, "rename_task")

                response = await client.put(
                    "/api/summary/jobs/daily",
                    json={"name": "daily2"},
                )
                assert response.status_code == 200

                call_names = [c[0] for c in mock_cm.mock_calls]
                assert "rename_task" in call_names
                assert "rename_notification_type" in call_names
                assert call_names.index("rename_task") < call_names.index(
                    "rename_notification_type"
                )
                # 配置改名仍在 save_summary_config 之前
                assert call_names.index("rename_notification_type") < call_names.index(
                    "save_summary_config"
                )

    @pytest.mark.asyncio
    async def test_rename_same_name_no_migration(self):
        """改名与原名相同 → 不触发记忆迁移。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.memory_service") as mock_memory,
                patch("app.api.summary_jobs.summary_scheduler") as mock_sched,
            ):
                mock_cm.get_summary_configs.return_value = [{"name": "daily"}]
                mock_sched.apply_config_after_save = AsyncMock()
                await client.put(
                    "/api/summary/jobs/daily",
                    json={"cron": "0 8 * * *"},
                )
                mock_memory.rename_task.assert_not_called()


class TestSummaryJobRuntimeSyncDegradation:
    """持久层成功后，运行时同步（reload/apply）失败只降级为日志，接口仍 200。

    持久层（ini/SQLite）已经成功，内存重载/调度器同步失败不应误报 500——
    否则用户会误以为操作失败而重试，而重试寻址已失效（配置已删/已改名）。
    """

    @pytest.mark.asyncio
    async def test_delete_returns_200_when_reload_fails(self):
        """DELETE：reload_config 抛异常 → 仍 200，配置已删除。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.summary_scheduler") as mock_scheduler,
                patch("app.api.summary_jobs.memory_service"),
            ):
                mock_scheduler.apply_config_after_save = AsyncMock()
                mock_cm.reload_config.side_effect = RuntimeError("reload boom")
                response = await client.delete("/api/summary/jobs/Dad%20Summary")
                assert response.status_code == 200
                assert response.json()["status"] == "success"
                mock_cm.delete_summary_config.assert_called_once_with("Dad Summary")

    @pytest.mark.asyncio
    async def test_delete_returns_200_when_apply_fails(self):
        """DELETE：apply_config_after_save 抛异常 → 仍 200。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.summary_scheduler") as mock_scheduler,
                patch("app.api.summary_jobs.memory_service"),
            ):
                mock_scheduler.apply_config_after_save = AsyncMock(
                    side_effect=RuntimeError("apply boom")
                )
                response = await client.delete("/api/summary/jobs/Dad%20Summary")
                assert response.status_code == 200
                assert response.json()["status"] == "success"
                mock_cm.delete_summary_config.assert_called_once_with("Dad Summary")

    @pytest.mark.asyncio
    async def test_update_returns_200_when_reload_fails(self):
        """PUT：reload_config 抛异常 → 仍 200，配置已保存。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.summary_scheduler") as mock_scheduler,
            ):
                mock_cm.get_summary_configs.return_value = [{"name": "daily"}]
                mock_scheduler.apply_config_after_save = AsyncMock()
                mock_cm.reload_config.side_effect = RuntimeError("reload boom")
                response = await client.put(
                    "/api/summary/jobs/daily", json={"cron": "0 8 * * *"}
                )
                assert response.status_code == 200
                assert response.json()["status"] == "success"
                mock_cm.save_summary_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_returns_200_when_apply_fails(self):
        """PUT：apply_config_after_save 抛异常 → 仍 200。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.summary_scheduler") as mock_scheduler,
            ):
                mock_cm.get_summary_configs.return_value = [{"name": "daily"}]
                mock_scheduler.apply_config_after_save = AsyncMock(
                    side_effect=RuntimeError("apply boom")
                )
                response = await client.put(
                    "/api/summary/jobs/daily", json={"cron": "0 8 * * *"}
                )
                assert response.status_code == 200
                assert response.json()["status"] == "success"
                mock_cm.save_summary_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_returns_200_when_reload_fails(self):
        """POST：reload_config 抛异常 → 仍 200，配置已保存。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.summary_scheduler") as mock_scheduler,
            ):
                mock_scheduler.apply_config_after_save = AsyncMock()
                mock_cm.reload_config.side_effect = RuntimeError("reload boom")
                response = await client.post(
                    "/api/summary/jobs", json={"name": "New Job"}
                )
                assert response.status_code == 200
                assert response.json()["status"] == "success"
                mock_cm.save_summary_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_returns_200_when_apply_fails(self):
        """POST：apply_config_after_save 抛异常 → 仍 200。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch("app.api.summary_jobs.summary_scheduler") as mock_scheduler,
            ):
                mock_scheduler.apply_config_after_save = AsyncMock(
                    side_effect=RuntimeError("apply boom")
                )
                response = await client.post(
                    "/api/summary/jobs", json={"name": "New Job"}
                )
                assert response.status_code == 200
                assert response.json()["status"] == "success"
                mock_cm.save_summary_config.assert_called_once()


class TestMemoryStatsApi:
    """S10'/S11'：memory-stats 返回记忆总量与注入估算（绝对量，无百分比）。"""

    @pytest.mark.asyncio
    async def test_stats_with_no_memory(self):
        """空记忆：count=0、avg=0、注入估算=0（related_limit 只算配置上限）。"""
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch(
                    "app.api.summary_jobs.database_manager.memory.get_recent",
                    return_value=[],
                ) as mock_recent,
            ):
                mock_cm.get_summary_configs.return_value = [
                    {"name": "daily", "memory_limit": "5", "related_limit": "3"}
                ]
                response = await client.get("/api/summary/jobs/daily/memory-stats")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total_count"] == 0
        assert data["total_chars"] == 0
        assert data["avg_chars"] == 0
        assert data["memory_limit"] == 5
        assert data["related_limit"] == 3
        assert data["injected_estimate_tokens"] == 0
        mock_recent.assert_called_once_with("summary", "summary-daily", limit=1000)

    @pytest.mark.asyncio
    async def test_stats_with_accumulated_memory(self):
        """有记忆：count/chars 正确；估算 = (min(count,limit)+related)×avg×0.7。"""
        from httpx import ASGITransport, AsyncClient

        from app.models.memory import MemoryEntry

        app = _make_summary_app()
        entries = [
            MemoryEntry(run_id="r-1", summary="芙莉莲S1E10" * 10),  # 100 字
            MemoryEntry(run_id="r-2", summary="鬼灭S3E5" * 10),  # 100 字
            MemoryEntry(run_id="r-3", summary="葬送S2E1" * 10),  # 100 字
        ]

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch(
                    "app.api.summary_jobs.database_manager.memory.get_recent",
                    return_value=entries,
                ),
            ):
                mock_cm.get_summary_configs.return_value = [
                    {"name": "daily", "memory_limit": "2", "related_limit": "1"}
                ]
                response = await client.get("/api/summary/jobs/daily/memory-stats")

        data = response.json()["data"]
        assert data["total_count"] == 3
        assert data["total_chars"] == 200  # 80 + 60 + 60
        assert data["avg_chars"] == 67  # round(200/3)
        # (min(3,2)+1) × 67 × 0.7 = 3 × 46.9 = 140.7 → round
        assert data["injected_estimate_tokens"] == 141

    @pytest.mark.asyncio
    async def test_stats_excludes_placeholder_rows(self):
        """T2：摘要失败占位行（summary=""）不计入 total_count/chars/avg 与注入估算。"""
        from httpx import ASGITransport, AsyncClient

        from app.models.memory import MemoryEntry

        app = _make_summary_app()
        entries = [
            MemoryEntry(run_id="r-ok", summary="有效摘要" * 10),  # 40 字
            MemoryEntry(
                run_id="r-placeholder", summary="", outcome="summary_failed"
            ),  # 占位行
        ]

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch(
                    "app.api.summary_jobs.database_manager.memory.get_recent",
                    return_value=entries,
                ),
            ):
                mock_cm.get_summary_configs.return_value = [
                    {"name": "daily", "memory_limit": "5", "related_limit": "0"}
                ]
                response = await client.get("/api/summary/jobs/daily/memory-stats")

        data = response.json()["data"]
        assert data["total_count"] == 1  # 只统计有效行
        assert data["total_chars"] == 40
        assert data["avg_chars"] == 40
        # (min(1,5)+0) × 40 × 0.7 = 28
        assert data["injected_estimate_tokens"] == 28

    @pytest.mark.asyncio
    async def test_stats_missing_job_404(self):
        from httpx import ASGITransport, AsyncClient

        app = _make_summary_app()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("app.api.summary_jobs.config_manager") as mock_cm:
                mock_cm.get_summary_configs.return_value = []
                response = await client.get("/api/summary/jobs/nope/memory-stats")
                assert response.status_code == 404


class TestMemoryStatsEstimateM11:
    """M11：注入估算需体现 related 独立生效（不因 memory_limit=0 而遗漏 related）。"""

    @pytest.mark.asyncio
    async def test_stats_includes_related_when_memory_zero(self):
        from httpx import ASGITransport, AsyncClient

        from app.models.memory import MemoryEntry

        app = _make_summary_app()
        entries = [MemoryEntry(run_id="r-1", summary="芙莉莲S1E10" * 10)]  # 80 字

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("app.api.summary_jobs.config_manager") as mock_cm,
                patch(
                    "app.api.summary_jobs.database_manager.memory.get_recent",
                    return_value=entries,
                ),
            ):
                mock_cm.get_summary_configs.return_value = [
                    {"name": "daily", "memory_limit": "0", "related_limit": "2"}
                ]
                response = await client.get("/api/summary/jobs/daily/memory-stats")

        data = response.json()["data"]
        # related 独立生效：估算 = (min(1,0)=0 + 2) × 80 × 0.7 = 112
        assert data["injected_estimate_tokens"] == 112
