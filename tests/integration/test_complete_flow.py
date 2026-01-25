"""
Trakt 完整流程集成测试
"""

import asyncio
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.models.trakt import TraktConfig
from app.services.trakt.auth import TraktAuthService
from app.services.trakt.client import TraktClient
from app.services.trakt.models import TraktHistoryItem
from app.services.trakt.scheduler import TraktScheduler
from app.services.trakt.sync_service import TraktSyncService


class TestCompleteTraktFlow:
    """完整 Trakt 流程集成测试"""

    @pytest.mark.asyncio
    async def test_complete_user_journey(
        self, mock_database_manager, mock_sync_service
    ):
        """测试从授权到定期同步的完整流程"""
        # 1. 初始化服务
        auth_service = TraktAuthService()
        sync_service = TraktSyncService()

        user_id = "test_user"

        # TODO: 需要mock auth_service._validate_config 方法以绕过实际验证
        with patch.object(
            TraktAuthService, "_validate_config", AsyncMock(return_value=True)
        ):
            # 2. 模拟 OAuth 授权
            # 生成授权 URL
            with patch("secrets.token_urlsafe", return_value="test_state_123"):
                auth_response = await auth_service.init_oauth(user_id)
                assert auth_response is not None
                assert auth_response.auth_url.startswith(auth_service.auth_url)
                assert "test_state_123" in auth_service._oauth_states

        # 3. 模拟 OAuth 回调处理
        mock_trakt_response = {
            "access_token": "test_access_token_789",
            "refresh_token": "test_refresh_token_101",
            "expires_in": 7200,
            "scope": "public",
            "token_type": "bearer",
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json = Mock(return_value=mock_trakt_response)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # 处理回调
            from app.models.trakt import TraktCallbackRequest

            callback_request = TraktCallbackRequest(
                code="test_code", state="test_state_123"
            )
            result = await auth_service.handle_callback(callback_request, "test_user")
            assert result.success is True

            # 验证配置保存
            saved_config = mock_database_manager.get_trakt_config(user_id)
            assert saved_config is not None
            assert saved_config["access_token"] == "test_access_token_789"
            assert saved_config["user_id"] == user_id

        # 4. 配置同步间隔
        config = TraktConfig.from_dict(saved_config)
        config.enabled = True
        config.sync_interval = "0 */2 * * *"  # 每2小时
        mock_database_manager.save_trakt_config(config.to_dict())

        # 5. 模拟 Trakt 数据获取
        trakt_client = TraktClient(access_token=config.access_token)

        # 创建测试数据
        test_history_item = TraktHistoryItem(
            id=123456,
            watched_at="2024-01-15T20:30:00.000Z",
            action="scrobble",
            type="episode",
            episode={"season": 1, "number": 5, "title": "Pilot", "ids": {"trakt": 123}},
            show={
                "title": "Example Show",
                "original_title": "Example Show Original",
                "ids": {"trakt": 456},
            },
        )

        # 模拟客户端方法
        trakt_client.get_all_watched_history = AsyncMock(
            return_value=[test_history_item]
        )

        # 6. 执行数据同步
        with patch(
            "app.services.trakt.sync_service.TraktClientFactory.create_client",
            AsyncMock(return_value=trakt_client),
        ):
            with patch(
                "app.services.trakt.sync_service.trakt_auth_service"
            ) as mock_auth_service:
                mock_auth_service.get_user_trakt_config.return_value = config
                mock_auth_service.refresh_token = AsyncMock()

                # 执行同步
                sync_result = await sync_service.sync_user_trakt_data(
                    user_id, full_sync=False
                )

                # 验证同步结果
                assert sync_result.success is True
                assert sync_result.synced_count == 1
                assert sync_result.error_count == 0

                # 验证同步服务调用
                mock_sync_service.sync_custom_item_async.assert_called_once()

        # 7. 验证同步历史记录
        sync_history = mock_database_manager.get_trakt_sync_history(user_id)
        assert sync_history["total"] > 0

        # 8. 测试调度器集成
        scheduler = TraktScheduler()

        # 模拟调度器启动
        with patch(
            "app.services.trakt.scheduler.AsyncIOScheduler"
        ) as mock_scheduler_class:
            mock_scheduler = Mock()
            mock_scheduler.running = False
            mock_scheduler.start = Mock()
            mock_scheduler.add_job = Mock()
            mock_scheduler_class.return_value = mock_scheduler

            # 启动调度器
            scheduler.start()
            assert scheduler.scheduler is not None

        # 9. 验证配置持久化
        updated_config = mock_database_manager.get_trakt_config(user_id)
        assert updated_config["enabled"] is True
        assert updated_config["sync_interval"] == "0 */2 * * *"

    @pytest.mark.asyncio
    async def test_error_recovery_scenario(self, mock_database_manager):
        """测试错误场景下的恢复能力"""
        # 1. 配置有效的 Trakt 连接
        user_id = "test_user"
        mock_database_manager.save_trakt_config(
            {
                "user_id": user_id,
                "access_token": "valid_token",
                "expires_at": int(time.time()) + 3600,
                "enabled": True,
                "last_sync_time": int(time.time()) - 86400,
            }
        )

        sync_service = TraktSyncService()

        # 2. 模拟第一次同步时 Trakt API 暂时不可用
        with patch(
            "app.services.trakt.sync_service.TraktClientFactory.create_client"
        ) as mock_create_client:
            # 第一次调用失败，第二次调用成功
            mock_client_failure = AsyncMock()
            mock_client_failure.get_all_watched_history = AsyncMock(
                side_effect=Exception("Trakt API 暂时不可用")
            )

            mock_client_success = AsyncMock()
            mock_client_success.get_all_watched_history = AsyncMock(return_value=[])

            mock_create_client.side_effect = [mock_client_failure, mock_client_success]

            with patch(
                "app.services.trakt.sync_service.trakt_auth_service"
            ) as mock_auth_service:
                config = TraktConfig.from_dict(
                    mock_database_manager.get_trakt_config(user_id)
                )
                mock_auth_service.get_user_trakt_config.return_value = config
                mock_auth_service.refresh_token = AsyncMock()

                # 第一次同步应该失败
                first_result = await sync_service.sync_user_trakt_data(user_id)
                assert first_result.success is False
                assert first_result.error_count > 0

                # 等待一段时间（模拟系统继续运行）
                await asyncio.sleep(0.1)

                # 第二次同步应该成功（API 恢复）
                second_result = await sync_service.sync_user_trakt_data(user_id)
                assert second_result.success is True

        # 3. 验证错误被记录但系统继续运行

    @pytest.mark.asyncio
    async def test_concurrent_user_sync(self, mock_database_manager, mock_sync_service):
        """测试多个用户同时同步"""
        # 准备3个用户
        users = ["user1", "user2", "user3"]
        sync_service = TraktSyncService()

        for user_id in users:
            mock_database_manager.save_trakt_config(
                {
                    "user_id": user_id,
                    "access_token": f"token_{user_id}",
                    "expires_at": int(time.time()) + 3600,
                    "enabled": True,
                }
            )

        # 模拟 Trakt 客户端
        mock_clients = {}

        def create_mock_client(user_id):
            mock_client = AsyncMock()
            # 每个用户返回不同的数据
            history_item = TraktHistoryItem(
                id=int(user_id[-1]),  # 使用用户ID的最后一位作为区别
                watched_at="2024-01-15T20:30:00.000Z",
                action="scrobble",
                type="episode",
                episode={"season": 1, "number": int(user_id[-1])},
                show={"title": f"Show for {user_id}"},
            )
            mock_client.get_all_watched_history = AsyncMock(return_value=[history_item])
            return mock_client

        # 模拟客户端工厂
        async def create_client_side_effect(access_token):
            # 根据token找到对应的用户ID
            for user_id in users:
                if access_token == f"token_{user_id}":
                    if user_id not in mock_clients:
                        mock_clients[user_id] = create_mock_client(user_id)
                    return mock_clients[user_id]
            return None

        with patch(
            "app.services.trakt.sync_service.TraktClientFactory.create_client",
            side_effect=create_client_side_effect,
        ):
            with patch(
                "app.services.trakt.sync_service.trakt_auth_service"
            ) as mock_auth_service:
                # 配置认证服务
                def get_config_side_effect(user_id):
                    config_dict = mock_database_manager.get_trakt_config(user_id)
                    if config_dict:
                        return TraktConfig.from_dict(config_dict)
                    return None

                mock_auth_service.get_user_trakt_config.side_effect = (
                    get_config_side_effect
                )
                mock_auth_service.refresh_token = AsyncMock()

                # 并发执行同步任务
                tasks = []
                for user_id in users:
                    task = sync_service.sync_user_trakt_data(user_id)
                    tasks.append(task)

                # 等待所有任务完成
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # 验证所有用户都同步成功
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        # 不应该有异常
                        raise result
                    assert result.success is True, (
                        f"用户 {users[i]} 同步失败: {result.message}"
                    )
                    assert result.synced_count == 1, f"用户 {users[i]} 应该同步1条记录"

                # 验证每个用户的数据独立
                for user_id in users:
                    sync_history = mock_database_manager.get_trakt_sync_history(
                        user_id, limit=10
                    )
                    assert sync_history["total"] == 1
                    record = sync_history["records"][0]
                    assert record["user_id"] == user_id

                # 验证无数据混淆
                # 每个用户的同步历史应该只包含自己的记录

    @pytest.mark.asyncio
    async def test_token_auto_refresh_flow(self, mock_database_manager):
        """测试 token 自动刷新流程"""
        user_id = "test_user"
        auth_service = TraktAuthService()

        # 1. 初始 token（已过期）
        expired_time = int(time.time()) - 3600
        mock_database_manager.save_trakt_config(
            {
                "user_id": user_id,
                "access_token": "expired_token",
                "refresh_token": "valid_refresh_token",
                "expires_at": expired_time,
                "enabled": True,
            }
        )

        # 2. 模拟 token 刷新成功
        mock_refresh_response = {
            "access_token": "new_access_token_123",
            "refresh_token": "new_refresh_token_456",
            "expires_in": 7200,
            "scope": "public",
            "token_type": "bearer",
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json = Mock(return_value=mock_refresh_response)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # 执行刷新
            result = await auth_service.refresh_token(user_id)

            # 验证
            assert result is True

            # 验证新 token 保存
            updated_config = mock_database_manager.get_trakt_config(user_id)
            assert updated_config["access_token"] == "new_access_token_123"
            assert updated_config["refresh_token"] == "new_refresh_token_456"
            assert updated_config["expires_at"] > expired_time

        # 3. 使用新 token 进行同步
        sync_service = TraktSyncService()

        with patch(
            "app.services.trakt.sync_service.TraktClientFactory.create_client"
        ) as mock_create_client:
            mock_client = AsyncMock()
            mock_client.get_all_watched_history = AsyncMock(return_value=[])
            mock_create_client.return_value = mock_client

            with patch(
                "app.services.trakt.sync_service.trakt_auth_service"
            ) as mock_auth_service:
                config = TraktConfig.from_dict(updated_config)
                mock_auth_service.get_user_trakt_config.return_value = config

                # 同步应该成功（使用新 token）
                sync_result = await sync_service.sync_user_trakt_data(user_id)
                assert sync_result.success is True

                # 验证客户端使用新 token 创建
                mock_create_client.assert_called_once_with("new_access_token_123")

    @pytest.mark.asyncio
    async def test_incremental_sync_logic(
        self, mock_database_manager, mock_sync_service
    ):
        """测试增量同步逻辑"""
        user_id = "test_user"
        sync_service = TraktSyncService()

        # 1. 设置上次同步时间（3天前）
        three_days_ago = int(time.time()) - 3 * 86400
        mock_database_manager.save_trakt_config(
            {
                "user_id": user_id,
                "access_token": "test_token",
                "expires_at": int(time.time()) + 3600,
                "enabled": True,
                "last_sync_time": three_days_ago,
            }
        )

        config = TraktConfig.from_dict(mock_database_manager.get_trakt_config(user_id))

        # 2. 模拟 Trakt 客户端
        mock_client = AsyncMock()

        # 创建一些历史数据
        old_item = TraktHistoryItem(
            id=1,
            watched_at=(datetime.now() - timedelta(days=5)).isoformat() + "Z",  # 5天前
            action="scrobble",
            type="episode",
            episode={"season": 1, "number": 1},
            show={"title": "Old Show"},
        )

        new_item = TraktHistoryItem(
            id=2,
            watched_at=(datetime.now() - timedelta(days=1)).isoformat() + "Z",  # 1天前
            action="scrobble",
            type="episode",
            episode={"season": 1, "number": 2},
            show={"title": "New Show"},
        )

        # 模拟客户端返回新旧数据
        mock_client.get_all_watched_history = AsyncMock(
            return_value=[old_item, new_item]
        )

        # 3. 执行增量同步（从3天前开始）
        result = await sync_service._sync_watched_history(
            user_id=user_id, client=mock_client, config=config, full_sync=False
        )

        # 4. 验证
        assert result.success is True

        # 验证客户端调用参数包含开始日期
        mock_client.get_all_watched_history.assert_called_once()
        call_args = mock_client.get_all_watched_history.call_args[1]
        assert "start_date" in call_args

        # 开始日期应该是 last_sync_time 减去一天缓冲
        start_date = call_args["start_date"]
        assert start_date is not None

        # 5. 验证只有新数据被同步（旧数据应该被过滤）
        # 注意：实际过滤在客户端层面，这里我们验证客户端被正确调用

        # 6. 执行全量同步
        mock_client.get_all_watched_history.reset_mock()
        mock_client.get_all_watched_history.return_value = [old_item, new_item]

        result = await sync_service._sync_watched_history(
            user_id=user_id, client=mock_client, config=config, full_sync=True
        )

        # 验证全量同步不包含开始日期
        mock_client.get_all_watched_history.assert_called_once()
        call_args = mock_client.get_all_watched_history.call_args[1]
        assert "start_date" not in call_args

    @pytest.mark.asyncio
    async def test_scheduler_integration(self, mock_database_manager):
        """测试调度器集成"""
        # 准备用户配置
        user_id = "test_user"
        mock_database_manager.save_trakt_config(
            {
                "user_id": user_id,
                "access_token": "test_token",
                "expires_at": int(time.time()) + 3600,
                "enabled": True,
                "sync_interval": "*/5 * * * *",  # 每5分钟（测试用）
            }
        )

        scheduler = TraktScheduler()

        # 模拟调度器
        mock_async_scheduler = Mock()
        mock_async_scheduler.running = False
        mock_async_scheduler.start = Mock()
        mock_async_scheduler.add_job = Mock()

        with patch(
            "app.services.trakt.scheduler.AsyncIOScheduler",
            return_value=mock_async_scheduler,
        ):
            # 启动调度器
            result = scheduler.start()
            assert result is True
            assert scheduler.scheduler == mock_async_scheduler

            # 验证调度器启动
            mock_async_scheduler.start.assert_called_once()

        # 模拟定时任务执行
        with patch.object(scheduler, "sync_user_data", AsyncMock()) as mock_sync_data:
            # 模拟调度器触发任务
            await scheduler._sync_user_data_wrapper(user_id)

            # 验证同步方法被调用
            mock_sync_data.assert_called_once_with(user_id)

        # 测试调度器停止
        mock_async_scheduler.running = True
        mock_async_scheduler.shutdown = Mock()

        result = scheduler.stop()
        assert result is True
        mock_async_scheduler.shutdown.assert_called_once()
        assert scheduler.scheduler is None
