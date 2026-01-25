"""
大数据量同步性能测试
"""

import asyncio
import sys
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.services.trakt.models import TraktHistoryItem
from app.services.trakt.sync_service import TraktSyncService


class TestLargeDatasetSync:
    """大数据量同步性能测试"""

    @pytest.mark.asyncio
    @pytest.mark.performance
    async def test_large_dataset_sync(self, mock_database_manager, mock_sync_service):
        """测试同步大量观看历史记录"""
        print("\n" + "=" * 60)
        print("开始大数据量同步性能测试")
        print("=" * 60)

        user_id = "test_user"
        sync_service = TraktSyncService()

        # 1. 准备测试配置
        mock_database_manager.save_trakt_config(
            {
                "user_id": user_id,
                "access_token": "test_token",
                "expires_at": int(time.time()) + 3600,
                "enabled": True,
                "last_sync_time": None,
            }
        )

        # 2. 生成大量测试数据 (1000条记录)
        large_dataset = []
        for i in range(1000):
            item = TraktHistoryItem(
                id=i,
                watched_at=(datetime.now() - timedelta(days=i % 30)).isoformat() + "Z",
                action="scrobble",
                type="episode",
                episode={
                    "season": (i // 10) + 1,
                    "number": (i % 10) + 1,
                    "title": f"Episode {i}",
                    "ids": {"trakt": i},
                },
                show={
                    "title": f"Test Show {i // 100}",
                    "original_title": f"Test Show {i // 100} Original",
                    "ids": {"trakt": i // 100},
                },
            )
            large_dataset.append(item)

        print(f"生成测试数据: {len(large_dataset)} 条记录")

        # 3. 模拟 Trakt 客户端
        mock_client = AsyncMock()
        mock_client.get_all_watched_history = AsyncMock(return_value=large_dataset)

        # 4. 记录开始时间和内存使用
        start_time = time.time()
        if hasattr(sys, "getallocatedblocks"):  # Python 3.4+
            start_memory = sys.getallocatedblocks()
        else:
            start_memory = None

        # 5. 执行同步
        with patch(
            "app.services.trakt.sync_service.TraktClientFactory.create_client",
            AsyncMock(return_value=mock_client),
        ):
            with patch(
                "app.services.trakt.sync_service.trakt_auth_service"
            ) as mock_auth_service:
                config = Mock(
                    access_token="test_token",
                    last_sync_time=None,
                    is_token_expired=Mock(return_value=False),
                )
                mock_auth_service.get_user_trakt_config.return_value = config
                mock_auth_service.refresh_token = AsyncMock()

                # 执行同步
                result = await sync_service.sync_user_trakt_data(
                    user_id, full_sync=True
                )

                # 记录结束时间
                end_time = time.time()
                elapsed_time = end_time - start_time

                if start_memory is not None:
                    end_memory = sys.getallocatedblocks()
                    memory_used = end_memory - start_memory
                else:
                    memory_used = None

        # 6. 验证结果
        assert result.success is True
        assert result.synced_count == 1000
        assert result.error_count == 0

        # 7. 输出性能指标
        print("\n性能测试结果:")
        print(f"- 总记录数: {len(large_dataset)} 条")
        print(f"- 同步成功: {result.synced_count} 条")
        print(f"- 同步失败: {result.error_count} 条")
        print(f"- 总耗时: {elapsed_time:.2f} 秒")
        print(
            f"- 平均每条记录耗时: {elapsed_time / len(large_dataset) * 1000:.2f} 毫秒"
        )

        if memory_used is not None:
            print(f"- 内存使用增加: {memory_used} 个内存块")

        # 8. 性能断言（可以根据实际情况调整）
        # 目标: 同步时间 < 60秒，内存增加 < 100MB
        assert elapsed_time < 60, f"同步时间过长: {elapsed_time:.2f}秒 > 60秒"

        if memory_used is not None:
            # 粗略估算：每个内存块约48字节（CPython）
            memory_bytes = memory_used * 48
            memory_mb = memory_bytes / (1024 * 1024)
            print(f"- 内存增加估算: {memory_mb:.2f} MB")
            assert memory_mb < 100, f"内存使用过多: {memory_mb:.2f}MB > 100MB"

        print("\n✅ 大数据量同步性能测试通过")
        print(f"   目标: 1000条记录 < 60秒，实际: {elapsed_time:.2f}秒")

    @pytest.mark.asyncio
    @pytest.mark.performance
    async def test_concurrent_large_sync(
        self, mock_database_manager, mock_sync_service
    ):
        """测试并发大数据量同步"""
        print("\n" + "=" * 60)
        print("开始并发大数据量同步性能测试")
        print("=" * 60)

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

        # 生成测试数据（每个用户500条）
        def generate_user_data(user_id, count=500):
            data = []
            for i in range(count):
                item = TraktHistoryItem(
                    id=int(f"{ord(user_id[-1])}{i}"),  # 生成唯一ID
                    watched_at=(datetime.now() - timedelta(days=i % 30)).isoformat()
                    + "Z",
                    action="scrobble",
                    type="episode",
                    episode={
                        "season": (i // 10) + 1,
                        "number": (i % 10) + 1,
                        "title": f"{user_id} Episode {i}",
                    },
                    show={
                        "title": f"{user_id} Show {i // 100}",
                        "original_title": f"{user_id} Show {i // 100} Original",
                    },
                )
                data.append(item)
            return data

        # 模拟客户端工厂
        user_data = {}
        for user_id in users:
            user_data[user_id] = generate_user_data(user_id, 500)

        async def create_client_side_effect(access_token):
            for user_id in users:
                if access_token == f"token_{user_id}":
                    mock_client = AsyncMock()
                    mock_client.get_all_watched_history = AsyncMock(
                        return_value=user_data[user_id]
                    )
                    return mock_client
            return None

        # 记录开始时间
        start_time = time.time()

        # 并发执行
        with patch(
            "app.services.trakt.sync_service.TraktClientFactory.create_client",
            side_effect=create_client_side_effect,
        ):
            with patch(
                "app.services.trakt.sync_service.trakt_auth_service"
            ) as mock_auth_service:

                def get_config_side_effect(user_id):
                    config_dict = mock_database_manager.get_trakt_config(user_id)
                    if config_dict:
                        return Mock(
                            access_token=f"token_{user_id}",
                            last_sync_time=None,
                            is_token_expired=Mock(return_value=False),
                        )
                    return None

                mock_auth_service.get_user_trakt_config.side_effect = (
                    get_config_side_effect
                )
                mock_auth_service.refresh_token = AsyncMock()

                # 创建并发任务
                tasks = []
                for user_id in users:
                    task = sync_service.sync_user_trakt_data(user_id, full_sync=True)
                    tasks.append(task)

                # 执行并发同步
                results = await asyncio.gather(*tasks)

        # 记录结束时间
        end_time = time.time()
        total_elapsed = end_time - start_time

        # 验证结果
        total_records = 0
        for i, result in enumerate(results):
            assert result.success is True, f"用户 {users[i]} 同步失败"
            total_records += result.synced_count

        # 输出性能指标
        print("\n并发性能测试结果:")
        print(f"- 用户数: {len(users)}")
        print(f"- 总记录数: {total_records} 条")
        print(f"- 总耗时: {total_elapsed:.2f} 秒")
        print(f"- 平均每个用户耗时: {total_elapsed / len(users):.2f} 秒")
        print(f"- 平均每条记录耗时: {total_elapsed / total_records * 1000:.2f} 毫秒")

        # 性能断言
        assert total_elapsed < 90, f"并发同步时间过长: {total_elapsed:.2f}秒 > 90秒"

        print("\n✅ 并发大数据量同步性能测试通过")
        print(f"   目标: 3用户×500条 < 90秒，实际: {total_elapsed:.2f}秒")

    @pytest.mark.asyncio
    @pytest.mark.performance
    async def test_memory_efficiency(self, mock_database_manager):
        """测试内存使用效率"""
        print("\n" + "=" * 60)
        print("开始内存使用效率测试")
        print("=" * 60)

        # 这个测试主要观察内存使用模式
        # 我们可以模拟不同数据量的内存使用

        if not hasattr(sys, "getallocatedblocks"):
            print("⚠️  当前Python版本不支持内存块统计，跳过内存测试")
            return

        sync_service = TraktSyncService()
        user_id = "mem_test_user"

        # 测试不同数据量的内存使用
        test_sizes = [100, 500, 1000, 2000]
        memory_results = []

        for size in test_sizes:
            print(f"\n测试数据量: {size} 条记录")

            # 准备配置
            mock_database_manager.save_trakt_config(
                {
                    "user_id": user_id,
                    "access_token": "test_token",
                    "expires_at": int(time.time()) + 3600,
                    "enabled": True,
                }
            )

            # 生成测试数据
            test_data = []
            for i in range(size):
                item = TraktHistoryItem(
                    id=i,
                    watched_at=datetime.now().isoformat() + "Z",
                    action="scrobble",
                    type="episode",
                    episode={"season": 1, "number": i + 1},
                    show={"title": f"Test Show {i // 100}"},
                )
                test_data.append(item)

            # 模拟客户端
            mock_client = AsyncMock()
            mock_client.get_all_watched_history = AsyncMock(return_value=test_data)

            # 记录开始内存
            start_memory = sys.getallocatedblocks()

            with patch(
                "app.services.trakt.sync_service.TraktClientFactory.create_client",
                AsyncMock(return_value=mock_client),
            ):
                with patch(
                    "app.services.trakt.sync_service.trakt_auth_service"
                ) as mock_auth_service:
                    config = Mock(
                        access_token="test_token",
                        last_sync_time=None,
                        is_token_expired=Mock(return_value=False),
                    )
                    mock_auth_service.get_user_trakt_config.return_value = config
                    mock_auth_service.refresh_token = AsyncMock()

                    # 执行同步（但跳过实际同步服务调用以专注内存）
                    with patch(
                        "app.services.trakt.sync_service.sync_service"
                    ) as mock_sync_service:
                        mock_sync_service.sync_custom_item_async = AsyncMock(
                            return_value="dummy_task"
                        )

                        # 执行
                        await sync_service.sync_user_trakt_data(user_id, full_sync=True)

            # 记录结束内存
            end_memory = sys.getallocatedblocks()
            memory_used = end_memory - start_memory

            # 计算内存使用率
            memory_per_record = memory_used / size if size > 0 else 0
            memory_results.append((size, memory_used, memory_per_record))

            print(f"  - 内存使用: {memory_used} 块")
            print(f"  - 平均每条记录: {memory_per_record:.2f} 块")

            # 强制垃圾回收
            import gc

            gc.collect()

        # 分析内存使用趋势
        print(f"\n{'=' * 60}")
        print("内存使用趋势分析:")
        print(f"{'=' * 60}")
        print(f"{'数据量':<10} {'内存块':<10} {'每记录块':<10}")
        print(f"{'-' * 30}")

        for size, memory, per_record in memory_results:
            print(f"{size:<10} {memory:<10} {per_record:.2f}")

        # 检查内存使用是否线性增长（应该有边际递减）
        if len(memory_results) >= 2:
            # 计算增长率
            growth_rates = []
            for i in range(1, len(memory_results)):
                prev_size, prev_memory, _ = memory_results[i - 1]
                curr_size, curr_memory, _ = memory_results[i]

                size_growth = curr_size / prev_size
                memory_growth = curr_memory / prev_memory
                growth_rate = memory_growth / size_growth
                growth_rates.append(growth_rate)

            print("\n内存增长率分析:")
            for i, rate in enumerate(growth_rates):
                print(f"  {test_sizes[i]} -> {test_sizes[i + 1]}: {rate:.2%}")

            # 增长率应该 <= 1.2（允许20%的额外开销）
            for rate in growth_rates:
                assert rate <= 1.2, f"内存增长过快: {rate:.2%} > 120%"

        print("\n✅ 内存使用效率测试通过")

    @pytest.mark.asyncio
    async def test_error_handling_performance(self, mock_database_manager):
        """测试错误处理性能"""
        print("\n" + "=" * 60)
        print("开始错误处理性能测试")
        print("=" * 60)

        user_id = "error_test_user"
        sync_service = TraktSyncService()

        # 准备配置
        mock_database_manager.save_trakt_config(
            {
                "user_id": user_id,
                "access_token": "test_token",
                "expires_at": int(time.time()) + 3600,
                "enabled": True,
            }
        )

        # 生成混合数据（包含一些错误数据）
        mixed_data = []
        for i in range(100):
            if i % 10 == 0:  # 每10条有一条无效数据
                # 无效数据（缺少必要字段）
                item = TraktHistoryItem(
                    id=i,
                    watched_at=datetime.now().isoformat() + "Z",
                    action="scrobble",
                    type="episode",
                    episode={},  # 空的 episode
                    show=None,  # 缺少 show
                )
            else:
                # 有效数据
                item = TraktHistoryItem(
                    id=i,
                    watched_at=datetime.now().isoformat() + "Z",
                    action="scrobble",
                    type="episode",
                    episode={"season": 1, "number": i % 10 + 1},
                    show={"title": f"Test Show {i // 10}"},
                )
            mixed_data.append(item)

        print(
            f"测试数据: {len(mixed_data)} 条记录（包含 {len([d for d in mixed_data if not d.episode])} 条错误数据）"
        )

        # 模拟客户端
        mock_client = AsyncMock()
        mock_client.get_all_watched_history = AsyncMock(return_value=mixed_data)

        # 模拟同步服务部分失败
        call_count = 0

        async def mock_sync_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count % 5 == 0:  # 每5次调用失败一次
                raise Exception("模拟同步失败")
            return f"task_{call_count}"

        start_time = time.time()

        with patch(
            "app.services.trakt.sync_service.TraktClientFactory.create_client",
            AsyncMock(return_value=mock_client),
        ):
            with patch(
                "app.services.trakt.sync_service.trakt_auth_service"
            ) as mock_auth_service:
                config = Mock(
                    access_token="test_token",
                    last_sync_time=None,
                    is_token_expired=Mock(return_value=False),
                )
                mock_auth_service.get_user_trakt_config.return_value = config
                mock_auth_service.refresh_token = AsyncMock()

                with patch(
                    "app.services.trakt.sync_service.sync_service"
                ) as mock_sync_service:
                    mock_sync_service.sync_custom_item_async = AsyncMock(
                        side_effect=mock_sync_side_effect
                    )

                    # 执行同步
                    result = await sync_service.sync_user_trakt_data(
                        user_id, full_sync=True
                    )

        end_time = time.time()
        elapsed_time = end_time - start_time

        # 验证结果
        print("\n错误处理性能测试结果:")
        print(f"- 总记录数: {len(mixed_data)}")
        print(f"- 成功同步: {result.synced_count}")
        print(f"- 同步错误: {result.error_count}")
        print(f"- 跳过数据: {result.skipped_count}")
        print(f"- 总耗时: {elapsed_time:.2f} 秒")

        # 验证错误被正确处理
        expected_skipped = len(
            [d for d in mixed_data if not d.episode]
        )  # 无效数据应该被跳过
        expected_errors = (
            len(mixed_data) - expected_skipped
        ) // 5  # 每5条有效数据有一条同步失败

        assert result.skipped_count >= expected_skipped, (
            f"应该跳过至少 {expected_skipped} 条无效数据"
        )
        assert result.error_count >= expected_errors, (
            f"应该至少有 {expected_errors} 条同步错误"
        )

        print("\n✅ 错误处理性能测试通过")
        print("   正确处理了无效数据和同步错误")
