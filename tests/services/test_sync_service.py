"""
SyncService 单元测试
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# 确保可以导入 app 模块
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


class TestSyncServiceInit:
    """测试 SyncService 初始化"""

    def test_sync_service_init(self):
        """测试服务初始化"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            # 验证初始化属性
            assert hasattr(service, "_executor")
            assert hasattr(service, "_sync_tasks")
            assert hasattr(service, "_task_counter")
            assert service._task_counter == 0
            assert service._sync_tasks == {}

    def test_sync_service_has_required_methods(self):
        """测试服务有所需的方法"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            # 验证关键方法存在
            assert hasattr(service, "sync_custom_item")
            assert hasattr(service, "sync_custom_item_async")
            assert hasattr(service, "sync_plex_item")
            assert hasattr(service, "sync_emby_item")
            assert hasattr(service, "sync_jellyfin_item")
            assert hasattr(service, "sync_movie_watching")
            assert hasattr(service, "get_sync_task_status")
            assert hasattr(service, "cleanup_old_tasks")


class TestSyncCustomItem:
    """测试自定义同步功能"""

    @pytest.fixture
    def mock_sync_service(self):
        """创建带有 mock 的 SyncService"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager") as mock_db,
            patch("app.services.sync_service.send_notify") as mock_notify,
            patch("app.services.sync_service.mapping_service") as mock_mapping,
            patch("app.services.sync_service.BangumiApi") as MockBgmApi,
        ):
            # Mock config
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "mode"): "single",
                ("sync", "blocked_keywords"): "",
                ("sync", "movie_mark_subject_completed"): True,
                ("sync", "movie_playback_start_mark_watching"): True,
                ("bangumi_data", "enabled"): False,
            }.get((section, key), fallback)

            mock_config.get_single_mode_media_usernames.return_value = ["test_user"]
            mock_config.get_user_mappings.return_value = {}
            mock_config.get_bangumi_configs.return_value = {}

            # Mock BangumiApi
            mock_api = MagicMock()
            MockBgmApi.return_value = mock_api

            # Mock get_target_season_episode_id - returns (subject_id, episode_id)
            mock_api.get_target_season_episode_id.return_value = ("123456", "789012")
            mock_api.get_movie_main_episode_id.return_value = ("123456", "789012")

            # Mock mark_episode_watched - returns 1 (marked as watched)
            mock_api.mark_episode_watched.return_value = 1
            mock_api.ensure_subject_watching.return_value = 1
            mock_api.get_subject_collection.return_value = {}

            # Mock mapping_service
            mock_mapping.load_custom_mappings.return_value = {}

            from app.services.sync_service import SyncService

            service = SyncService()

            yield service, mock_config, mock_db, mock_notify

    def test_sync_custom_item_invalid_media_type(self):
        """测试不支持的媒体类型"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.models.sync import CustomItem
            from app.services.sync_service import SyncService

            service = SyncService()

            item = CustomItem(
                media_type="music",
                title="测试",
                ori_title="",
                season=1,
                episode=1,
                release_date="2024-01-01",
                user_name="test_user",
            )

            result = service.sync_custom_item(item, source="custom")

            assert result.status == "error"
            assert "不支持" in result.message

    def test_sync_custom_item_blocked(self):
        """测试屏蔽词跳过场景"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "mode"): "single",
                ("sync", "blocked_keywords"): "测试,广告",  # 包含屏蔽词
                ("bangumi_data", "enabled"): False,
            }.get((section, key), fallback)
            mock_config.get_single_mode_media_usernames.return_value = ["test_user"]
            mock_config.get_user_mappings.return_value = {}
            mock_config.get_bangumi_configs.return_value = {}

            from app.models.sync import CustomItem
            from app.services.sync_service import SyncService

            service = SyncService()

            item = CustomItem(
                media_type="episode",
                title="这是一个测试番剧",
                ori_title="Test Anime",
                season=1,
                episode=1,
                release_date="2024-01-01",
                user_name="test_user",
            )

            result = service.sync_custom_item(item, source="custom")

            assert result.status == "ignored"
            assert "屏蔽关键词" in result.message

    def test_sync_custom_item_movie_success_with_fixture(self, mock_sync_service):
        """电影同步成功（短路径 + 条目标看过）"""
        service, mock_config, mock_db, mock_notify = mock_sync_service

        from app.models.sync import CustomItem

        with patch.object(service, "_find_subject_id", return_value=("999", False, "")):
            with patch.object(
                service,
                "_get_bangumi_config_for_user",
                return_value={
                    "username": "u",
                    "access_token": "t",
                    "private": True,
                },
            ):
                item = CustomItem(
                    media_type="movie",
                    title="剧场版测试",
                    ori_title=None,
                    season=1,
                    episode=1,
                    release_date="",
                    user_name="test_user",
                )
                result = service.sync_custom_item(item, source="custom")

        assert result.status == "success"
        mock_db.log_sync_record.assert_called()
        _args, call_kw = mock_db.log_sync_record.call_args
        assert call_kw.get("media_type") == "movie"

    def test_sync_custom_item_mark_watching_skips_episode_resolution(
        self, mock_sync_service
    ):
        """mark_watching + 电影只调 ensure_subject_watching，不解析章节、不点已看"""
        service, mock_config, mock_db, mock_notify = mock_sync_service
        from app.models.sync import CustomItem

        local_bgm = MagicMock()
        local_bgm.ensure_subject_watching.return_value = 1
        with patch.object(service, "_find_subject_id", return_value=("777", False, "")):
            with patch.object(
                service, "_get_bangumi_api_for_user", return_value=local_bgm
            ):
                item = CustomItem(
                    media_type="movie",
                    title="剧场版测试",
                    ori_title=None,
                    season=1,
                    episode=1,
                    release_date="2024-01-01",
                    user_name="test_user",
                    sync_action="mark_watching",
                )
                result = service.sync_custom_item(item, source="custom")

        assert result.status == "success"
        local_bgm.ensure_subject_watching.assert_called_once_with("777")
        local_bgm.get_movie_main_episode_id.assert_not_called()
        local_bgm.mark_episode_watched.assert_not_called()

    def test_sync_custom_item_mark_watching_episode_ignored(self, mock_sync_service):
        """mark_watching + 剧集不走 sync_movie_watching"""
        service, mock_config, mock_db, mock_notify = mock_sync_service
        from app.models.sync import CustomItem

        item = CustomItem(
            media_type="episode",
            title="某番",
            ori_title="",
            season=1,
            episode=1,
            release_date="2024-01-01",
            user_name="test_user",
            sync_action="mark_watching",
        )
        with patch.object(service, "sync_movie_watching") as mock_watch:
            r = service.sync_custom_item(item, source="custom")
        assert r.status == "ignored"
        assert "剧场版" in r.message
        mock_watch.assert_not_called()

    def test_sync_custom_item_empty_title(self, mock_sync_service):
        """测试空标题"""
        service, mock_config, mock_db, mock_notify = mock_sync_service

        from app.models.sync import CustomItem

        item = CustomItem(
            media_type="episode",
            title="",  # 空标题
            ori_title="",
            season=1,
            episode=1,
            release_date="2024-01-01",
            user_name="test_user",
        )

        result = service.sync_custom_item(item, source="custom")

        assert result.status == "error"
        assert "为空" in result.message

    def test_sync_custom_item_sp_not_supported(self, mock_sync_service):
        """测试 SP 标记不支持"""
        service, mock_config, mock_db, mock_notify = mock_sync_service

        from app.models.sync import CustomItem

        item = CustomItem(
            media_type="episode",
            title="测试番剧",
            ori_title="",
            season=0,  # SP 标记
            episode=1,
            release_date="2024-01-01",
            user_name="test_user",
        )

        result = service.sync_custom_item(item, source="custom")

        assert result.status == "error"
        assert "SP" in result.message or "不支持" in result.message

    def test_sync_custom_item_zero_episode(self, mock_sync_service):
        """测试集数为0"""
        service, mock_config, mock_db, mock_notify = mock_sync_service

        from app.models.sync import CustomItem

        item = CustomItem(
            media_type="episode",
            title="测试番剧",
            ori_title="",
            season=1,
            episode=0,  # 集数为0
            release_date="2024-01-01",
            user_name="test_user",
        )

        result = service.sync_custom_item(item, source="custom")

        assert result.status == "error"
        assert "0" in result.message

    def test_sync_custom_item_no_permission(self, mock_sync_service):
        """测试用户无权限"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            # 配置为单用户模式，但请求用户名不匹配
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "mode"): "single",
                ("sync", "blocked_keywords"): "",
                ("bangumi_data", "enabled"): False,
            }.get((section, key), fallback)
            mock_config.get_single_mode_media_usernames.return_value = ["admin"]

            from app.models.sync import CustomItem

            item = CustomItem(
                media_type="episode",
                title="测试番剧",
                ori_title="",
                season=1,
                episode=1,
                release_date="2024-01-01",
                user_name="other_user",  # 不同的用户
            )

            from app.services.sync_service import SyncService

            service = SyncService()

            result = service.sync_custom_item(item, source="custom")

            assert result.status == "error"
            assert "无权限" in result.message

    def test_sync_custom_item_subject_not_found_logs_record(self, mock_sync_service):
        """未找到番剧时写入同步记录"""
        service, mock_config, mock_db, mock_notify = mock_sync_service
        from app.models.sync import CustomItem

        item = CustomItem(
            media_type="episode",
            title="未知番",
            ori_title="X",
            season=1,
            episode=2,
            release_date="2024-01-01",
            user_name="test_user",
            source="plex",
        )
        with patch.object(
            service,
            "_find_subject_id",
            return_value=(None, False, "Bangumi 搜索无结果"),
        ):
            r = service.sync_custom_item(item, source="custom")
        assert r.status == "error"
        assert "未找到" in r.message
        mock_db.log_sync_record.assert_called_once()
        kw = mock_db.log_sync_record.call_args[1]
        assert kw.get("status") == "error"
        assert kw.get("source") == "plex"
        assert "未查询到番剧信息，跳过" in (kw.get("message") or "")
        assert "Bangumi 搜索无结果" in (kw.get("message") or "")
        assert "premiere_date=2024-01-01" in (kw.get("message") or "")


class TestSyncTaskStatus:
    """测试任务状态功能"""

    def test_get_sync_task_status(self):
        """测试获取任务状态"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            # 添加一个任务
            service._sync_tasks["test_task_1"] = {
                "status": "pending",
                "item": {"title": "test"},
                "source": "custom",
                "created_at": 1234567890,
                "result": None,
                "error": None,
            }

            # 获取任务状态
            status = service.get_sync_task_status("test_task_1")

            assert status is not None
            assert status["status"] == "pending"

    def test_get_sync_task_status_not_found(self):
        """测试获取不存在的任务状态"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            status = service.get_sync_task_status("nonexistent_task")

            assert status is None


class TestCleanupOldTasks:
    """测试清理旧任务"""

    def test_cleanup_old_tasks(self):
        """测试清理旧任务"""
        import time as time_module

        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            # 获取当前时间
            current_time = time_module.time()

            # 添加任务：1个新的，2个旧的（超过24小时）
            service._sync_tasks["new_task"] = {
                "status": "completed",
                "created_at": current_time - 100,  # 100秒前
            }
            service._sync_tasks["old_task_1"] = {
                "status": "completed",
                "created_at": current_time - 90000,  # 超过24小时前
            }
            service._sync_tasks["old_task_2"] = {
                "status": "failed",
                "created_at": current_time - 95000,  # 超过24小时前
            }

            # 执行清理
            service.cleanup_old_tasks(max_age_hours=24)

            # 验证只保留新任务
            assert "new_task" in service._sync_tasks
            assert "old_task_1" not in service._sync_tasks
            assert "old_task_2" not in service._sync_tasks


class TestSyncMovieWatching:
    """sync_movie_watching 全分支（配置 / 权限 / 匹配 / Bangumi）"""

    @staticmethod
    def _cfg_get(*, mark_watching_enabled: bool = True, blocked_keywords: str = ""):
        def _get(section, key, fallback=None):
            d = {
                ("sync", "movie_playback_start_mark_watching"): mark_watching_enabled,
                ("sync", "blocked_keywords"): blocked_keywords,
                ("sync", "mode"): "single",
            }
            return d.get((section, key), fallback)

        return _get

    @staticmethod
    def _movie_item(**kwargs):
        from app.models.sync import CustomItem

        base = {
            "media_type": "movie",
            "title": "剧场版测试",
            "ori_title": None,
            "season": 1,
            "episode": 1,
            "release_date": "2024-01-01",
            "user_name": "test_user",
        }
        base.update(kwargs)
        return CustomItem(**base)

    def test_sync_movie_watching_config_disabled(self):
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = self._cfg_get(mark_watching_enabled=False)
            from app.services.sync_service import SyncService

            svc = SyncService()
            r = svc.sync_movie_watching(self._movie_item(), source="custom")
        assert r.status == "ignored"
        assert "关闭" in r.message

    def test_sync_movie_watching_not_movie(self):
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = self._cfg_get()
            mock_config.get_single_mode_media_usernames.return_value = ["test_user"]
            from app.models.sync import CustomItem
            from app.services.sync_service import SyncService

            svc = SyncService()
            item = CustomItem(
                media_type="episode",
                title="番剧",
                ori_title="",
                season=1,
                episode=1,
                release_date="2024-01-01",
                user_name="test_user",
            )
            r = svc.sync_movie_watching(item, source="custom")
        assert r.status == "ignored"
        assert "仅剧场版" in r.message

    def test_sync_movie_watching_empty_title(self):
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = self._cfg_get()
            from app.services.sync_service import SyncService

            svc = SyncService()
            r = svc.sync_movie_watching(self._movie_item(title=""), source="custom")
        assert r.status == "error"
        assert "名称为空" in r.message

    def test_sync_movie_watching_no_permission(self):
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = self._cfg_get()
            mock_config.get_single_mode_media_usernames.return_value = ["other_user"]
            from app.services.sync_service import SyncService

            svc = SyncService()
            r = svc.sync_movie_watching(self._movie_item(), source="custom")
        assert r.status == "error"
        assert "无权限" in r.message

    def test_sync_movie_watching_blocked(self):
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = self._cfg_get(blocked_keywords="广告,跳过")
            mock_config.get_single_mode_media_usernames.return_value = ["test_user"]
            from app.services.sync_service import SyncService

            svc = SyncService()
            r = svc.sync_movie_watching(
                self._movie_item(title="内含广告词的剧场版"),
                source="custom",
            )
        assert r.status == "ignored"
        assert "屏蔽" in r.message

    def test_sync_movie_watching_subject_not_found(self):
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager") as mock_db,
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = self._cfg_get()
            mock_config.get_single_mode_media_usernames.return_value = ["test_user"]
            from app.services.sync_service import SyncService

            svc = SyncService()
            with patch.object(
                svc,
                "_find_subject_id",
                return_value=(None, False, "Bangumi 搜索无结果"),
            ):
                r = svc.sync_movie_watching(self._movie_item(), source="custom")
        assert r.status == "error"
        assert "未找到" in r.message
        mock_db.log_sync_record.assert_called_once()
        _kw = mock_db.log_sync_record.call_args[1]
        assert _kw.get("status") == "error"
        assert "未查询到番剧信息，跳过" in (_kw.get("message") or "")
        assert "Bangumi 搜索无结果" in (_kw.get("message") or "")

    def test_sync_movie_watching_no_bgm_api(self):
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = self._cfg_get()
            mock_config.get_single_mode_media_usernames.return_value = ["test_user"]
            from app.services.sync_service import SyncService

            svc = SyncService()
            with patch.object(svc, "_find_subject_id", return_value=("888", False, "")):
                with patch.object(svc, "_get_bangumi_api_for_user", return_value=None):
                    r = svc.sync_movie_watching(self._movie_item(), source="custom")
        assert r.status == "error"
        assert "bangumi" in r.message.lower()

    def test_sync_movie_watching_ensure_auth_value_error(self):
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = self._cfg_get()
            mock_config.get_single_mode_media_usernames.return_value = ["test_user"]
            from app.services.sync_service import SyncService

            svc = SyncService()
            bgm = MagicMock()
            bgm.ensure_subject_watching.side_effect = ValueError(
                "认证失败: access_token 无效"
            )
            with patch.object(svc, "_find_subject_id", return_value=("888", False, "")):
                with patch.object(svc, "_get_bangumi_api_for_user", return_value=bgm):
                    r = svc.sync_movie_watching(self._movie_item(), source="custom")
        assert r.status == "error"
        assert "认证" in r.message

    def test_sync_movie_watching_ensure_other_value_error_wrapped(self):
        """非认证类 ValueError 重新抛出后由外层捕获为处理失败"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = self._cfg_get()
            mock_config.get_single_mode_media_usernames.return_value = ["test_user"]
            from app.services.sync_service import SyncService

            svc = SyncService()
            bgm = MagicMock()
            bgm.ensure_subject_watching.side_effect = ValueError("other reason")
            with patch.object(svc, "_find_subject_id", return_value=("888", False, "")):
                with patch.object(svc, "_get_bangumi_api_for_user", return_value=bgm):
                    r = svc.sync_movie_watching(self._movie_item(), source="custom")
        assert r.status == "error"
        assert "处理失败" in r.message
        assert "other reason" in r.message

    def test_sync_movie_watching_ensure_returns_zero_success(self):
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager") as mock_db,
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = self._cfg_get()
            mock_config.get_single_mode_media_usernames.return_value = ["test_user"]
            from app.services.sync_service import SyncService

            svc = SyncService()
            bgm = MagicMock()
            bgm.ensure_subject_watching.return_value = 0
            with patch.object(svc, "_find_subject_id", return_value=("888", False, "")):
                with patch.object(svc, "_get_bangumi_api_for_user", return_value=bgm):
                    r = svc.sync_movie_watching(self._movie_item(), source="custom")
        assert r.status == "success"
        assert "已在看" in r.message or "已看过" in r.message
        mock_db.log_sync_record.assert_called()

    def test_sync_movie_watching_ensure_unexpected_error(self):
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager") as mock_db,
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = self._cfg_get()
            mock_config.get_single_mode_media_usernames.return_value = ["test_user"]
            from app.services.sync_service import SyncService

            svc = SyncService()
            bgm = MagicMock()
            bgm.ensure_subject_watching.side_effect = RuntimeError("network")
            with patch.object(svc, "_find_subject_id", return_value=("888", False, "")):
                with patch.object(svc, "_get_bangumi_api_for_user", return_value=bgm):
                    r = svc.sync_movie_watching(self._movie_item(), source="custom")
        assert r.status == "error"
        assert "处理失败" in r.message
        mock_db.log_sync_record.assert_called()


