"""
SyncService 更多测试
"""

import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

# 确保可以导入 app 模块
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from app.models.sync import CustomItem


@contextmanager
def patched_sync_deps():
    with patch("app.services.sync_service.config_manager") as mock_cfg:
        with patch("app.services.sync_service.database_manager"):
            with patch("app.services.sync_service.send_notify"):
                with patch("app.services.sync_service.mapping_service"):
                    yield mock_cfg


def _branch_custom_item(**kwargs):
    defaults = dict(
        user_name="testuser",
        title="番剧A",
        ori_title="A",
        season=1,
        episode=1,
        media_type="episode",
        release_date="2024-01-15",
        source=None,
    )
    defaults.update(kwargs)
    return CustomItem(**defaults)


class TestSyncServiceHelperMethods:
    """测试 SyncService 辅助方法"""

    def test_check_user_permission_single_mode(self):
        """测试单用户模式权限检查"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "mode"): "single",
            }.get((section, key), fallback)
            mock_config.get_single_mode_media_usernames.return_value = ["admin"]

            from app.services.sync_service import SyncService

            service = SyncService()

            # 测试有权限的用户
            result = service._check_user_permission("admin")
            assert result is True

            # 测试无权限的用户
            result = service._check_user_permission("other_user")
            assert result is False

    def test_check_user_permission_single_mode_comma_separated(self):
        """单用户模式 media_server_username 逗号分隔时多个名均通过"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "mode"): "single",
            }.get((section, key), fallback)
            mock_config.get_single_mode_media_usernames.return_value = [
                "plex_u",
                "emby_u",
            ]
            from app.services.sync_service import SyncService

            service = SyncService()
            assert service._check_user_permission("plex_u") is True
            assert service._check_user_permission("emby_u") is True
            assert service._check_user_permission("other") is False

    def test_check_user_permission_single_mode_missing_media_usernames(self):
        """单用户模式未配置 media_server_username（解析结果为空）时拒绝"""
        with patched_sync_deps() as cfg:

            def get_side_effect(section, key, fallback=None):
                if section == "sync" and key == "mode":
                    return "single"
                return fallback

            cfg.get.side_effect = get_side_effect
            cfg.get_single_mode_media_usernames.return_value = []
            from app.services.sync_service import SyncService

            svc = SyncService()
            assert svc._check_user_permission("anyone") is False

    def test_check_user_permission_multi_mode_user_not_in_mappings(self):
        with patched_sync_deps() as cfg:

            def get_side_effect(section, key, fallback=None):
                if section == "sync" and key == "mode":
                    return "multi"
                return fallback

            cfg.get.side_effect = get_side_effect
            cfg.get_user_mappings.return_value = {}
            from app.services.sync_service import SyncService

            svc = SyncService()
            assert svc._check_user_permission("ghost") is False

    def test_check_user_permission_multi_mode_missing_bangumi_section(self):
        with patched_sync_deps() as cfg:

            def get_side_effect(section, key, fallback=None):
                if section == "sync" and key == "mode":
                    return "multi"
                return fallback

            cfg.get.side_effect = get_side_effect
            cfg.get_user_mappings.return_value = {"u1": "missing_section"}
            cfg.get_bangumi_configs.return_value = {}
            from app.services.sync_service import SyncService

            svc = SyncService()
            assert svc._check_user_permission("u1") is False

    def test_check_user_permission_unknown_mode_returns_false(self):
        with patched_sync_deps() as cfg:

            def get_side_effect(section, key, fallback=None):
                if section == "sync" and key == "mode":
                    return "weird"
                return fallback

            cfg.get.side_effect = get_side_effect
            from app.services.sync_service import SyncService

            svc = SyncService()
            assert svc._check_user_permission("u") is False

    def test_is_title_blocked_empty_keywords(self):
        """测试空屏蔽关键词"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "blocked_keywords"): "",
            }.get((section, key), fallback)

            from app.services.sync_service import SyncService

            service = SyncService()

            result = service._is_title_blocked("测试番剧", "")
            assert result is False

    def test_is_title_blocked_with_keywords(self):
        """测试有屏蔽关键词"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "blocked_keywords"): "hentai,18+,adult",
            }.get((section, key), fallback)

            from app.services.sync_service import SyncService

            service = SyncService()

            # 测试标题包含屏蔽词
            result = service._is_title_blocked("测试 hentai 番剧", "")
            assert result is True

            # 测试原标题包含屏蔽词
            result = service._is_title_blocked("测试", "adult video")
            assert result is True

            # 测试正常标题
            result = service._is_title_blocked("正常番剧", "")
            assert result is False

    def test_get_bangumi_config_for_user(self):
        """测试获取用户 bangumi 配置"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("bangumi-testuser", "username"): "bgm_user",
                ("bangumi-testuser", "access_token"): "test_token",
                ("bangumi-data", "enabled"): False,
            }.get((section, key), fallback)

            from app.services.sync_service import SyncService

            service = SyncService()

            result = service._get_bangumi_config_for_user("testuser")
            assert result is not None

    def test_is_title_blocked_keywords_only_commas_returns_false(self):
        with patched_sync_deps() as cfg:
            cfg.get.return_value = "  ,  ,  "
            from app.services.sync_service import SyncService

            svc = SyncService()
            assert svc._is_title_blocked("任何标题", "副标题") is False

    def test_get_bangumi_api_incomplete_config_returns_none(self):
        with patched_sync_deps() as cfg:

            def get_side_effect(section, key, fallback=None):
                if section == "sync" and key == "mode":
                    return "single"
                return fallback

            cfg.get.side_effect = get_side_effect
            cfg.get_user_mappings.return_value = {}
            from app.services.sync_service import SyncService

            svc = SyncService()
            with patch.object(
                svc,
                "_get_bangumi_config_for_user",
                return_value={"username": "", "access_token": "t", "private": False},
            ):
                assert svc._get_bangumi_api_for_user("u") is None

    def test_get_bangumi_data_uses_singleton_cache(self):
        with patched_sync_deps():
            from app.services.sync_service import SyncService

            svc = SyncService()
            svc._bangumi_data_cache = None
            m = MagicMock()
            with patch("app.services.sync_service.bangumi_data", m):
                assert svc._get_bangumi_data() is m
                assert svc._get_bangumi_data() is m


class TestPlexSync:
    """测试 Plex 同步功能"""

    def test_sync_plex_item_not_scrobble(self):
        """测试非 scrobble 事件跳过"""
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
                "event": "media.rate",  # 不是 scrobble
                "Account": {"title": "test_user"},
            }

            result = service.sync_plex_item(plex_data)

            assert result.status == "ignored"
            assert "无需同步" in result.message

    def test_sync_plex_item_sync_failure_records_task_failed(self):
        with patched_sync_deps():
            from app.services.sync_service import SyncService

            svc = SyncService()
            tid = "plex_x"
            svc._sync_tasks[tid] = {
                "status": "pending",
                "item": {},
                "source": "plex",
                "created_at": 0.0,
                "result": None,
                "error": None,
            }
            with patch.object(
                svc, "sync_plex_item", side_effect=ValueError("plex bad")
            ):
                out = svc._sync_plex_item_sync({"event": "media.scrobble"}, tid)
            assert out.status == "error"
            assert svc._sync_tasks[tid]["status"] == "failed"

    def test_sync_plex_item_extract_raises_returns_error(self):
        with patched_sync_deps():
            with patch(
                "app.services.sync_service.extract_plex_data",
                side_effect=RuntimeError("parse"),
            ):
                from app.services.sync_service import SyncService

                svc = SyncService()
                plex = {
                    "event": "media.scrobble",
                    "Account": {"title": "u"},
                    "Metadata": {
                        "parentIndex": 1,
                        "index": 1,
                        "grandparentTitle": "G",
                    },
                }
                r = svc.sync_plex_item(plex_data=plex)
            assert r.status == "error"

    def test_sync_plex_item_movie_reaches_extract_and_sync(self):
        """电影 scrobble 不应因日志访问 grandparentTitle 等剧集字段而崩溃"""
        movie_item = CustomItem(
            media_type="movie",
            title="剧场版",
            ori_title=None,
            season=1,
            episode=1,
            release_date="",
            user_name="u",
            source="plex",
        )
        with patched_sync_deps():
            with patch(
                "app.services.sync_service.extract_plex_data", return_value=movie_item
            ) as ex:
                from app.models.sync import SyncResponse
                from app.services.sync_service import SyncService

                svc = SyncService()
                with patch.object(
                    svc,
                    "sync_custom_item",
                    return_value=SyncResponse(status="success", message="ok"),
                ) as sc:
                    plex = {
                        "event": "media.scrobble",
                        "Account": {"title": "u"},
                        "Metadata": {
                            "type": "movie",
                            "title": "剧场版 XYZ",
                        },
                    }
                    r = svc.sync_plex_item(plex_data=plex)
        assert ex.called
        assert sc.called
        assert r.status == "success"
        assert r.message == "ok"


class TestEmbySync:
    """测试 Emby 同步功能"""

    def test_sync_emby_item_missing_field(self):
        """测试缺少字段"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            emby_data = {
                "Event": "item.markplayed",
                # 缺少 Item 字段
            }

            result = service.sync_emby_item(emby_data)

            assert result.status == "error"
            assert "缺少" in result.message

    def test_sync_emby_item_wrong_event(self):
        """测试错误事件类型"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            emby_data = {
                "Event": "item.download",  # 错误的事件
                "Item": {
                    "Type": "Episode",
                    "SeriesName": "Test",
                    "ParentIndexNumber": 1,
                    "IndexNumber": 1,
                },
                "User": {"Id": "123"},
            }

            result = service.sync_emby_item(emby_data)

            assert result.status == "ignored"

    def test_sync_emby_item_missing_item_field(self):
        """测试 Item 缺少字段"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            emby_data = {
                "Event": "item.markplayed",
                "Item": {
                    "Type": "Episode",
                    # 缺少 SeriesName
                },
            }

            result = service.sync_emby_item(emby_data)

            assert result.status == "error"

    def test_sync_emby_playback_stop_incomplete_playback_info_ignored(self):
        with patched_sync_deps():
            from app.services.sync_service import SyncService

            svc = SyncService()
            payload = {
                "Event": "playback.stop",
                "Item": {
                    "Type": "Episode",
                    "SeriesName": "S",
                    "ParentIndexNumber": 1,
                    "IndexNumber": 1,
                },
                "User": {"Id": "1"},
            }
            r = svc.sync_emby_item(payload)
            assert r.status == "ignored"
            assert "不完整" in r.message

    def test_sync_emby_playback_stop_not_completed_ignored(self):
        with patched_sync_deps():
            from app.services.sync_service import SyncService

            svc = SyncService()
            payload = {
                "Event": "playback.stop",
                "Item": {
                    "Type": "Episode",
                    "SeriesName": "S",
                    "ParentIndexNumber": 1,
                    "IndexNumber": 2,
                },
                "User": {"Id": "1"},
                "PlaybackInfo": {"PlayedToCompletion": False},
            }
            r = svc.sync_emby_item(payload)
            assert r.status == "ignored"
            assert "未播放完成" in r.message

    def test_sync_emby_item_extract_raises_returns_error(self):
        with patched_sync_deps():
            with patch(
                "app.services.sync_service.extract_emby_data",
                side_effect=OSError("emby ex"),
            ):
                from app.services.sync_service import SyncService

                svc = SyncService()
                payload = {
                    "Event": "item.markplayed",
                    "Item": {
                        "Type": "Episode",
                        "SeriesName": "S",
                        "ParentIndexNumber": 1,
                        "IndexNumber": 1,
                    },
                    "User": {"Id": "1"},
                }
                r = svc.sync_emby_item(payload)
            assert r.status == "error"

    def test_sync_emby_item_sync_failure_records_task_failed(self):
        with patched_sync_deps():
            from app.services.sync_service import SyncService

            svc = SyncService()
            tid = "emby_t"
            svc._sync_tasks[tid] = {
                "status": "pending",
                "item": {},
                "source": "emby",
                "created_at": 0.0,
                "result": None,
                "error": None,
            }
            with patch.object(svc, "sync_emby_item", side_effect=KeyError("k")):
                out = svc._sync_emby_item_sync({}, tid)
            assert out.status == "error"
            assert svc._sync_tasks[tid]["status"] == "failed"


