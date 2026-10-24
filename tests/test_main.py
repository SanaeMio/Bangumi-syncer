"""
FastAPI 主应用测试
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

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
                    with patch("app.main.register_schedulers"):
                        from app.main import app

                        assert app is not None

    def test_app_title(self):
        """测试应用标题"""
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.register_schedulers"):
                        from app.main import app

                        assert app.title is not None

    def test_app_version(self):
        """测试应用版本"""
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.register_schedulers"):
                        from app.main import app

                        assert app.version is not None

    def test_app_has_routes(self):
        """测试应用有路由"""
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.register_schedulers"):
                        from app.main import app

                        # 检查应用有路由
                        assert len(app.routes) > 0, "No routes registered"

    def test_app_description(self):
        """测试应用描述"""
        with patch("app.main.startup_info"):
            with patch("app.main.config_manager"):
                with patch("app.main.mapping_service"):
                    with patch("app.main.register_schedulers"):
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
        "app.main.mapping_service.get_all_mappings": {"return_value": {}},
        "app.main.ensure_feiniu_startup_watermark": {},
        "app.main.database_manager.cleanup_pending_sync_queue": {},
        "app.main.config_manager.get_scheduler_config": {
            "return_value": {"startup_delay": 0}
        },
        "app.main.register_schedulers": {},
        "app.main.scheduler_registry.start_all": {"new": AsyncMock()},
        "app.main.scheduler_registry.stop_all": {"new": AsyncMock()},
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


def test_main_startup_delayed_scheduler_start_fails():
    """start_all 抛异常时仅记录日志，不影响应用启动"""
    from app.main import app

    with _main_lifespan_mocks(
        **{
            "app.main.scheduler_registry.start_all": {
                "new": AsyncMock(side_effect=RuntimeError("start fail"))
            },
        }
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


def test_main_shutdown_stop_all_failure_logged():
    """stop_all 抛异常时仅记录日志，不影响应用关闭"""
    from app.main import app

    with _main_lifespan_mocks(
        **{
            "app.main.scheduler_registry.stop_all": {
                "new": AsyncMock(side_effect=RuntimeError("stop fail"))
            },
        }
    ):
        with TestClient(app):
            pass


def test_main_startup_retention_defaults_to_no_cleanup():
    """缺省 sync_records_retention_days 为 0，启动时不清理历史记录。"""
    from app.main import app

    mock_get_config = MagicMock(return_value=0)
    mock_cleanup = MagicMock(return_value=0)

    with _main_lifespan_mocks(
        **{
            "app.main.config_manager.get_config": {"new": mock_get_config},
            "app.main.database_manager.cleanup_old_records": {"new": mock_cleanup},
        }
    ):
        with TestClient(app):
            pass

    mock_get_config.assert_any_call("dev", "sync_records_retention_days", 0)
    mock_cleanup.assert_called_once_with(0)


class TestCspHeader:
    """CSP 头构建：图片放行 https 源，其余指令保持严格。"""

    def test_img_src_allows_https_images(self):
        """发行说明与公告正文的图片可托管在任意域名，img-src 放行 https 源。"""
        from app.main import _build_csp_header

        header = _build_csp_header()
        assert "img-src 'self' data: https:; " in header

    def test_other_directives_stay_restrictive(self):
        """脚本、连接与嵌入限制不随图片策略放宽。"""
        from app.main import _build_csp_header

        header = _build_csp_header()
        assert "script-src 'self' 'unsafe-inline'; " in header
        assert "connect-src 'self'; " in header
        assert "frame-ancestors 'none'; " in header
        assert "default-src 'self'; " in header
        assert header.endswith("base-uri 'self'")

    @pytest.mark.parametrize(
        "value",
        [
            "https://a.com; default-src *",
            "https://user:pass@a.com",
            "javascript:alert(1)",
        ],
    )
    def test_header_is_independent_of_config(self, value):
        """头内容不拼接配置值，配置内容无法改写策略。"""
        from app.main import _build_csp_header

        with patch("app.main.config_manager.get", return_value=value):
            header = _build_csp_header()
        assert header == _build_csp_header()
        for token in ("default-src *", "user:pass", "alert(1)", "a.com"):
            assert token not in header
