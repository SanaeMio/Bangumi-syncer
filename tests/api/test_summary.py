"""
Summary API models validation tests and endpoint integration tests.
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
    """Tests for LLMConfigResponse model."""

    def test_default_values(self):
        """Verify default field values on construction."""
        model = LLMConfigResponse()
        assert model.api_base == "https://api.openai.com/v1"
        assert model.api_key == ""
        assert model.model == "gpt-4o-mini"
        assert model.max_tokens == 2000
        assert model.temperature == 0.7
        assert model.timeout == 60

    def test_override_values(self):
        """Verify explicit field values are accepted."""
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
        """Verify model_dump() serializes correctly."""
        model = LLMConfigResponse(api_key="***hidden")
        data = model.model_dump()
        assert data["api_key"] == "***hidden"


# ========== LLMConfigUpdate ==========


class TestLLMConfigUpdate:
    """Tests for LLMConfigUpdate model."""

    def test_all_none_is_valid(self):
        """Verify an empty partial update is valid."""
        model = LLMConfigUpdate()
        assert model.api_base is None
        assert model.api_key is None
        assert model.model is None
        assert model.max_tokens is None
        assert model.temperature is None
        assert model.timeout is None

    def test_single_field_update(self):
        """Verify setting only one field (partial update)."""
        model = LLMConfigUpdate(model="gpt-4")
        assert model.model == "gpt-4"
        assert model.api_base is None
        assert model.api_key is None

    def test_multiple_fields_update(self):
        """Verify setting multiple fields."""
        model = LLMConfigUpdate(
            api_base="https://new.api/v1",
            temperature=0.1,
        )
        assert model.api_base == "https://new.api/v1"
        assert model.temperature == 0.1
        assert model.max_tokens is None

    def test_model_dump_excludes_none(self):
        """Verify model_dump(exclude_none=True) omits None fields."""
        model = LLMConfigUpdate(model="gpt-4")
        data = model.model_dump(exclude_none=True)
        assert "model" in data
        assert data["model"] == "gpt-4"
        assert "api_base" not in data


# ========== LLMTestResponse ==========


class TestLLMTestResponse:
    """Tests for LLMTestResponse model."""

    def test_minimal_creation(self):
        """Verify required fields only."""
        model = LLMTestResponse(success=True, message="OK")
        assert model.success is True
        assert model.message == "OK"
        assert model.model is None
        assert model.latency_ms is None

    def test_full_creation(self):
        """Verify all fields."""
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
    """Tests for SummaryJobCreate model."""

    def test_default_values(self):
        """Verify default field values."""
        model = SummaryJobCreate()
        assert model.name == "New Summary"
        assert model.cron == "0 21 * * *"
        assert model.lookback_days == 1
        assert model.user_name == ""
        assert model.system_prompt == ""
        assert model.max_records == 200
        assert model.enabled is True

    def test_all_fields_set(self):
        """Verify all fields can be set explicitly."""
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
    """Tests for SummaryJobUpdate model."""

    def test_all_none_is_valid(self):
        """Verify an empty partial update is valid."""
        model = SummaryJobUpdate()
        assert model.name is None
        assert model.cron is None
        assert model.lookback_days is None
        assert model.user_name is None
        assert model.system_prompt is None
        assert model.max_records is None
        assert model.enabled is None

    def test_single_field_update(self):
        """Verify setting only one field."""
        model = SummaryJobUpdate(enabled=False)
        assert model.enabled is False
        assert model.name is None
        assert model.cron is None

    def test_multiple_fields_update(self):
        """Verify setting multiple fields."""
        model = SummaryJobUpdate(
            name="Updated Job",
            lookback_days=7,
        )
        assert model.name == "Updated Job"
        assert model.lookback_days == 7
        assert model.cron is None

    def test_model_dump_excludes_none(self):
        """Verify model_dump(exclude_none=True) omits None fields."""
        model = SummaryJobUpdate(enabled=False)
        data = model.model_dump(exclude_none=True)
        assert "enabled" in data
        assert "name" not in data


# ========== SummaryJobResponse ==========


class TestSummaryJobResponse:
    """Tests for SummaryJobResponse model."""

    def test_creation_with_all_fields(self):
        """Verify model creation with all fields."""
        model = SummaryJobResponse(
            id=1,
            name="Test Job",
            cron="0 21 * * *",
            lookback_days=1,
            user_name="dad",
            system_prompt="Be concise.",
            max_records=200,
            enabled=True,
            notification_type="watching_summary_dad",
        )
        assert model.id == 1
        assert model.name == "Test Job"
        assert model.notification_type == "watching_summary_dad"

    def test_notification_type_empty_by_default(self):
        """Verify notification_type defaults to empty string."""
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

    # ========== from_config_dict tests ==========

    def test_from_config_dict_empty_user_name(self):
        """notification_type = 'watching_summary' when user_name is empty."""
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
        assert model.notification_type == "watching_summary"
        assert model.id == 1
        assert model.name == "Test"

    def test_from_config_dict_with_user_name(self):
        """notification_type = 'watching_summary_dad' when user_name = 'dad'."""
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
        assert model.notification_type == "watching_summary_dad"
        assert model.id == 2
        assert model.name == "Dad's Summary"
        assert model.user_name == "dad"

    def test_from_config_dict_missing_optional_keys(self):
        """Verify defaults are applied when keys are missing from dict."""
        data = {
            "id": 3,
            "name": "Minimal Job",
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.id == 3
        assert model.name == "Minimal Job"
        assert model.cron == "0 21 * * *"
        assert model.lookback_days == 1
        assert model.user_name == ""
        assert model.system_prompt == ""
        assert model.max_records == 200
        assert model.enabled is True
        assert model.notification_type == "watching_summary"

    def test_from_config_dict_user_name_none(self):
        """notification_type = 'watching_summary' when user_name is None."""
        data = {
            "id": 4,
            "name": "None User",
            "user_name": None,
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.user_name == ""
        assert model.notification_type == "watching_summary"

    def test_from_config_dict_disabled_job(self):
        """Verify enabled=False is preserved."""
        data = {
            "id": 5,
            "name": "Disabled Job",
            "enabled": False,
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.enabled is False

    def test_from_config_dict_enabled_int_zero(self):
        """Verify enabled=0 is treated as False."""
        data = {
            "id": 6,
            "name": "Int Zero Job",
            "enabled": 0,
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.enabled is False

    def test_from_config_dict_enabled_int_one(self):
        """Verify enabled=1 is treated as True."""
        data = {
            "id": 7,
            "name": "Int One Job",
            "enabled": 1,
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.enabled is True

    def test_from_config_dict_with_extra_keys(self):
        """Verify extra keys in dict are ignored."""
        data = {
            "id": 8,
            "name": "Extra Keys",
            "unknown_field": "should be ignored",
            "another_extra": 42,
        }
        model = SummaryJobResponse.from_config_dict(data)
        assert model.id == 8
        assert model.name == "Extra Keys"


# ========== SummaryJobTestResponse ==========


class TestSummaryJobTestResponse:
    """Tests for SummaryJobTestResponse model."""

    def test_minimal_creation(self):
        """Verify required fields only, defaults on others."""
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
        """Verify error fields are populated."""
        model = SummaryJobTestResponse(
            success=False,
            job_name="Failing Job",
            error_message="LLM timeout after 60s",
        )
        assert model.success is False
        assert model.error_message == "LLM timeout after 60s"

    def test_success_response_with_tokens(self):
        """Verify token counts and latency populated on success."""
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


# ========== API Endpoint Tests ==========


class TestGetLLMConfig:
    """Tests for GET /api/summary/llm endpoint."""

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
        """GET /api/summary/llm should return config with masked api_key."""
        from httpx import ASGITransport, AsyncClient

        app = self._create_test_app()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/summary/llm")
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
        """GET /api/summary/llm should mask short api_key as '***'."""
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
                response = await client.get("/api/summary/llm")
                assert response.json()["api_key"] == "***"

    @pytest.mark.asyncio
    async def test_handles_empty_api_key(self):
        """GET /api/summary/llm should handle empty api_key gracefully."""
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
                response = await client.get("/api/summary/llm")
                assert response.json()["api_key"] == ""

    def _create_test_app(self):
        """Create a FastAPI test app with auth override."""
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
    """Tests for PUT /api/summary/llm endpoint."""

    @pytest.mark.asyncio
    async def test_updates_config_and_calls_reload(self):
        """PUT /api/summary/llm should update config and reload."""
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
                response = await client.put("/api/summary/llm", json=payload)
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                assert mock_cm.set_config.call_count == 3
                mock_cm.reload_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_update_is_accepted(self):
        """PUT /api/summary/llm with empty body should still succeed."""
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
                response = await client.put("/api/summary/llm", json={})
                assert response.status_code == 200
                # No fields to update, so set_config should not be called
                mock_cm.set_config.assert_not_called()
                mock_cm.reload_config.assert_called_once()


class TestTestLLMConnection:
    """Tests for POST /api/summary/llm/test endpoint."""

    @pytest.mark.asyncio
    async def test_successful_llm_connection(self):
        """POST /api/summary/llm/test should return success on valid LLM."""
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
                response = await client.post("/api/summary/llm/test")
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert "Hello! How can I help you?" in data["message"]
                assert data["model"] == "gpt-4o-mini"
                assert data["latency_ms"] is not None

    @pytest.mark.asyncio
    async def test_llm_connection_failure(self):
        """POST /api/summary/llm/test should return failure on LLM error."""
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
                response = await client.post("/api/summary/llm/test")
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is False
                assert "Connection refused" in data["message"]


class TestGetLLMStats:
    """Tests for GET /api/summary/llm/stats endpoint."""

    @pytest.mark.asyncio
    async def test_returns_aggregate_stats(self):
        """GET /api/summary/llm/stats should return usage statistics."""
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
            mock_db.llm_usage.get_stats.return_value = {
                "total_calls": 10,
                "total_tokens": 5000,
                "error_count": 1,
                "avg_latency_ms": 350,
            }
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/summary/llm/stats")
                assert response.status_code == 200
                data = response.json()
                assert data["total_calls"] == 10
                assert data["total_tokens"] == 5000
                assert data["error_count"] == 1
                assert data["avg_latency_ms"] == 350

    @pytest.mark.asyncio
    async def test_passes_scope_and_days_params(self):
        """GET /api/summary/llm/stats should forward scope and days params."""
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
            mock_db.llm_usage.get_stats.return_value = {
                "total_calls": 0,
                "total_tokens": 0,
                "error_count": 0,
                "avg_latency_ms": 0,
                "by_model": [],
                "by_job": [],
                "daily": [],
            }
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/summary/llm/stats?scope=detailed&days=7"
                )
                assert response.status_code == 200
                mock_db.llm_usage.get_stats.assert_called_once_with(
                    scope="detailed", days=7
                )


class TestListSummaryJobs:
    """Tests for GET /api/summary/jobs endpoint."""

    @pytest.mark.asyncio
    async def test_returns_list_of_jobs(self):
        """GET /api/summary/jobs should return serialized job configs."""
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
                assert data["data"][0]["id"] == 1
                assert data["data"][1]["notification_type"] == "watching_summary_dad"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_configs(self):
        """GET /api/summary/jobs should return empty list when no configs exist."""
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
    """Tests for POST /api/summary/jobs endpoint."""

    @pytest.mark.asyncio
    async def test_creates_job_and_calls_scheduler(self):
        """POST /api/summary/jobs should save config and apply scheduler."""
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
    """Tests for PUT /api/summary/jobs/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_updates_existing_job(self):
        """PUT /api/summary/jobs/{id} should update config with job id."""
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
                # Verify the updates dict includes the job_id
                call_args = mock_cm.save_summary_config.call_args[0][0]
                assert call_args["id"] == 3
                assert call_args["name"] == "Updated Job"
                mock_cm.reload_config.assert_called_once()
                mock_scheduler.apply_config_after_save.assert_awaited_once()


