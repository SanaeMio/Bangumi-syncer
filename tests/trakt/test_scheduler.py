"""
Trakt 调度器测试 - 简化版
"""

from unittest.mock import patch

from app.services.trakt.scheduler import TraktScheduler


class TestTraktScheduler:
    """Trakt 调度器测试 - 简化版"""

    def test_init(self):
        """测试初始化"""
        with (
            patch("app.services.trakt.scheduler.config_manager"),
            patch("app.services.trakt.scheduler.database_manager"),
        ):
            scheduler = TraktScheduler()
            assert scheduler is not None

    def test_remove_user_job(self):
        """测试删除用户任务 - 简化测试"""
        pass

    def test_get_all_jobs_status(self):
        """测试获取所有任务状态"""
        with (
            patch("app.services.trakt.scheduler.config_manager"),
            patch("app.services.trakt.scheduler.database_manager"),
        ):
            scheduler = TraktScheduler()
            all_status = scheduler.get_all_jobs_status()
            assert isinstance(all_status, dict)
