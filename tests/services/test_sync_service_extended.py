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
            with patch("app.services.sync_service.notification_service"):
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
            patch("app.services.sync_service.notification_service"),
            patch("app.services.sync_service.mapping_service"),
            patch(
                "app.core.accounts.list_bangumi_accounts",
                return_value=[{"section_name": "bangumi"}],
            ),
            patch(
                "app.core.accounts.get_single_mode_media_usernames",
                return_value=["admin"],
            ),
        ):
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "mode"): "single",
            }.get((section, key), fallback)
            mock_config.get_single_mode_media_usernames.return_value = ["admin"]

            from app.services.sync_service import SyncService

            service = SyncService()

            # 测试有权限的用户
            allowed, msg = service._check_user_permission("admin")
            assert allowed is True
            assert msg == ""

            # 测试无权限的用户
            allowed, msg = service._check_user_permission("other_user")
            assert allowed is False
            assert "other_user" in msg

    def test_check_user_permission_single_mode_comma_separated(self):
        """单用户模式 media_server_username 逗号分隔时多个名均通过"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.notification_service"),
            patch("app.services.sync_service.mapping_service"),
            patch(
                "app.core.accounts.list_bangumi_accounts",
                return_value=[{"section_name": "bangumi"}],
            ),
            patch(
                "app.core.accounts.get_single_mode_media_usernames",
                return_value=["plex_u", "emby_u"],
            ),
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
            assert service._check_user_permission("plex_u")[0] is True
            assert service._check_user_permission("emby_u")[0] is True
            assert service._check_user_permission("other")[0] is False

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
            allowed, msg = svc._check_user_permission("anyone")
            assert allowed is False
            assert "media_server_username" in msg

    def test_check_user_permission_single_mode_enabled_account(self):
        """单账号启用时权限检查不受停用分支影响"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.notification_service"),
            patch("app.services.sync_service.mapping_service"),
            patch(
                "app.core.accounts.list_bangumi_accounts",
                return_value=[{"section_name": "bangumi", "enabled": True}],
            ),
            patch(
                "app.core.accounts.get_single_mode_media_usernames",
                return_value=["admin"],
            ),
        ):
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "mode"): "single",
            }.get((section, key), fallback)
            from app.services.sync_service import SyncService

            service = SyncService()
            allowed, msg = service._check_user_permission("admin")
            assert allowed is True
            assert msg == ""

    def test_check_user_permission_single_mode_disabled_account(self):
        """单账号被停用时权限检查直接短路，避免继续执行匹配流程"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.notification_service"),
            patch("app.services.sync_service.mapping_service"),
            patch(
                "app.core.accounts.list_bangumi_accounts",
                return_value=[{"section_name": "bangumi", "enabled": False}],
            ),
            patch(
                "app.core.accounts.get_single_mode_media_usernames",
                return_value=["admin"],
            ),
        ):
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "mode"): "single",
            }.get((section, key), fallback)
            from app.services.sync_service import SyncService

            service = SyncService()
            allowed, msg = service._check_user_permission("admin")
            assert allowed is False
            assert "已停用" in msg

    def test_check_user_permission_multi_mode_disabled_accounts(self):
        """多用户模式下账号全部停用时，拒绝原因指向账号停用"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.notification_service"),
            patch("app.services.sync_service.mapping_service"),
            patch(
                "app.core.accounts.list_bangumi_accounts",
                return_value=[
                    {"section_name": "bangumi", "enabled": False},
                    {"section_name": "bangumi-2", "enabled": False},
                ],
            ),
            patch(
                "app.core.accounts.get_user_mappings",
                return_value={"Elegy233": "bangumi"},
            ),
            patch("app.core.accounts.get_bangumi_config_for_user", return_value=None),
        ):
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "mode"): "multi",
            }.get((section, key), fallback)
            from app.services.sync_service import SyncService

            service = SyncService()
            allowed, msg = service._check_user_permission("Elegy233")
            assert allowed is False
            assert "已停用" in msg

    def test_check_user_permission_multi_mode_user_not_in_mappings(self):
        with patched_sync_deps() as cfg:

            def get_side_effect(section, key, fallback=None):
                if section == "sync" and key == "mode":
                    return "multi"
                return fallback

            cfg.get.side_effect = get_side_effect
            cfg.get_user_mappings.return_value = {}
            from app.services.sync_service import SyncService

            with (
                patch(
                    "app.core.accounts.list_bangumi_accounts",
                    return_value=[
                        {"section_name": "bangumi-a"},
                        {"section_name": "bangumi-b"},
                    ],
                ),
                patch("app.core.accounts.get_user_mappings", return_value={}),
            ):
                svc = SyncService()
                allowed, msg = svc._check_user_permission("ghost")
                assert allowed is False
                assert "ghost" in msg

    def test_check_user_permission_multi_mode_missing_bangumi_section(self):
        with patched_sync_deps() as cfg:

            def get_side_effect(section, key, fallback=None):
                if section == "sync" and key == "mode":
                    return "multi"
                return fallback

            cfg.get.side_effect = get_side_effect
            cfg.get_user_mappings.return_value = {"u1": "missing_section"}
            cfg.get_bangumi_configs.return_value = {}
            cfg.get_primary_bangumi_config.return_value = None
            from app.services.sync_service import SyncService

            with (
                patch(
                    "app.core.accounts.list_bangumi_accounts",
                    return_value=[
                        {"section_name": "bangumi-a"},
                        {"section_name": "bangumi-b"},
                    ],
                ),
                patch(
                    "app.core.accounts.get_user_mappings",
                    return_value={"u1": "missing_section"},
                ),
                patch(
                    "app.core.accounts.get_bangumi_config_for_user",
                    return_value=None,
                ),
            ):
                svc = SyncService()
                allowed, msg = svc._check_user_permission("u1")
                assert allowed is False
                assert "u1" in msg

    def test_check_user_permission_test_source_skip(self):
        """测试来源 + test_skip_permission_check=True 时跳过校验"""
        with patched_sync_deps() as cfg:

            def get_side_effect(section, key, fallback=None):
                if section == "sync" and key == "mode":
                    return "single"
                if section == "sync" and key == "test_skip_permission_check":
                    return True
                return fallback

            cfg.get.side_effect = get_side_effect
            cfg.get_single_mode_media_usernames.return_value = []
            from app.services.sync_service import SyncService

            svc = SyncService()
            # test 来源跳过
            allowed, msg = svc._check_user_permission("anyone", source="test")
            assert allowed is True
            assert msg == ""
            # test-match 来源跳过
            allowed, msg = svc._check_user_permission("anyone", source="test-match")
            assert allowed is True
            # fongmi-debug 来源跳过
            allowed, msg = svc._check_user_permission("anyone", source="fongmi-debug")
            assert allowed is True
            # 生产来源不跳过
            allowed, msg = svc._check_user_permission("anyone", source="custom")
            assert allowed is False

    def test_check_user_permission_test_source_not_skipped_by_default(self):
        """test_skip_permission_check=False（默认）时测试来源也不跳过"""
        with patched_sync_deps() as cfg:

            def get_side_effect(section, key, fallback=None):
                if section == "sync" and key == "mode":
                    return "single"
                return fallback

            cfg.get.side_effect = get_side_effect
            cfg.get_single_mode_media_usernames.return_value = ["admin"]
            from app.services.sync_service import SyncService

            svc = SyncService()
            # test 来源但未开启跳过，仍按权限校验
            allowed, _ = svc._check_user_permission("other", source="test")
            assert allowed is False

    def test_is_title_blocked_empty_keywords(self):
        """测试无屏蔽关键词（DB 为空）"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager") as mock_db,
            patch("app.services.sync_service.notification_service"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "mode"): "single",
            }.get((section, key), fallback)
            # 屏蔽关键词已迁至 DB：空表 → 永不命中
            mock_db.match_blocked_keyword.return_value = ""

            from app.services.sync_service import SyncService

            service = SyncService()

            result = service._is_title_blocked("测试番剧", "")
            assert result is False

    def test_is_title_blocked_with_keywords(self):
        """测试有屏蔽关键词（DB 命中）"""
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.database_manager") as mock_db,
            patch("app.services.sync_service.notification_service"),
            patch("app.services.sync_service.mapping_service"),
        ):
            mock_config.get.side_effect = lambda section, key, fallback=None: {
                ("sync", "mode"): "single",
            }.get((section, key), fallback)
            # 模拟 DB 中的关键词集合：大小写不敏感子串匹配
            _kws = ("hentai", "18+", "adult")

            def _match(*titles):
                for t in titles:
                    low = (t or "").lower()
                    for k in _kws:
                        if k in low:
                            return k
                return ""

            mock_db.match_blocked_keyword.side_effect = _match

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
            patch("app.services.sync_service.notification_service"),
            patch("app.services.sync_service.mapping_service"),
            patch(
                "app.core.accounts.get_bangumi_config_for_user",
                return_value={
                    "username": "bgm_user",
                    "access_token": "test_token",
                    "private": False,
                },
            ),
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

    def test_bangumi_api_cache_reuses_instance(self):
        """同一用户多次调用应复用同一 BangumiApi 实例"""
        with patched_sync_deps() as cfg:
            cfg.get.side_effect = lambda s, k, fallback=None: fallback
            from app.services.sync_service import SyncService

            svc = SyncService()
            svc._bangumi_api_cache.clear()

            # Mock BangumiApi 构造，验证只被调用一次
            with patch.object(svc, "_get_bangumi_config_for_user") as mock_cfg:
                mock_cfg.return_value = {
                    "username": "user1",
                    "access_token": "token1",
                    "private": False,
                }
                with patch("app.services.sync_service.BangumiApi") as mock_cls:
                    mock_instance = MagicMock()
                    mock_cls.return_value = mock_instance

                    api1 = svc._get_bangumi_api_for_user("user_a")
                    api2 = svc._get_bangumi_api_for_user("user_a")

                    assert api1 is api2 is mock_instance
                    assert mock_cls.call_count == 1

    def test_bangumi_api_cache_invalidates_on_config_change(self):
        """用户配置变更（如 access_token 改了）应重建实例"""
        with patched_sync_deps() as cfg:
            cfg.get.side_effect = lambda s, k, fallback=None: fallback
            from app.services.sync_service import SyncService

            svc = SyncService()
            svc._bangumi_api_cache.clear()

            with patch.object(svc, "_get_bangumi_config_for_user") as mock_cfg:
                mock_cfg.return_value = {
                    "username": "user1",
                    "access_token": "token1",
                    "private": False,
                }
                with patch("app.services.sync_service.BangumiApi") as mock_cls:
                    instance1 = MagicMock()
                    instance2 = MagicMock()
                    mock_cls.side_effect = [instance1, instance2]

                    # 第一次调用：使用 token1
                    api1 = svc._get_bangumi_api_for_user("user_a")
                    assert api1 is instance1

                    # 配置变更：token 改了
                    mock_cfg.return_value = {
                        "username": "user1",
                        "access_token": "token2",  # 改了
                        "private": False,
                    }

                    # 第二次调用：应重建实例
                    api2 = svc._get_bangumi_api_for_user("user_a")
                    assert api2 is instance2
                    assert api1 is not api2
                    assert mock_cls.call_count == 2

    def test_bangumi_api_cache_separate_users(self):
        """不同用户的实例互不干扰"""
        with patched_sync_deps() as cfg:
            cfg.get.side_effect = lambda s, k, fallback=None: fallback
            from app.services.sync_service import SyncService

            svc = SyncService()
            svc._bangumi_api_cache.clear()

            with patch.object(svc, "_get_bangumi_config_for_user") as mock_cfg:
                mock_cfg.return_value = {
                    "username": "u",
                    "access_token": "t",
                    "private": False,
                }
                with patch("app.services.sync_service.BangumiApi") as mock_cls:
                    inst_a = MagicMock()
                    inst_b = MagicMock()
                    mock_cls.side_effect = [inst_a, inst_b]

                    api_a = svc._get_bangumi_api_for_user("user_a")
                    api_b = svc._get_bangumi_api_for_user("user_b")

                    assert api_a is inst_a
                    assert api_b is inst_b
                    assert api_a is not api_b
                    assert mock_cls.call_count == 2

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
                "app.services.plex.sync_service.extract_plex_data",
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
                "app.services.plex.sync_service.extract_plex_data",
                return_value=movie_item,
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
            patch("app.services.sync_service.notification_service"),
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

    def test_sync_emby_item_missing_item_field(self):
        """测试 Item 缺少字段"""
        with (
            patch("app.services.sync_service.config_manager"),
            patch("app.services.sync_service.database_manager"),
            patch("app.services.sync_service.notification_service"),
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
                "app.services.emby.sync_service.extract_emby_data",
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
            patch("app.services.sync_service.notification_service"),
            patch("app.services.sync_service.mapping_service"),
            patch("app.services.jellyfin.sync_service.extract_jellyfin_data"),
        ):
            from app.services.sync_service import SyncService

            service = SyncService()

            jellyfin_data = {
                "NotificationType": "PlaybackStart",  # 不是停止
            }

            result = service.sync_jellyfin_item(jellyfin_data)

            assert result.status == "ignored"

    def test_sync_jellyfin_item_extract_raises_returns_error(self):
        with patched_sync_deps():
            with patch(
                "app.services.jellyfin.sync_service.extract_jellyfin_data",
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
            patch("app.services.sync_service.notification_service"),
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
            patch("app.services.sync_service.notification_service"),
            patch("app.services.sync_service.mapping_service"),
            patch("app.services.plex.sync_service.extract_plex_data") as mock_extract,
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


# ── 多账号同步：同一媒体服务器用户名 → 多个 Bangumi 账号 ───────────────


class TestMultiAccountSyncFanOut:
    """同一媒体服务器用户名绑定多个 Bangumi 账号时的标记分发。"""

    @staticmethod
    def _item(user_name="Elegy233", media_type="episode"):
        from app.models.sync import CustomItem

        return CustomItem(
            user_name=user_name,
            title="Test Anime",
            season=1,
            episode=1,
            media_type=media_type,
            release_date="2024-01-01",
        )

    def test_mark_episode_for_other_accounts_marks_each_remaining(self, monkeypatch):
        """首选账号之外的其余账号各标记一次（一人多号 / 亲友共享场景）。"""
        from unittest.mock import MagicMock

        from app.services.sync_service import SyncService

        svc = SyncService()
        primary, other = MagicMock(), MagicMock()
        monkeypatch.setattr(
            svc,
            "_get_bangumi_account_targets_for_user",
            lambda user_name: [("bangumi", primary), ("bangumi-2", other)],
        )
        marked = []
        archived = []
        monkeypatch.setattr(
            svc,
            "_retry_mark_episode",
            lambda bgm, subject_id, ep_id, **kwargs: marked.append(bgm),
        )
        monkeypatch.setattr(
            svc,
            "_mark_subject_completed_if_needed",
            lambda item, bgm, subject_id, title: archived.append(bgm),
        )

        svc._mark_episode_for_other_accounts(self._item(), primary, "123", "456", "T")

        # 首选账号已由执行阶段管线标记，此处只补标记其余账号
        assert marked == [other]
        assert archived == [other]

    def test_mark_episode_for_other_accounts_single_account_is_noop(self, monkeypatch):
        """仅一个账号时不产生额外标记，避免重复写入同一账号。"""
        from unittest.mock import MagicMock

        from app.services.sync_service import SyncService

        svc = SyncService()
        primary = MagicMock()
        monkeypatch.setattr(
            svc,
            "_get_bangumi_account_targets_for_user",
            lambda user_name: [("bangumi", primary)],
        )
        marked = []
        archived = []
        monkeypatch.setattr(
            svc,
            "_retry_mark_episode",
            lambda bgm, subject_id, ep_id, **kwargs: marked.append(bgm),
        )
        monkeypatch.setattr(
            svc,
            "_mark_subject_completed_if_needed",
            lambda item, bgm, subject_id, title: archived.append(bgm),
        )

        svc._mark_episode_for_other_accounts(self._item(), primary, "123", "456", "T")

        assert marked == []
        assert archived == []

    def test_mark_episode_for_other_accounts_returns_per_account_results(
        self, monkeypatch
    ):
        """返回的每账号结果含配置段、用户名与状态，首选账号被跳过。"""
        from unittest.mock import MagicMock

        from app.services.sync_service import SyncService

        svc = SyncService()
        primary, other = MagicMock(), MagicMock()
        other.username = "alt-account"
        monkeypatch.setattr(
            svc,
            "_get_bangumi_account_targets_for_user",
            lambda user_name: [("bangumi", primary), ("bangumi-2", other)],
        )
        monkeypatch.setattr(svc, "_retry_mark_episode", lambda *a, **k: 1)
        monkeypatch.setattr(svc, "_mark_subject_completed_if_needed", lambda *a, **k: 1)

        results = svc._mark_episode_for_other_accounts(
            self._item(), primary, "123", "456", "T"
        )

        assert results == [
            {
                "section": "bangumi-2",
                "username": "alt-account",
                "status": "success",
                "mark_status": 1,
                "message": "已标记为看过",
            }
        ]

    def test_mark_episode_for_other_accounts_skipped_message(self, monkeypatch):
        """其余账号已看过时结果为跳过文案，可区分于新标记文案。"""
        from unittest.mock import MagicMock

        from app.services.sync_service import SyncService

        svc = SyncService()
        primary, other = MagicMock(), MagicMock()
        other.username = "alt-account"
        monkeypatch.setattr(
            svc,
            "_get_bangumi_account_targets_for_user",
            lambda user_name: [("bangumi", primary), ("bangumi-2", other)],
        )
        monkeypatch.setattr(svc, "_retry_mark_episode", lambda *a, **k: 0)
        monkeypatch.setattr(svc, "_mark_subject_completed_if_needed", lambda *a, **k: 1)

        results = svc._mark_episode_for_other_accounts(
            self._item(), primary, "123", "456", "T"
        )

        assert results[0]["mark_status"] == 0
        assert results[0]["message"] == "已看过，不再重复标记"

    def test_mark_episode_for_other_accounts_failure_isolated(self, monkeypatch):
        """其余账号标记失败只记录日志并记入失败结果，不向上抛出。"""
        from unittest.mock import MagicMock

        from app.services.sync_service import SyncService

        svc = SyncService()
        primary, other = MagicMock(), MagicMock()
        other.username = "alt-account"
        monkeypatch.setattr(
            svc,
            "_get_bangumi_account_targets_for_user",
            lambda user_name: [("bangumi", primary), ("bangumi-2", other)],
        )

        def boom(bgm, subject_id, ep_id, **kwargs):
            raise RuntimeError("API 不可达")

        monkeypatch.setattr(svc, "_retry_mark_episode", boom)

        results = svc._mark_episode_for_other_accounts(
            self._item(), primary, "123", "456", "T"
        )

        # 不向上抛出，主流程结果仍由首选账号决定
        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert "API 不可达" in results[0]["message"]

    def test_mark_movie_watching_for_other_accounts_returns_per_account_results(
        self, monkeypatch
    ):
        """剧场版其余账号共享在看标记，返回每账号结果。"""
        from unittest.mock import MagicMock

        from app.services.sync_service import SyncService

        svc = SyncService()
        primary, other = MagicMock(), MagicMock()
        other.username = "alt-account"
        other.ensure_subject_watching.return_value = 1
        monkeypatch.setattr(
            svc,
            "_get_bangumi_account_targets_for_user",
            lambda user_name: [("bangumi", primary), ("bangumi-2", other)],
        )

        results = svc._mark_movie_watching_for_other_accounts(
            self._item(media_type="movie"), primary, "123"
        )

        assert results == [
            {
                "section": "bangumi-2",
                "username": "alt-account",
                "status": "success",
                "mark_status": 1,
                "message": "已标记为在看",
            }
        ]

    def test_build_account_outcomes_marks_primary_first(self, monkeypatch):
        """组装结果首选在前并标记 primary，其余账号排在后面。"""
        from unittest.mock import MagicMock

        from app.services.sync_service import SyncService

        svc = SyncService()
        primary, other = MagicMock(), MagicMock()
        primary.username = "main-account"
        other.username = "alt-account"
        monkeypatch.setattr(
            svc,
            "_get_bangumi_account_targets_for_user",
            lambda user_name: [("bangumi", primary), ("bangumi-2", other)],
        )

        outcomes = svc._build_account_outcomes(
            self._item(),
            primary,
            [
                {
                    "section": "bangumi-2",
                    "username": "alt-account",
                    "status": "success",
                    "message": "",
                }
            ],
            "success",
        )

        assert outcomes[0]["primary"] is True
        assert outcomes[0]["username"] == "main-account"
        assert outcomes[1]["primary"] is False
        assert outcomes[1]["username"] == "alt-account"

    def test_get_bangumi_account_targets_for_user_returns_all_accounts(self):
        """同一用户名绑定多个账号时返回全部（配置段, 实例），首选与主流程同一对象。"""
        from unittest.mock import MagicMock, patch

        from app.services.sync_service import SyncService

        svc = SyncService()
        svc._bangumi_api_cache.clear()

        primary = MagicMock()
        secondary = MagicMock()
        with (
            patch(
                "app.core.accounts.get_bangumi_sections_for_user",
                return_value=["bangumi", "bangumi-944646"],
            ),
            patch.object(svc, "_get_bangumi_api_for_user", return_value=primary),
            patch.object(
                svc,
                "_get_bangumi_api_by_section",
                side_effect=lambda section: (
                    secondary if section == "bangumi-944646" else None
                ),
            ),
            patch("app.core.accounts.get_bangumi_config_by_section", return_value={}),
        ):
            targets = svc._get_bangumi_account_targets_for_user("Elegy233")

        assert [section for section, _api in targets] == ["bangumi", "bangumi-944646"]
        assert [api for _section, api in targets] == [primary, secondary]

    def test_get_bangumi_account_targets_for_user_distinct_media_user_returns_single(
        self,
    ):
        """不同任务场景：每个媒体服务器用户名只绑定一个账号，不取其余账号实例。"""
        from unittest.mock import MagicMock, patch

        from app.services.sync_service import SyncService

        svc = SyncService()
        primary = MagicMock()

        def should_not_run(section):
            raise AssertionError("不同任务场景不应取其余账号实例")

        with (
            patch(
                "app.core.accounts.get_bangumi_sections_for_user",
                return_value=["bangumi"],
            ),
            patch("app.core.accounts.get_bangumi_config_by_section", return_value={}),
            patch.object(svc, "_get_bangumi_api_for_user", return_value=primary),
            patch.object(
                svc, "_get_bangumi_api_by_section", side_effect=should_not_run
            ),
        ):
            targets = svc._get_bangumi_account_targets_for_user("alice")

        assert [api for _section, api in targets] == [primary]

    def test_orchestrator_fans_out_after_primary_mark(self):
        """主流程在首选账号标记成功后、收尾前把结果分发到其余账号。"""
        from unittest.mock import MagicMock, patch

        from app.models.sync import SyncResponse
        from app.services.sync_service import SyncService
        from app.services.sync_service.match_trace import MatchTrace
        from app.services.sync_service.orchestrator import SyncOrchestrator

        svc = SyncService()
        orch = SyncOrchestrator(svc)
        bgm = MagicMock()
        bgm.username = "main-account"
        exec_ctx = MagicMock()
        exec_ctx.terminal = None
        exec_ctx.current_outputs = {
            "subject_id": "100",
            "episode_id": "200",
            "bgm_title": "T",
            "mark_status": 1,
            "message": "",
        }

        fan_out = []
        other_result = {
            "section": "bangumi-2",
            "username": "alt-account",
            "status": "success",
            "message": "",
        }
        with (
            patch("app.services.sync_service.notification_service"),
            patch.object(svc, "_normalize_custom_item_params", return_value=None),
            patch.object(svc, "_get_bangumi_api_for_user", return_value=bgm),
            patch.object(svc, "_maybe_notify_match_ambiguous", return_value=None),
            patch.object(
                svc,
                "_get_bangumi_account_targets_for_user",
                return_value=[("bangumi", bgm)],
            ),
            patch.object(
                svc,
                "_mark_episode_for_other_accounts",
                side_effect=lambda *args: fan_out.append(args) or [other_result],
            ),
            patch.object(
                orch,
                "_match_subject",
                return_value=("100", False, None, MatchTrace()),
            ),
            patch.object(orch, "_run_execution_pipeline", return_value=exec_ctx),
            patch.object(
                orch,
                "_finalize_success",
                return_value=SyncResponse(status="success", message="ok"),
            ) as finalize,
        ):
            result = orch.sync_custom_item(self._item(), source="custom")

        assert result.status == "success"
        assert len(fan_out) == 1
        item, primary, se_id, ep_id, title = fan_out[0]
        assert item.user_name == "Elegy233"
        assert primary is bgm  # 首选沿用主流程实例，分发只补其余账号
        assert (se_id, ep_id, title) == ("100", "200", "T")

        # 各账号结果随收尾一并落库：首选在前并标记 primary，其余账号紧随其后
        outcomes = finalize.call_args.kwargs["account_results"]
        assert [o["section"] for o in outcomes] == ["bangumi", "bangumi-2"]
        assert outcomes[0]["username"] == "main-account"
        assert outcomes[0]["primary"] is True
        assert outcomes[1] == {**other_result, "primary": False}

    def test_finalize_success_response_carries_account_results(self):
        """收尾响应的 data 携带各账号结果，供调试页与同步记录详情读取。"""
        from app.services.sync_service import SyncService
        from app.services.sync_service.match_trace import MatchTrace
        from app.services.sync_service.orchestrator import SyncOrchestrator

        svc = SyncService()
        orch = SyncOrchestrator(svc)
        bgm = MagicMock()
        bgm.username = "main-account"
        outcomes = [
            {
                "section": "bangumi",
                "username": "main-account",
                "status": "success",
                "primary": True,
            },
            {
                "section": "bangumi-2",
                "username": "alt-account",
                "status": "failed",
                "message": "标记失败：401",
                "primary": False,
            },
        ]
        holder = [""]
        with (
            patch.object(svc, "_apply_sync_status", return_value=None),
            patch.object(svc, "_notify_account_outcomes", return_value=None),
            patch.object(svc, "_mark_subject_completed_if_needed", return_value=None),
            patch.object(orch, "_persist_sync_record", return_value=None),
        ):
            result = orch._finalize_success(
                self._item(),
                "custom",
                bgm,
                "100",
                "200",
                "T",
                2,
                "已标记为看过",
                MatchTrace(),
                holder,
                account_results=outcomes,
            )

        assert result.status == "success"
        assert result.data["account_results"] == outcomes
        assert holder[0] == "success"

    def test_mark_movie_watching_for_other_accounts_marks_each_remaining(
        self, monkeypatch
    ):
        """剧场版：其余账号各置一次「在看」，首选账号不重复写入。"""
        from unittest.mock import MagicMock

        from app.services.sync_service import SyncService

        svc = SyncService()
        primary, other = MagicMock(), MagicMock()
        monkeypatch.setattr(
            svc,
            "_get_bangumi_account_targets_for_user",
            lambda user_name: [("bangumi", primary), ("bangumi-2", other)],
        )

        svc._mark_movie_watching_for_other_accounts(
            self._item(media_type="movie"), primary, "123"
        )

        primary.ensure_subject_watching.assert_not_called()
        other.ensure_subject_watching.assert_called_once_with("123")

    def test_mark_movie_watching_for_other_accounts_failure_isolated(self, monkeypatch):
        """其余账号置「在看」失败只记录日志，不改变本次同步结果。"""
        from unittest.mock import MagicMock

        from app.services.sync_service import SyncService

        svc = SyncService()
        primary, other = MagicMock(), MagicMock()
        other.username = "alt-account"
        other.ensure_subject_watching.side_effect = RuntimeError("API 不可达")
        monkeypatch.setattr(
            svc,
            "_get_bangumi_account_targets_for_user",
            lambda user_name: [("bangumi", primary), ("bangumi-2", other)],
        )

        results = svc._mark_movie_watching_for_other_accounts(
            self._item(media_type="movie"), primary, "123"
        )

        # 不向上抛出，失败记入该账号结果，主流程结果仍由首选账号决定
        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert "API 不可达" in results[0]["message"]

    def test_get_bangumi_account_targets_for_user_unbound_user_returns_empty(self):
        """媒体服务器用户名未绑定任何 Bangumi 账号时返回空列表。"""
        from unittest.mock import patch

        from app.services.sync_service import SyncService

        svc = SyncService()

        with patch("app.core.accounts.get_bangumi_sections_for_user", return_value=[]):
            assert svc._get_bangumi_account_targets_for_user("ghost") == []

    def test_get_bangumi_account_targets_for_user_skips_incomplete_section(self):
        """同一用户名声明了多个账号但其中一个缺 token 时只返回可用账号。"""
        from unittest.mock import MagicMock, patch

        from app.services.sync_service import SyncService

        svc = SyncService()
        svc._bangumi_api_cache.clear()
        complete = MagicMock()
        with (
            patch(
                "app.core.accounts.get_bangumi_sections_for_user",
                return_value=["bangumi-incomplete", "bangumi"],
            ),
            patch(
                "app.core.accounts.get_bangumi_config_by_section",
                side_effect=lambda section: (
                    {"username": "u", "access_token": "t"}
                    if section == "bangumi"
                    else None
                ),
            ),
            patch.object(svc, "_get_bangumi_api_for_user", return_value=complete),
            patch.object(
                svc,
                "_get_bangumi_api_by_section",
                side_effect=lambda section: (
                    None if section == "bangumi-incomplete" else MagicMock()
                ),
            ),
        ):
            targets = svc._get_bangumi_account_targets_for_user("Elegy233")
        # 缺 token 的账号不可标记，首选仍是可用的首个完整账号
        assert [api for _section, api in targets] == [complete]

    def test_bangumi_api_cache_keys_namespace_isolated(self):
        """用户名与配置段同名时，按用户名与按配置段缓存互不覆盖。"""
        from unittest.mock import patch

        from app.services.sync_service import SyncService

        svc = SyncService()
        svc._bangumi_api_cache.clear()
        user_cfg = {"username": "shared", "access_token": "tu", "private": False}
        section_cfg = {"username": "shared", "access_token": "ts", "private": False}
        with (
            patch(
                "app.core.accounts.get_bangumi_config_for_user",
                side_effect=lambda name: user_cfg if name == "shared" else None,
            ),
            patch(
                "app.core.accounts.get_bangumi_config_by_section",
                side_effect=lambda section: (
                    section_cfg if section == "shared" else None
                ),
            ),
        ):
            user_api = svc._get_bangumi_api_for_user("shared")
            section_api = svc._get_bangumi_api_by_section("shared")
            # 同一命名空间再次取用返回已缓存的同一实例
            assert svc._get_bangumi_api_for_user("shared") is user_api
            assert svc._get_bangumi_api_by_section("shared") is section_api

        # 两个命名空间各自独立缓存，不共用扁平键
        assert len(svc._bangumi_api_cache) == 2
        assert "u:shared" in svc._bangumi_api_cache
        assert "s:shared" in svc._bangumi_api_cache

    def test_replay_pending_item_fans_out_to_other_accounts(self, monkeypatch):
        """补发成功时其余账号共享补发结果（入队只记首选账号）。"""
        import json
        from unittest.mock import MagicMock, patch

        from app.services.sync_service import SyncService

        svc = SyncService()
        bgm = MagicMock()
        monkeypatch.setattr(svc, "_get_bangumi_api_for_user", lambda user_name: bgm)
        monkeypatch.setattr(svc, "_retry_mark_episode", lambda *args, **kwargs: 1)
        fan_out = []
        monkeypatch.setattr(
            svc,
            "_mark_episode_for_other_accounts",
            lambda it, primary, se_id, ep_id, title: fan_out.append(
                (se_id, ep_id, title)
            ),
        )
        record = {
            "id": 1,
            "user_name": "Elegy233",
            "subject_id": "123",
            "episode_id": "456",
            "payload_json": json.dumps(self._item().model_dump()),
        }

        with patch("app.services.sync_service.notification_service"):
            result = svc.replay_pending_item(record)

        assert result["success"] is True
        assert fan_out == [("123", "456", "")]

    def test_replay_pending_item_movie_fans_out_to_other_accounts(self, monkeypatch):
        """剧场版补发成功时其余账号共享补发结果。"""
        import json
        from unittest.mock import MagicMock, patch

        from app.services.sync_service import SyncService

        svc = SyncService()
        bgm = MagicMock()
        bgm.ensure_subject_watching.return_value = 1
        monkeypatch.setattr(svc, "_get_bangumi_api_for_user", lambda user_name: bgm)
        fan_out = []
        monkeypatch.setattr(
            svc,
            "_mark_movie_watching_for_other_accounts",
            lambda it, primary, subject_id: fan_out.append(subject_id),
        )
        record = {
            "id": 2,
            "user_name": "Elegy233",
            "subject_id": "789",
            "episode_id": "",
            "payload_json": json.dumps(self._item(media_type="movie").model_dump()),
        }

        with patch("app.services.sync_service.notification_service"):
            result = svc.replay_pending_item(record)

        assert result["success"] is True
        assert fan_out == ["789"]

    def test_notify_account_outcomes_skips_primary_account(self):
        """首选账号的通知由主流程发出，逐账号通知不重复发送。"""
        from unittest.mock import patch

        from app.services.sync_service import SyncService

        svc = SyncService()
        outcomes = [
            {
                "section": "bangumi",
                "username": "main-account",
                "status": "success",
                "primary": True,
            },
            {
                "section": "bangumi-2",
                "username": "alt-account",
                "status": "success",
                "primary": False,
            },
        ]
        with patch("app.services.sync_service.notification_service") as mock_notify:
            svc._notify_account_outcomes(
                self._item(), "emby", "123", "456", "T", outcomes
            )

        sent = mock_notify.notify.call_args_list
        assert [c.args[0] for c in sent] == ["mark_success"]
        assert sent[0].kwargs["bgm_username"] == "alt-account"

    def test_notify_account_outcomes_reports_failure_per_account(self):
        """标记失败的账号单独发 mark_failed 通知，失败原因随通知带出。"""
        from unittest.mock import patch

        from app.services.sync_service import SyncService

        svc = SyncService()
        outcomes = [
            {
                "section": "bangumi-2",
                "username": "alt-account",
                "status": "failed",
                "message": "标记失败：401",
                "primary": False,
            }
        ]
        with patch("app.services.sync_service.notification_service") as mock_notify:
            svc._notify_account_outcomes(
                self._item(), "emby", "123", "456", "T", outcomes
            )

        call = mock_notify.notify.call_args_list[0]
        assert call.args[0] == "mark_failed"
        assert call.kwargs["error_message"] == "标记失败：401"
        assert call.kwargs["bgm_username"] == "alt-account"

    def test_notify_account_outcomes_skipped_uses_mark_skipped(self):
        """其余账号已看过时按 mark_skipped 通知，与 mark_success 区分。"""
        from unittest.mock import patch

        from app.services.sync_service import SyncService

        svc = SyncService()
        outcomes = [
            {
                "section": "bangumi-2",
                "username": "alt-account",
                "status": "success",
                "mark_status": 0,
                "message": "已在看或已看过，不再重复标记",
                "primary": False,
            }
        ]
        with patch("app.services.sync_service.notification_service") as mock_notify:
            svc._notify_account_outcomes(
                self._item(), "emby", "123", "456", "T", outcomes
            )

        call = mock_notify.notify.call_args_list[0]
        assert call.args[0] == "mark_skipped"
        assert call.kwargs["message"] == "已在看或已看过，不再重复标记"

    def test_notify_account_outcomes_without_outcomes_sends_nothing(self):
        """无账号结果（旧记录 / 单账号）时不额外发通知。"""
        from unittest.mock import patch

        from app.services.sync_service import SyncService

        svc = SyncService()
        for outcomes in (None, []):
            with patch("app.services.sync_service.notification_service") as mock_notify:
                svc._notify_account_outcomes(
                    self._item(), "emby", "123", "456", "T", outcomes
                )
            mock_notify.notify.assert_not_called()
