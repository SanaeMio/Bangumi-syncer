"""
Summary API 模型验证测试与端点集成测试。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
                assert "Hello! How can I help you?" in data["message"]
                assert data["model"] == "gpt-4o-mini"
                assert data["latency_ms"] is not None

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
                mock_cm.reload_config.assert_called_once()
                mock_scheduler.apply_config_after_save.assert_awaited_once()


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
