"""
FastAPI 主应用测试
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


class TestMainApp:
    """主应用测试"""

    def test_app_creation(self):
        """测试应用创建"""
        # 避免在导入时触发启动事件
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.trakt_scheduler"):
                        from app.main import app

                        assert app is not None

    def test_app_title(self):
        """测试应用标题"""
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.trakt_scheduler"):
                        from app.main import app

                        assert app.title is not None

    def test_app_version(self):
        """测试应用版本"""
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.trakt_scheduler"):
                        from app.main import app

                        assert app.version is not None

    def test_app_has_routes(self):
        """测试应用有路由"""
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.trakt_scheduler"):
                        from app.main import app

                        # 检查应用有路由
                        assert len(app.routes) > 0, "No routes registered"

    def test_app_description(self):
        """测试应用描述"""
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.trakt_scheduler"):
                        from app.main import app

                        assert app.description is not None


@contextmanager
def _main_lifespan_mocks(**replace: object):
    """为 TestClient 进入/退出触发的 startup/shutdown 打桩；replace 为 patch 目标 -> patch 的 kwargs 字典。"""
    from contextlib import ExitStack

    defaults: dict[str, dict] = {
        "app.main.startup_info.print_info": {},
        "app.main.startup_info.print_separator": {},
        "app.main.startup_info.print_success": {},
        "app.main.startup_info.print_error": {},
        "app.main.startup_info.print_startup_complete": {},
        "app.main.config_manager.get_bangumi_configs": {"return_value": {}},
        "app.main.config_manager.get_user_mappings": {"return_value": {}},
        "app.main.mapping_service.get_all_mappings": {"return_value": {}},
        "app.main.ensure_feiniu_startup_watermark": {},
        "app.main.config_manager.get_scheduler_config": {
            "return_value": {"startup_delay": 0}
        },
        "app.main.trakt_scheduler.start": {"new": AsyncMock(return_value=True)},
        "app.main.feiniu_scheduler.start": {"new": AsyncMock(return_value=True)},
        "app.main.trakt_scheduler.stop": {"new": AsyncMock()},
        "app.main.feiniu_scheduler.stop": {"new": AsyncMock()},
        "asyncio.sleep": {"new": AsyncMock()},
    }
    merged = {**defaults, **replace}
    with ExitStack() as stack:
        for path, kw in merged.items():
            stack.enter_context(patch(path, **kw))
        yield


def test_main_lifespan_startup_and_shutdown():
    from app.main import app

    with _main_lifespan_mocks():
        with TestClient(app) as client:
            r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "healthy"


def test_main_startup_config_load_failure_logged():
    from app.main import app

    with _main_lifespan_mocks(
        **{
            "app.main.config_manager.get_bangumi_configs": {
                "side_effect": RuntimeError("cfg fail")
            }
        }
    ):
        with TestClient(app):
            pass


def test_main_startup_feiniu_watermark_failure_only_logs():
    from app.main import app

    with _main_lifespan_mocks(
        **{
            "app.main.ensure_feiniu_startup_watermark": {
                "side_effect": RuntimeError("watermark")
            }
        }
    ):
        with TestClient(app):
            pass


def test_main_startup_delayed_scheduler_trakt_start_fails():
    from app.main import app

    with _main_lifespan_mocks(
        **{
            "app.main.trakt_scheduler.start": {"new": AsyncMock(return_value=False)},
            "app.main.feiniu_scheduler.start": {"new": AsyncMock(return_value=True)},
        }
    ):
        with TestClient(app):
            pass


def test_main_startup_delayed_scheduler_feiniu_start_fails():
    from app.main import app

    with _main_lifespan_mocks(
        **{"app.main.feiniu_scheduler.start": {"new": AsyncMock(return_value=False)}}
    ):
        with TestClient(app):
            pass


@pytest.mark.filterwarnings("ignore:coroutine .*:RuntimeWarning")
def test_main_startup_create_task_failure_logged():
    from app.main import app

    with _main_lifespan_mocks(
        **{"asyncio.create_task": {"side_effect": RuntimeError("no task")}}
    ):
        with TestClient(app):
            pass


def test_main_shutdown_trakt_stop_failure_logged():
    from app.main import app

    with _main_lifespan_mocks(
        **{
            "app.main.trakt_scheduler.stop": {
                "new": AsyncMock(side_effect=RuntimeError("stop trakt"))
            },
        }
    ):
        with TestClient(app):
            pass


def test_main_shutdown_feiniu_stop_failure_logged():
    from app.main import app

    with _main_lifespan_mocks(
        **{
            "app.main.feiniu_scheduler.stop": {
                "new": AsyncMock(side_effect=RuntimeError("stop feiniu"))
            },
        }
    ):
        with TestClient(app):
            pass