class TestJellyfinSync:
    """测试 Jellyfin 同步功能"""

    def test_sync_jellyfin_item_not_stop(self):
        """测试非停止事件跳过"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
            patch("app.services.sync_service.extract_jellyfin_data"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            jellyfin_data = {
                "NotificationType": "PlaybackStart",  # 不是停止
            }

            result = service.sync_jellyfin_item(jellyfin_data)

            assert result.status == "ignored"

    def test_sync_jellyfin_item_not_completed(self):
        """测试未播放完成跳过"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
            patch("app.services.sync_service.extract_jellyfin_data"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            jellyfin_data = {
                "NotificationType": "PlaybackStop",
                "PlayedToCompletion": "False",  # 未播放完成
            }

            result = service.sync_jellyfin_item(jellyfin_data)

            assert result.status == "ignored"

    def test_sync_jellyfin_item_extract_raises_returns_error(self):
        with patched_sync_deps():
            with patch(
                "app.services.sync_service.extract_jellyfin_data",
                side_effect=ValueError("jf"),
            ):
                from app.services.sync_service import SyncService

                svc = SyncService()
                jf = {
                    "NotificationType": "PlaybackStop",
                    "PlayedToCompletion": "True",
                }
                r = svc.sync_jellyfin_item(jf)
            assert r.status == "error"

    def test_sync_jellyfin_item_sync_failure_records_task_failed(self):
        with patched_sync_deps():
            from app.services.sync_service import SyncService

            svc = SyncService()
            tid = "jf_t"
            svc._sync_tasks[tid] = {
                "status": "pending",
                "item": {},
                "source": "jellyfin",
                "created_at": 0.0,
                "result": None,
                "error": None,
            }
            with patch.object(svc, "sync_jellyfin_item", side_effect=RuntimeError("x")):
                out = svc._sync_jellyfin_item_sync({}, tid)
            assert out.status == "error"
            assert svc._sync_tasks[tid]["status"] == "failed"


class TestAsyncMethods:
    """测试异步方法"""

    @pytest.mark.asyncio
    async def test_sync_custom_item_async(self):
        """测试异步自定义同步"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
            patch(
                "app.services.sync_service.SyncService.sync_custom_item"
            ) as mock_sync,
        ):
            mock_sync.return_value = MagicMock(
                status="success",
                message="同步成功",
                dict=lambda: {"status": "success", "message": "同步成功"},
            )

            from app.services.sync_service import SyncService

            service = SyncService()

            from app.models.sync import CustomItem

            item = CustomItem(
                media_type="episode",
                title="Test",
                season=1,
                episode=1,
                release_date="2024-01-01",
                user_name="test",
            )

            task_id = await service.sync_custom_item_async(item, "custom")
            assert task_id is not None
            assert "_" in task_id

    @pytest.mark.asyncio
    async def test_sync_plex_item_async(self):
        """测试异步 Plex 同步"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.send_notify"),
            patch("app.services.sync_service.mapping_service"),
            patch("app.services.sync_service.extract_plex_data") as mock_extract,
            patch(
                "app.services.sync_service.SyncService.sync_custom_item"
            ) as mock_sync,
        ):
            mock_extract.return_value = MagicMock(
                media_type="episode",
                title="Test",
                season=1,
                episode=1,
                release_date="",
                user_name="test",
            )
            mock_sync.return_value = MagicMock(
                status="success", message="同步成功", dict=lambda: {"status": "success"}
            )

            from app.services.sync_service import SyncService

            service = SyncService()

            plex_data = {"event": "media.scrobble"}
            task_id = await service.sync_plex_item_async(plex_data)
            assert task_id is not None

    def test_sync_custom_item_sync_records_failure_on_inner_error(self):
        with patched_sync_deps():
            from app.services.sync_service import SyncService

            svc = SyncService()
            item = _branch_custom_item()
            tid = "manual_task"
            svc._sync_tasks[tid] = {
                "status": "pending",
                "item": item.model_dump(mode="python"),
                "source": "custom",
                "created_at": 0.0,
                "result": None,
                "error": None,
            }
            with patch.object(
                svc, "sync_custom_item", side_effect=RuntimeError("inner boom")
            ):
                out = svc._sync_custom_item_sync(item, "custom", tid)
            assert out.status == "error"
            assert "异步处理失败" in out.message
            assert svc._sync_tasks[tid]["status"] == "failed"
            assert "inner boom" in svc._sync_tasks[tid]["error"]
