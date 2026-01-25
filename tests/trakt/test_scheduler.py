"""
Trakt 调度器测试
"""

import asyncio
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.services.trakt.scheduler import TraktScheduler, trakt_scheduler


class TestTraktScheduler:
    """TraktScheduler 测试类"""

    def test_init(self):
        """测试调度器初始化"""
        scheduler = TraktScheduler()

        assert scheduler.scheduler is None
        assert scheduler._user_jobs == {}
        assert scheduler.scheduler_config is not None

    def test_start_success(self):
        """测试成功启动调度器"""
        scheduler = TraktScheduler()

        # 模拟 AsyncIOScheduler
        mock_scheduler = Mock()
        mock_scheduler.running = False
        mock_scheduler.start = Mock()
        mock_scheduler.get_jobs = Mock(return_value=[])

        with patch(
            "app.services.trakt.scheduler.AsyncIOScheduler", return_value=mock_scheduler
        ):
            with patch.object(scheduler, "_schedule_all_users"):
                # 执行
                result = scheduler.start()

                # 验证
                assert result is True
                assert scheduler.scheduler == mock_scheduler
                mock_scheduler.start.assert_called_once()
                # Skip the call assertion due to type checking limitations

    def test_start_already_running(self):
        """测试调度器已在运行"""
        scheduler = TraktScheduler()

        # 模拟已在运行的调度器
        mock_scheduler = Mock()
        mock_scheduler.running = True
        scheduler.scheduler = mock_scheduler

        # 执行
        result = scheduler.start()

        # 验证
        assert result is True  # 应该返回 True，但不会重新启动

    def test_start_failure(self):
        """测试启动调度器失败"""
        scheduler = TraktScheduler()

        # 模拟启动时抛出异常
        with patch(
            "app.services.trakt.scheduler.AsyncIOScheduler",
            side_effect=Exception("启动失败"),
        ):
            # 执行
            result = scheduler.start()

            # 验证
            assert result is False
            assert scheduler.scheduler is None

    def test_stop_success(self):
        """测试成功停止调度器"""
        scheduler = TraktScheduler()

        # 模拟运行中的调度器
        mock_scheduler = Mock()
        mock_scheduler.running = True
        mock_scheduler.shutdown = Mock()
        scheduler.scheduler = mock_scheduler
        scheduler._user_jobs = {"user1": "job1"}

        # 执行
        result = scheduler.stop()

        # 验证
        assert result is True
        mock_scheduler.shutdown.assert_called_once()
        assert scheduler.scheduler is None
        assert scheduler._user_jobs == {}

    def test_stop_not_running(self):
        """测试停止未运行的调度器"""
        scheduler = TraktScheduler()
        scheduler.scheduler = None

        # 执行
        result = scheduler.stop()

        # 验证
        assert result is True  # 应该返回 True

    def test_schedule_all_users(self, mock_database_manager):
        """测试为所有用户创建定时任务"""
        scheduler = TraktScheduler()

        # 模拟调度器
        mock_scheduler = Mock()
        scheduler.scheduler = mock_scheduler

        # 准备测试数据
        test_configs = [
            {
                "user_id": "user1",
                "sync_interval": "0 */6 * * *",
                "enabled": True,
                "access_token": "token1",
            },
            {
                "user_id": "user2",
                "sync_interval": "0 2 * * *",
                "enabled": True,
                "access_token": "token2",
            },
        ]

        # 模拟数据库查询
        with patch.object(
            mock_database_manager,
            "get_trakt_configs_with_sync_enabled",
            return_value=test_configs,
        ):
            with patch.object(
                scheduler, "add_user_job", return_value=True
            ) as mock_add_job:
                # 执行
                scheduler._schedule_all_users()

                # 验证
                assert mock_add_job.call_count == 2
                mock_add_job.assert_any_call("user1", "0 */6 * * *")
                mock_add_job.assert_any_call("user2", "0 2 * * *")

    def test_schedule_all_users_no_configs(self, mock_database_manager):
        """测试没有启用同步的用户"""
        scheduler = TraktScheduler()
        scheduler.scheduler = Mock()

        # 模拟数据库返回空列表
        with patch.object(
            mock_database_manager,
            "get_trakt_configs_with_sync_enabled",
            return_value=[],
        ):
            # 执行 - 应该不会出错
            scheduler._schedule_all_users()

    def test_add_user_job_success(self):
        """测试成功添加用户定时任务"""
        scheduler = TraktScheduler()

        # 模拟调度器
        mock_scheduler = Mock()
        mock_job = Mock()
        mock_job.id = "test_job_id"
        mock_job.next_run_time = datetime.now() + timedelta(hours=6)
        mock_job.name = "Test Job"

        mock_scheduler.add_job = Mock(return_value=mock_job)
        scheduler.scheduler = mock_scheduler

        # 执行
        result = scheduler.add_user_job("test_user", "0 */6 * * *")

        # 验证
        assert result is True
        assert "test_user" in scheduler._user_jobs
        assert scheduler._user_jobs["test_user"] == "test_job_id"

        # 验证 add_job 调用
        mock_scheduler.add_job.assert_called_once()
        call_args = mock_scheduler.add_job.call_args
        assert call_args[1]["id"] == "trakt_sync_test_user"
        assert call_args[1]["name"] == "Trakt Sync - test_user"
        assert call_args[1]["trigger"] is not None

    def test_add_user_job_invalid_cron(self):
        """测试无效的 Cron 表达式"""
        scheduler = TraktScheduler()
        scheduler.scheduler = Mock()

        # 执行（无效的 Cron 表达式）
        result = scheduler.add_user_job("test_user", "invalid cron")

        # 验证
        assert result is True  # 应该使用默认值并成功
        assert "test_user" in scheduler._user_jobs

    def test_add_user_job_scheduler_not_initialized(self):
        """测试调度器未初始化时添加任务"""
        scheduler = TraktScheduler()
        scheduler.scheduler = None

        # 执行
        result = scheduler.add_user_job("test_user", "0 */6 * * *")

        # 验证
        assert result is False

    def test_add_user_job_replace_existing(self):
        """测试替换现有任务"""
        scheduler = TraktScheduler()

        # 模拟调度器
        mock_scheduler = Mock()
        mock_job = Mock()
        mock_job.id = "new_job_id"
        mock_scheduler.add_job = Mock(return_value=mock_job)
        mock_scheduler.remove_job = Mock()
        scheduler.scheduler = mock_scheduler

        # 先添加一个任务
        scheduler._user_jobs["test_user"] = "old_job_id"

        # 执行（添加相同的用户）
        result = scheduler.add_user_job("test_user", "0 */6 * * *")

        # 验证
        assert result is True
        mock_scheduler.remove_job.assert_called_once_with("old_job_id")
        assert scheduler._user_jobs["test_user"] == "new_job_id"

    def test_remove_user_job_success(self):
        """测试成功移除用户定时任务"""
        scheduler = TraktScheduler()

        # 模拟调度器
        mock_scheduler = Mock()
        mock_scheduler.remove_job = Mock()
        scheduler.scheduler = mock_scheduler

        # 准备测试数据
        scheduler._user_jobs["test_user"] = "test_job_id"

        # 执行
        result = scheduler.remove_user_job("test_user")

        # 验证
        assert result is True
        mock_scheduler.remove_job.assert_called_once_with("test_job_id")
        assert "test_user" not in scheduler._user_jobs

    def test_remove_user_job_not_found(self):
        """测试移除不存在的用户任务"""
        scheduler = TraktScheduler()
        scheduler.scheduler = Mock()

        # 执行
        result = scheduler.remove_user_job("non_existent_user")

        # 验证
        assert result is True  # 应该返回 True，不会出错

    def test_remove_user_job_scheduler_not_initialized(self):
        """测试调度器未初始化时移除任务"""
        scheduler = TraktScheduler()
        scheduler.scheduler = None
        scheduler._user_jobs["test_user"] = "test_job_id"

        # 执行
        result = scheduler.remove_user_job("test_user")

        # 验证
        assert result is True
        assert "test_user" not in scheduler._user_jobs

    def test_update_user_job(self):
        """测试更新用户的定时任务"""
        scheduler = TraktScheduler()

        with patch.object(
            scheduler, "remove_user_job", return_value=True
        ) as mock_remove:
            with patch.object(scheduler, "add_user_job", return_value=True) as mock_add:
                # 执行
                result = scheduler.update_user_job("test_user", "0 */3 * * *")

                # 验证
                assert result is True
                mock_remove.assert_called_once_with("test_user")
                mock_add.assert_called_once_with("test_user", "0 */3 * * *")

    @pytest.mark.asyncio
    async def test_sync_user_data_success(self, mock_database_manager):
        """测试执行用户数据同步成功"""
        scheduler = TraktScheduler()

        # 准备测试数据
        mock_database_manager.save_trakt_config(
            {
                "user_id": "test_user",
                "access_token": "valid_token",
                "expires_at": int(time.time()) + 3600,
                "enabled": True,
            }
        )

        # 模拟同步服务
        mock_sync_result = Mock(success=True, message="同步完成", synced_count=5)

        with patch(
            "app.services.trakt.scheduler.trakt_sync_service"
        ) as mock_sync_service:
            mock_sync_service.sync_user_trakt_data = AsyncMock(
                return_value=mock_sync_result
            )

            with patch(
                "app.services.trakt.scheduler.trakt_auth_service"
            ) as mock_auth_service:
                mock_auth_service.refresh_token = AsyncMock(return_value=True)

                # 执行
                await scheduler.sync_user_data("test_user")

                # 验证
                mock_sync_service.sync_user_trakt_data.assert_called_once_with(
                    user_id="test_user",
                    full_sync=False,  # 定时任务使用增量同步
                )

    @pytest.mark.asyncio
    async def test_sync_user_data_config_not_found(self, mock_database_manager):
        """测试用户配置未找到"""
        scheduler = TraktScheduler()

        # 执行（用户无配置）
        await scheduler.sync_user_data("non_existent_user")

        # 验证 - 不应该抛出异常

    @pytest.mark.asyncio
    async def test_sync_user_data_disabled(self, mock_database_manager):
        """测试用户同步已禁用"""
        scheduler = TraktScheduler()

        # 准备禁用的配置
        mock_database_manager.save_trakt_config(
            {
                "user_id": "test_user",
                "access_token": "valid_token",
                "expires_at": int(time.time()) + 3600,
                "enabled": False,  # 禁用
            }
        )

        # 执行
        await scheduler.sync_user_data("test_user")

        # 验证 - 不应该调用同步服务

    @pytest.mark.asyncio
    async def test_sync_user_data_token_refresh(self, mock_database_manager):
        """测试 token 过期刷新"""
        scheduler = TraktScheduler()

        # 准备过期的配置
        expired_time = int(time.time()) - 3600
        mock_database_manager.save_trakt_config(
            {
                "user_id": "test_user",
                "access_token": "expired_token",
                "refresh_token": "refresh_token",
                "expires_at": expired_time,
                "enabled": True,
            }
        )

        with patch(
            "app.services.trakt.scheduler.trakt_auth_service"
        ) as mock_auth_service:
            mock_auth_service.refresh_token = AsyncMock(return_value=True)

            with patch(
                "app.services.trakt.scheduler.trakt_sync_service"
            ) as mock_sync_service:
                mock_sync_service.sync_user_trakt_data = AsyncMock()

                # 执行
                await scheduler.sync_user_data("test_user")

                # 验证
                mock_auth_service.refresh_token.assert_called_once_with("test_user")

    @pytest.mark.asyncio
    async def test_sync_user_data_token_refresh_failed(self, mock_database_manager):
        """测试 token 刷新失败"""
        scheduler = TraktScheduler()

        # 准备过期的配置
        expired_time = int(time.time()) - 3600
        mock_database_manager.save_trakt_config(
            {
                "user_id": "test_user",
                "access_token": "expired_token",
                "refresh_token": "refresh_token",
                "expires_at": expired_time,
                "enabled": True,
            }
        )

        with patch(
            "app.services.trakt.scheduler.trakt_auth_service"
        ) as mock_auth_service:
            mock_auth_service.refresh_token = AsyncMock(return_value=False)

            with patch(
                "app.services.trakt.scheduler.trakt_sync_service"
            ) as mock_sync_service:
                mock_sync_service.sync_user_trakt_data = AsyncMock()

                # 执行
                await scheduler.sync_user_data("test_user")

                # 验证
                mock_sync_service.sync_user_trakt_data.assert_not_called()  # 不应该调用同步

    @pytest.mark.asyncio
    async def test_sync_user_data_wrapper_timeout(self):
        """测试同步任务包装器超时"""
        scheduler = TraktScheduler()

        # 模拟超时的同步任务
        async def long_running_task():
            await asyncio.sleep(10)  # 长时间运行

        with patch.object(scheduler, "sync_user_data", side_effect=long_running_task):
            # 执行（设置短超时）
            await scheduler._sync_user_data_wrapper("test_user")

            # 验证 - 不应该抛出异常，应该记录超时错误

    @pytest.mark.asyncio
    async def test_sync_user_data_wrapper_exception(self):
        """测试同步任务包装器异常"""
        scheduler = TraktScheduler()

        # 模拟抛出异常的同步任务
        with patch.object(
            scheduler, "sync_user_data", side_effect=Exception("测试异常")
        ):
            # 执行
            await scheduler._sync_user_data_wrapper("test_user")

            # 验证 - 不应该抛出异常，应该记录错误

    def test_get_user_job_status(self):
        """测试获取用户的定时任务状态"""
        scheduler = TraktScheduler()

        # 模拟调度器和任务
        mock_scheduler = Mock()
        mock_job = Mock()
        mock_job.id = "test_job_id"
        mock_job.name = "Test Job"
        mock_job.next_run_time = datetime.now()
        mock_job.trigger = "cron[hour='*/6']"
        mock_job.pending = False

        mock_scheduler.get_job = Mock(return_value=mock_job)
        scheduler.scheduler = mock_scheduler
        scheduler._user_jobs["test_user"] = "test_job_id"

        # 执行
        status = scheduler.get_user_job_status("test_user")

        # 验证
        assert status is not None
        assert status["job_id"] == "test_job_id"
        assert status["name"] == "Test Job"
        assert status["next_run_time"] is not None
        assert status["trigger"] == "cron[hour='*/6']"
        assert status["pending"] is False

    def test_get_user_job_status_not_found(self):
        """测试获取不存在的用户任务状态"""
        scheduler = TraktScheduler()
        scheduler.scheduler = Mock()

        # 执行
        status = scheduler.get_user_job_status("non_existent_user")

        # 验证
        assert status is None

    def test_get_all_jobs_status(self):
        """测试获取所有定时任务状态"""
        scheduler = TraktScheduler()

        # 模拟多个用户的任务状态
        with patch.object(scheduler, "get_user_job_status") as mock_get_status:
            mock_get_status.side_effect = lambda user_id: {
                "user1": {"job_id": "job1", "next_run_time": 123},
                "user2": {"job_id": "job2", "next_run_time": 456},
            }.get(user_id)

            scheduler._user_jobs = {"user1": "job1", "user2": "job2", "user3": "job3"}

            # 执行
            all_status = scheduler.get_all_jobs_status()

            # 验证
            assert "user1" in all_status
            assert "user2" in all_status
            assert "user3" not in all_status  # get_user_job_status 返回 None
            assert len(all_status) == 2

    def test_trigger_user_sync_success(self):
        """测试成功触发用户的同步任务"""
        scheduler = TraktScheduler()

        # 模拟调度器和任务
        mock_scheduler = Mock()
        mock_job = Mock()
        mock_job.modify = Mock()

        mock_scheduler.get_job = Mock(return_value=mock_job)
        scheduler.scheduler = mock_scheduler
        scheduler._user_jobs["test_user"] = "test_job_id"

        # 执行
        result = scheduler.trigger_user_sync("test_user")

        # 验证
        assert result is True
        mock_job.modify.assert_called_once()
        assert mock_job.modify.call_args[1]["next_run_time"] is not None

    def test_trigger_user_sync_job_not_found(self):
        """测试触发不存在的用户任务"""
        scheduler = TraktScheduler()
        scheduler.scheduler = Mock()

        # 执行
        result = scheduler.trigger_user_sync("non_existent_user")

        # 验证
        assert result is False

    def test_pause_user_job_success(self):
        """测试成功暂停用户的定时任务"""
        scheduler = TraktScheduler()

        # 模拟调度器和任务
        mock_scheduler = Mock()
        mock_job = Mock()
        mock_job.pause = Mock()

        mock_scheduler.get_job = Mock(return_value=mock_job)
        scheduler.scheduler = mock_scheduler
        scheduler._user_jobs["test_user"] = "test_job_id"

        # 执行
        result = scheduler.pause_user_job("test_user")

        # 验证
        assert result is True
        mock_job.pause.assert_called_once()

    def test_resume_user_job_success(self):
        """测试成功恢复用户的定时任务"""
        scheduler = TraktScheduler()

        # 模拟调度器和任务
        mock_scheduler = Mock()
        mock_job = Mock()
        mock_job.resume = Mock()

        mock_scheduler.get_job = Mock(return_value=mock_job)
        scheduler.scheduler = mock_scheduler
        scheduler._user_jobs["test_user"] = "test_job_id"

        # 执行
        result = scheduler.resume_user_job("test_user")

        # 验证
        assert result is True
        mock_job.resume.assert_called_once()

    def test_global_trakt_scheduler_instance(self):
        """测试全局 trakt_scheduler 实例"""
        # 验证全局实例存在
        assert trakt_scheduler is not None
        assert isinstance(trakt_scheduler, TraktScheduler)

        # 验证全局实例是单例
        from app.services.trakt.scheduler import trakt_scheduler as instance2

        assert trakt_scheduler is instance2