class TestPlexSync:
    """测试 Plex 同步"""

    def test_sync_plex_item_ignored_event(self):
        """测试忽略非 scrobble 事件"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
            patch("app.services.sync_service.extract_plex_data"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            plex_data = {
                "event": "media.play",  # 不是 scrobble
                "Account": {"title": "test_user"},
                "Metadata": {},
            }

            result = service.sync_plex_item(plex_data)

            assert result.status == "ignored"

    def test_sync_plex_media_play_movie_delegates(self):
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.models.sync import SyncResponse
            from app.services.sync_service import SyncService

            service = SyncService()
            plex_data = {
                "event": "media.play",
                "Account": {"title": "test_user"},
                "Metadata": {
                    "type": "movie",
                    "title": "Film",
                    "originallyAvailableAt": "2024-06-01",
                },
            }
            with patch.object(service, "sync_movie_watching") as mock_watch:
                mock_watch.return_value = SyncResponse(
                    status="success", message="ok", data={}
                )
                result = service.sync_plex_item(plex_data)
            assert result.status == "success"
            mock_watch.assert_called_once()
            assert mock_watch.call_args[0][0].media_type == "movie"


class TestEmbySync:
    """测试 Emby 同步"""

    def test_sync_emby_item_missing_field(self):
        """测试缺少必需字段"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            # 缺少 User 字段
            emby_data = {
                "Event": "item.markplayed",
                "Item": {},
            }

            result = service.sync_emby_item(emby_data)

            assert result.status == "error"

    def test_sync_emby_item_ignored_event(self):
        """测试忽略非标记播放事件"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            emby_data = {
                "Event": "item.added",  # 不是标记播放
                "User": {"Name": "test_user"},
                "Item": {
                    "Type": "Episode",
                    "SeriesName": "Test",
                    "ParentIndexNumber": 1,
                    "IndexNumber": 1,
                },
            }

            result = service.sync_emby_item(emby_data)

            assert result.status == "ignored"

    def test_sync_emby_playback_start_movie_delegates(self):
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.models.sync import SyncResponse
            from app.services.sync_service import SyncService

            service = SyncService()
            emby_data = {
                "Event": "playback.start",
                "User": {"Name": "test_user"},
                "Item": {
                    "Type": "Movie",
                    "Name": "Film",
                    "PremiereDate": "2024-06-01T00:00:00.0000000Z",
                },
            }
            with patch.object(service, "sync_movie_watching") as mock_watch:
                mock_watch.return_value = SyncResponse(
                    status="success", message="ok", data={}
                )
                result = service.sync_emby_item(emby_data)
            assert result.status == "success"
            mock_watch.assert_called_once()
            assert mock_watch.call_args[0][0].media_type == "movie"

    def test_sync_emby_playback_start_pascal_case_ignored(self):
        """Emby 仅认 playback.start，不认 PlaybackStart"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()
            emby_data = {
                "Event": "PlaybackStart",
                "User": {"Name": "test_user"},
                "Item": {
                    "Type": "Movie",
                    "Name": "Film",
                },
            }
            with patch.object(service, "sync_movie_watching") as mock_watch:
                r = service.sync_emby_item(emby_data)
            assert r.status == "ignored"
            mock_watch.assert_not_called()