class TestDeleteSummaryJob:
    """Tests for DELETE /api/summary/jobs/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_deletes_job_and_calls_scheduler(self):
        """DELETE /api/summary/jobs/{id} should delete config and apply scheduler."""
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
                response = await client.delete("/api/summary/jobs/2")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                mock_cm.delete_summary_config.assert_called_once_with(2)
                mock_cm.reload_config.assert_called_once()
                mock_scheduler.apply_config_after_save.assert_awaited_once()


class TestTestSummaryJob:
    """Tests for POST /api/summary/jobs/{id}/test endpoint."""

    @pytest.mark.asyncio
    async def test_returns_test_result(self):
        """POST /api/summary/jobs/{id}/test should run generate and return result."""
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
                response = await client.post("/api/summary/jobs/1/test")
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
        """POST /api/summary/jobs/{id}/test should return 404 for missing job."""
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
                response = await client.post("/api/summary/jobs/99/test")
                assert response.status_code == 404


class TestTriggerSummaryJob:
    """Tests for POST /api/summary/jobs/{id}/trigger endpoint."""

    @pytest.mark.asyncio
    async def test_triggers_job_execution(self):
        """POST /api/summary/jobs/{id}/trigger should execute the job."""
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
                response = await client.post("/api/summary/jobs/1/trigger")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                assert "Trigger Job" in data["message"]
                mock_service.execute_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent_job(self):
        """POST /api/summary/jobs/{id}/trigger should return 404 for missing job."""
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
                response = await client.post("/api/summary/jobs/1/trigger")
                assert response.status_code == 404


class TestLLMUsageStatsResponse:
    """Tests for LLMUsageStatsResponse model."""

    def test_all_defaults(self):
        """Verify all fields have correct defaults."""
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