class TestJellyfinSync:
    """测试 Jellyfin 同步"""

    def test_sync_jellyfin_item_ignored_event(self):
        """测试忽略非播放停止事件"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            jellyfin_data = {
                "NotificationType": "PlaybackStart",  # 不是停止
                "PlayedToCompletion": "True",
            }

            result = service.sync_jellyfin_item(jellyfin_data)

            assert result.status == "ignored"

    def test_sync_jellyfin_missing_notification_type_ignored(self):
        """无 NotificationType 时视为非 Stop / 非播放开始电影，忽略"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()
            r = service.sync_jellyfin_item({"media_type": "Movie"})
            assert r.status == "ignored"

    def test_sync_jellyfin_playback_stop_missing_played_to_completion_error(self):
        """PlaybackStop 缺少 PlayedToCompletion 时进入异常处理"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()
            r = service.sync_jellyfin_item({"NotificationType": "PlaybackStop"})
            assert r.status == "error"
            assert "处理失败" in r.message

    def test_sync_jellyfin_playback_start_movie_delegates(self):
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.models.sync import SyncResponse
            from app.services.sync_service import SyncService

            service = SyncService()
            jellyfin_data = {
                "NotificationType": "PlaybackStart",
                "media_type": "Movie",
                "title": "Film",
                "ori_title": " ",
                "season": 1,
                "episode": 1,
                "release_date": "2024-01-01",
                "user_name": "test_user",
            }
            with patch.object(service, "sync_movie_watching") as mock_watch:
                mock_watch.return_value = SyncResponse(
                    status="success", message="ok", data={}
                )
                result = service.sync_jellyfin_item(jellyfin_data)
            assert result.status == "success"
            mock_watch.assert_called_once()
            call_item = mock_watch.call_args[0][0]
            assert call_item.media_type == "movie"
            assert call_item.title == "Film"

    def test_sync_jellyfin_item_not_completed(self):
        """测试未播放完成"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            jellyfin_data = {
                "NotificationType": "PlaybackStop",
                "PlayedToCompletion": "False",  # 未播放完成
            }

            result = service.sync_jellyfin_item(jellyfin_data)

            assert result.status == "ignored"
