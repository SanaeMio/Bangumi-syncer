"""
同步服务完整流程 HTTP Mock 测试
使用 responses 和 mock 测试完整的同步流程
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from app.models.sync import CustomItem
from app.services.mapping_service import mapping_service
from app.services.sync_service import SyncService


@pytest.fixture
def mock_config():
    """创建模拟的 config"""
    with patch("app.services.sync_service.config_manager") as mock_cm:
        # 使用 side_effect 根据不同参数返回不同值
        def get_side_effect(section, key, fallback=None):
            if section == "sync" and key == "mode":
                return "single"
            elif section == "sync" and key == "blocked_keywords":
                return ""
            elif section == "sync" and key == "movie_mark_subject_completed":
                return True
            elif section == "sync" and key == "movie_playback_start_mark_watching":
                return True
            return fallback

        mock_cm.get.side_effect = get_side_effect
        mock_cm.get_single_mode_media_usernames.return_value = ["testuser"]
        mock_cm.get_user_mappings.return_value = {}
        mock_cm.get_bangumi_configs.return_value = {}
        yield mock_cm


@pytest.fixture
def mock_database():
    """创建模拟的 database"""
    with patch("app.services.sync_service.database_manager") as mock_db:
        mock_db.log_sync_record.return_value = None
        yield mock_db


@pytest.fixture
def mock_bangumi_api():
    """创建模拟的 BangumiApi"""
    with patch("app.services.sync_service.BangumiApi") as mock_api:
        mock_instance = MagicMock()

        # Mock search 方法
        mock_instance.search.return_value = [
            {"id": 123, "name": "Test Anime", "name_cn": "测试动画"}
        ]

        # Mock bgm_search 方法（_find_subject_id 阶段3 调用）
        mock_instance.bgm_search.return_value = [
            {
                "id": 123,
                "name": "Test Anime",
                "name_cn": "测试动画",
                "platform": "TV",
                "date": "2024-01-01",
            }
        ]

        # Mock get_subject 方法
        mock_instance.get_subject.return_value = {
            "id": 123,
            "name": "Test Anime",
            "name_cn": "测试动画",
            "type": 2,
            "eps": 12,
            "platform": "TV",
        }

        # Mock get_episodes 方法
        mock_instance.get_episodes.return_value = {
            "data": [
                {"id": 1, "ep": 1, "sort": 1, "name": "第1话"},
                {"id": 2, "ep": 2, "sort": 2, "name": "第2话"},
            ],
            "total": 2,
        }

        # Mock get_related_subjects 方法
        mock_instance.get_related_subjects.return_value = []

        # Mock mark_episode_watched 方法
        mock_instance.mark_episode_watched.return_value = 1
        mock_instance.ensure_subject_watching.return_value = 1

        mock_instance.get_target_season_episode_id.return_value = (123, 1)
        mock_instance.get_movie_main_episode_id.return_value = ("123", 1)
        # 剧场版条目标「看过」前会查询收藏；默认非「看过」以便断言会调用 change_collection_state
        mock_instance.get_subject_collection.return_value = {}

        mock_api.return_value = mock_instance
        yield mock_api


def test_sync_custom_item_success(mock_config, mock_database, mock_bangumi_api):
    """测试成功同步自定义项目"""
    service = SyncService()

    # 创建测试数据
    item = CustomItem(
        user_name="testuser",
        title="Test Anime",
        ori_title="Test Anime",
        season=1,
        episode=1,
        media_type="episode",
        release_date="2024-01-01",
    )

    # Mock get_bangumi_config_for_user
    with patch.object(
        service,
        "_get_bangumi_config_for_user",
        return_value={
            "username": "testuser",
            "access_token": "test_token",
            "private": True,
        },
    ):
        result = service.sync_custom_item(item, "custom")

    assert result.status == "success"
    assert result.message == "已标记为看过"


def test_sync_custom_item_not_found(mock_config, mock_database):
    """测试同步 - 番剧未找到"""
    service = SyncService()

    # Mock BangumiApi 搜索返回空
    with patch("app.services.sync_service.BangumiApi") as mock_api:
        mock_instance = MagicMock()
        mock_instance.bgm_search.return_value = None
        mock_instance.get_target_season_episode_id.return_value = (None, None)
        mock_api.return_value = mock_instance

        item = CustomItem(
            user_name="testuser",
            title="NonExistent",
            ori_title="",
            season=1,
            episode=1,
            media_type="episode",
            release_date="2024-01-01",
        )

        with patch.object(
            service,
            "_get_bangumi_config_for_user",
            return_value={
                "username": "testuser",
                "access_token": "test_token",
                "private": True,
            },
        ):
            result = service.sync_custom_item(item, "custom")

        assert result.status == "error"
        assert "未找到" in result.message


def test_sync_custom_item_episode_not_found(mock_config, mock_database):
    """测试同步 - 剧集未找到"""
    service = SyncService()

    with patch("app.services.sync_service.BangumiApi") as mock_api:
        mock_instance = MagicMock()
        mock_instance.bgm_search.return_value = [{"id": 123, "name": "Test"}]
        mock_instance.get_target_season_episode_id.return_value = (None, None)
        # 关联季条目链回退也返回 None（不命中），走原有 error 分支
        mock_instance.find_episode_across_seasons.return_value = None
        mock_api.return_value = mock_instance

        item = CustomItem(
            user_name="testuser",
            title="Test Anime",
            ori_title="",
            season=1,
            episode=999,  # 不存在的集数
            media_type="episode",
            release_date="2024-01-01",
        )

        with patch.object(
            service,
            "_get_bangumi_config_for_user",
            return_value={
                "username": "testuser",
                "access_token": "test_token",
                "private": True,
            },
        ):
            result = service.sync_custom_item(item, "custom")

        assert result.status == "error"
        assert "剧集" in result.message


def test_sync_custom_item_already_watched(mock_config, mock_database):
    """测试同步 - 已经看过"""
    service = SyncService()

    with patch("app.services.sync_service.BangumiApi") as mock_api:
        mock_instance = MagicMock()
        mock_instance.bgm_search.return_value = [{"id": 123, "name": "Test"}]
        mock_instance.get_target_season_episode_id.return_value = (123, 1)
        mock_instance.mark_episode_watched.return_value = 0  # 已看过
        mock_api.return_value = mock_instance

        item = CustomItem(
            user_name="testuser",
            title="Test Anime",
            ori_title="",
            season=1,
            episode=1,
            media_type="episode",
            release_date="2024-01-01",
        )

        with patch.object(
            service,
            "_get_bangumi_config_for_user",
            return_value={
                "username": "testuser",
                "access_token": "test_token",
                "private": True,
            },
        ):
            result = service.sync_custom_item(item, "custom")

        assert result.status == "success"
        assert "已看过" in result.message


def test_sync_custom_item_add_collection(mock_config, mock_database):
    """测试同步 - 添加收藏"""
    service = SyncService()

    with patch("app.services.sync_service.BangumiApi") as mock_api:
        mock_instance = MagicMock()
        mock_instance.bgm_search.return_value = [{"id": 123, "name": "Test"}]
        mock_instance.get_target_season_episode_id.return_value = (123, 1)
        mock_instance.mark_episode_watched.return_value = 2  # 添加收藏
        mock_api.return_value = mock_instance

        item = CustomItem(
            user_name="testuser",
            title="Test Anime",
            ori_title="",
            season=1,
            episode=1,
            media_type="episode",
            release_date="2024-01-01",
        )

        with patch.object(
            service,
            "_get_bangumi_config_for_user",
            return_value={
                "username": "testuser",
                "access_token": "test_token",
                "private": True,
            },
        ):
            result = service.sync_custom_item(item, "custom")

        assert result.status == "success"
        assert "添加" in result.message


def test_sync_custom_item_invalid_type(mock_config, mock_database):
    """测试同步 - 不支持的类型"""
    service = SyncService()

    item = CustomItem(
        user_name="testuser",
        title="Test",
        ori_title="",
        season=1,
        episode=1,
        media_type="music",
        release_date="2024-01-01",
    )

    result = service.sync_custom_item(item, "custom")

    assert result.status == "error"
    assert "不支持" in result.message


def test_sync_custom_item_movie_success_calls_movie_episode_path(
    mock_config, mock_database, mock_bangumi_api
):
    """电影走 get_movie_main_episode_id 并可条目标看过"""
    service = SyncService()
    mock_instance = mock_bangumi_api.return_value

    item = CustomItem(
        user_name="testuser",
        title="剧场版 X",
        ori_title=None,
        season=1,
        episode=1,
        media_type="movie",
        release_date="",
    )

    with patch.object(service, "_find_subject_id", return_value=("456", False, "")):
        with patch.object(
            service,
            "_get_bangumi_config_for_user",
            return_value={
                "username": "testuser",
                "access_token": "test_token",
                "private": True,
            },
        ):
            result = service.sync_custom_item(item, "custom")

    assert result.status == "success"
    mock_instance.get_movie_main_episode_id.assert_called()
    mock_instance.get_target_season_episode_id.assert_not_called()
    mock_instance.change_collection_state.assert_called()


def test_sync_custom_item_movie_skips_collection_when_subject_already_completed(
    mock_config, mock_database, mock_bangumi_api
):
    """剧场版条目收藏已是「看过」时不再调用 change_collection_state"""
    service = SyncService()
    mock_instance = mock_bangumi_api.return_value
    mock_instance.get_subject_collection.return_value = {"type": 2}

    item = CustomItem(
        user_name="testuser",
        title="剧场版 X",
        ori_title=None,
        season=1,
        episode=1,
        media_type="movie",
        release_date="",
    )

    with patch.object(service, "_find_subject_id", return_value=("456", False, "")):
        with patch.object(
            service,
            "_get_bangumi_config_for_user",
            return_value={
                "username": "testuser",
                "access_token": "test_token",
                "private": True,
            },
        ):
            result = service.sync_custom_item(item, "custom")

    assert result.status == "success"
    mock_instance.change_collection_state.assert_not_called()


def test_sync_custom_item_movie_no_subject_collection_when_mark_flag_off(
    mock_database, mock_bangumi_api
):
    """movie_mark_subject_completed 关闭时不查询收藏、不调 change_collection_state"""
    service = SyncService()
    mock_instance = mock_bangumi_api.return_value

    with patch("app.services.sync_service.config_manager") as mock_cfg:

        def get_side_effect(section, key, fallback=None):
            if section == "sync" and key == "movie_mark_subject_completed":
                return False
            if section == "sync" and key == "mode":
                return "single"
            if section == "sync" and key == "blocked_keywords":
                return ""
            if section == "bangumi_data" and key == "enabled":
                return False
            return fallback

        mock_cfg.get.side_effect = get_side_effect
        mock_cfg.get_single_mode_media_usernames.return_value = ["testuser"]
        mock_cfg.get_user_mappings.return_value = {}
        mock_cfg.get_bangumi_configs.return_value = {}

        item = CustomItem(
            user_name="testuser",
            title="剧场版 X",
            ori_title=None,
            season=1,
            episode=1,
            media_type="movie",
            release_date="",
        )

        with patch.object(service, "_find_subject_id", return_value=("456", False, "")):
            with patch.object(
                service,
                "_get_bangumi_config_for_user",
                return_value={
                    "username": "testuser",
                    "access_token": "test_token",
                    "private": True,
                },
            ):
                result = service.sync_custom_item(item, "custom")

    assert result.status == "success"
    mock_instance.get_subject_collection.assert_not_called()
    mock_instance.change_collection_state.assert_not_called()


def test_sync_custom_item_anime_completes_collection(mock_database, mock_bangumi_api):
    """TV番剧所有剧集看完时自动归档为看过"""
    service = SyncService()
    mock_instance = mock_bangumi_api.return_value
    mock_instance.get_subject_collection.return_value = {"type": 3, "ep_status": 12}
    mock_instance.get_subject.return_value = {"eps": 12}

    with patch("app.services.sync_service.config_manager") as mock_cfg:

        def get_side_effect(section, key, fallback=None):
            if section == "sync" and key == "anime_mark_subject_completed":
                return True
            if section == "sync" and key == "mode":
                return "single"
            if section == "sync" and key == "blocked_keywords":
                return ""
            if section == "bangumi_data" and key == "enabled":
                return False
            return fallback

        mock_cfg.get.side_effect = get_side_effect
        mock_cfg.get_single_mode_media_usernames.return_value = ["testuser"]
        mock_cfg.get_user_mappings.return_value = {}
        mock_cfg.get_bangumi_configs.return_value = {}

        item = CustomItem(
            user_name="testuser",
            title="TV Anime X",
            ori_title=None,
            season=1,
            episode=12,
            media_type="episode",
            release_date="",
        )

        with patch.object(service, "_find_subject_id", return_value=("456", False, "")):
            with patch.object(
                service,
                "_get_bangumi_config_for_user",
                return_value={
                    "username": "testuser",
                    "access_token": "***",
                    "private": True,
                },
            ):
                result = service.sync_custom_item(item, "custom")

    assert result.status == "success"
    mock_instance.change_collection_state.assert_called_once_with(
        subject_id="123", state=2
    )


def test_sync_custom_item_empty_title(mock_config, mock_database):
    """测试同步 - 空标题"""
    service = SyncService()

    item = CustomItem(
        user_name="testuser",
        title="",  # 空标题
        ori_title="",
        season=1,
        episode=1,
        media_type="episode",
        release_date="2024-01-01",
    )

    result = service.sync_custom_item(item, "custom")

    assert result.status == "error"
    assert "为空" in result.message


def test_sync_custom_item_sp_not_supported(mock_config, mock_database):
    """测试同步 - SP 标记不支持"""
    service = SyncService()

    item = CustomItem(
        user_name="testuser",
        title="Test Anime",
        ori_title="",
        season=0,  # SP
        episode=1,
        media_type="episode",
        release_date="2024-01-01",
    )

    result = service.sync_custom_item(item, "custom")

    assert result.status == "error"
    assert "SP" in result.message


def test_sync_custom_item_zero_episode(mock_config, mock_database):
    """测试同步 - 集数为0"""
    service = SyncService()

    item = CustomItem(
        user_name="testuser",
        title="Test Anime",
        ori_title="",
        season=1,
        episode=0,  # 集数为0
        media_type="episode",
        release_date="2024-01-01",
    )

    result = service.sync_custom_item(item, "custom")

    assert result.status == "error"
    assert "不能为0" in result.message


def test_sync_custom_item_blocked_keyword(mock_config, mock_database):
    """测试同步 - 屏蔽关键词"""
    service = SyncService()

    item = CustomItem(
        user_name="testuser",
        title="Test blocked Anime",  # 包含屏蔽词
        ori_title="",
        season=1,
        episode=1,
        media_type="episode",
        release_date="2024-01-01",
    )

    # 使用 side_effect 根据不同参数返回不同值
    def get_side_effect(section, key, fallback=None):
        if section == "sync" and key == "mode":
            return "single"
        elif section == "sync" and key == "blocked_keywords":
            return "blocked"
        return fallback or ""

    with patch("app.services.sync_service.config_manager") as cm:
        cm.get.side_effect = get_side_effect
        cm.get_single_mode_media_usernames.return_value = ["testuser"]
        cm.get_user_mappings.return_value = {}
        cm.get_bangumi_configs.return_value = {}
        result = service.sync_custom_item(item, "custom")

    assert result.status == "ignored"
    assert "屏蔽" in result.message


def test_sync_custom_item_no_permission(mock_config, mock_database):
    """测试同步 - 无权限（用户不在允许同步的媒体服务器用户名列表中）"""
    service = SyncService()

    # 单用户模式，用户名不在允许列表
    def get_side_effect(section, key, fallback=None):
        if section == "sync" and key == "mode":
            return "single"
        return fallback

    with patch("app.services.sync_service.config_manager") as cm:
        cm.get.side_effect = get_side_effect
        cm.get_single_mode_media_usernames.return_value = ["other_user"]
        cm.get_user_mappings.return_value = {}
        cm.get_bangumi_configs.return_value = {}

        item = CustomItem(
            user_name="testuser",
            title="Test Anime",
            ori_title="",
            season=1,
            episode=1,
            media_type="episode",
            release_date="2024-01-01",
        )

        result = service.sync_custom_item(item, "custom")

    assert result.status == "error"
    assert "不在允许同步" in result.message


def test_sync_task_status(mock_config, mock_database):
    """测试获取同步任务状态"""
    service = SyncService()

    # 手动创建一个任务状态
    task_id = "test_task_1"

    service._sync_tasks[task_id] = {
        "status": "running",
        "item": {"title": "Test Anime"},
        "source": "custom",
        "created_at": 1234567890,
        "result": None,
    }

    # 获取状态
    status = service.get_sync_task_status(task_id)

    assert status is not None
    assert "status" in status


def test_cleanup_old_tasks(mock_config, mock_database):
    """测试清理旧任务"""
    service = SyncService()

    # 手动添加一些旧任务
    import time

    old_time = time.time() - 3600 * 25  # 25小时前

    service._sync_tasks["old_task"] = {
        "status": "completed",
        "item": {},
        "source": "custom",
        "created_at": old_time,
    }

    service._sync_tasks["new_task"] = {
        "status": "completed",
        "item": {},
        "source": "custom",
        "created_at": time.time(),
    }

    # 清理24小时前的任务
    service.cleanup_old_tasks(max_age_hours=24)

    assert "old_task" not in service._sync_tasks
    assert "new_task" in service._sync_tasks


def test_check_season_info_in_title(mock_config, mock_database):
    """测试检查标题中的季度信息"""
    service = SyncService()

    # 测试中文数字
    assert service._check_season_info_in_title("测试动画 第一季", 1) is True
    assert service._check_season_info_in_title("测试动画 第二季", 2) is True

    # 测试阿拉伯数字
    assert service._check_season_info_in_title("测试动画 第1季", 1) is True
    assert service._check_season_info_in_title("测试动画 1季", 1) is True

    # 测试 Season 关键字
    assert service._check_season_info_in_title("Test Anime Season 1", 1) is True

    # 测试 S 关键字
    assert service._check_season_info_in_title("Test Anime S1", 1) is True

    # 测试无季度信息
    assert service._check_season_info_in_title("Test Anime", 1) is False

    assert service._check_season_info_in_title("某番第3季上半", 3) is True


@contextmanager
def _patched_sync_service_deps():
    with patch("app.services.sync_service.config_manager") as mock_cfg:
        with patch("app.services.sync_service.database_manager"):
            with patch("app.services.sync_service.send_notify"):
                with patch(
                    "app.services.sync_service.mapping_service"
                ) as mock_mapping_service:
                    # 默认 find_mapping 返回未命中（subject_id, match_type, reason）
                    mock_mapping_service.find_mapping.return_value = ("", "", "")
                    yield mock_cfg


def _branch_custom_item_for_find(**kwargs):
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


def _find_subject_via_bangumi_data(mock_cfg, find_return, season=2):
    def get_side_effect(section, key, fallback=None):
        if section == "bangumi_data" and key == "enabled":
            return True
        return fallback

    mock_cfg.get.side_effect = get_side_effect
    service = SyncService()
    mock_data = MagicMock()
    mock_data.find_bangumi_id.return_value = find_return
    # find_mapping 已由 _patched_sync_service_deps 默认置为未命中
    with patch.object(service, "_get_bangumi_data", return_value=mock_data):
        return service._find_subject_id(
            _branch_custom_item_for_find(season=season, title="T", ori_title="O")
        )


def test_find_subject_id_from_mapping(mock_config, mock_database):
    """测试从自定义映射查找 subject ID"""
    service = SyncService()

    # Mock 自定义映射（季度感知 + 正则规则统一通过 find_mapping）
    with patch.object(
        mapping_service,
        "find_mapping",
        return_value=("12345", "exact", "自定义映射命中：Test Anime=12345"),
    ):
        item = CustomItem(
            user_name="testuser",
            title="Test Anime",
            ori_title="",
            season=1,
            episode=1,
            media_type="episode",
            release_date="2024-01-01",
        )

        result = service._find_subject_id(item)

        assert result[0] == "12345"
        assert result[1] is False  # 自定义映射不视为特定季度ID
        assert result[2] == ""


def test_find_subject_id_movie_passes_is_movie_to_bgm_search(mock_database):
    """电影走 API 搜索时向 bgm_search 传入 is_movie=True"""
    service = SyncService()

    with patch("app.services.sync_service.config_manager") as mock_cfg:

        def get_side_effect(section, key, fallback=None):
            if section == "bangumi_data" and key == "enabled":
                return False
            if section == "sync" and key == "mode":
                return "single"
            return fallback

        mock_cfg.get.side_effect = get_side_effect
        mock_cfg.get_single_mode_media_usernames.return_value = ["testuser"]
        mock_cfg.get_user_mappings.return_value = {}
        mock_cfg.get_bangumi_configs.return_value = {}

        bgm = MagicMock()
        bgm.bgm_search.return_value = [{"id": 4242, "name_cn": "剧场版"}]

        item = CustomItem(
            user_name="testuser",
            title="某剧场版",
            ori_title=None,
            season=1,
            episode=1,
            media_type="movie",
            release_date="",
            source="custom",
        )

        with patch.object(mapping_service, "find_mapping", return_value=("", "", "")):
            with patch.object(service, "_get_bangumi_api_for_user", return_value=bgm):
                sid, is_season, _ = service._find_subject_id(item)

    assert str(sid) == "4242"
    assert is_season is False
    bgm.bgm_search.assert_called_once()
    assert bgm.bgm_search.call_args.kwargs.get("is_movie") is True


def test_plex_sync_item_success(mock_config, mock_database, mock_bangumi_api):
    """测试 Plex 同步 - 成功"""
    service = SyncService()

    plex_data = {
        "event": "media.scrobble",
        "Account": {"title": "testuser"},
        "Metadata": {
            "type": "episode",
            "parentIndex": 1,
            "index": 1,
            "grandparentTitle": "Test Anime",
        },
    }

    with patch("app.services.sync_service.BangumiApi") as mock_api:
        mock_instance = MagicMock()
        mock_instance.bgm_search.return_value = [{"id": 123, "name": "Test"}]
        mock_instance.get_target_season_episode_id.return_value = (123, 1)
        mock_instance.mark_episode_watched.return_value = 1
        mock_api.return_value = mock_instance

        with patch.object(
            service,
            "_get_bangumi_config_for_user",
            return_value={
                "username": "testuser",
                "access_token": "test_token",
                "private": True,
            },
        ):
            result = service.sync_plex_item(plex_data)

    assert result.status == "success"


def test_plex_sync_item_ignored_event(mock_config, mock_database):
    """测试 Plex 同步 - 忽略非 scrobble 事件"""
    service = SyncService()

    plex_data = {
        "event": "media.play",  # 非 scrobble 事件
        "Account": {"title": "testuser"},
        "Metadata": {
            "type": "episode",
            "parentIndex": 1,
            "index": 1,
            "grandparentTitle": "Test Anime",
        },
    }

    result = service.sync_plex_item(plex_data)

    assert result.status == "ignored"


def test_emby_sync_item_success(mock_config, mock_database, mock_bangumi_api):
    """测试 Emby 同步 - 成功"""
    service = SyncService()

    emby_data = {
        "Event": "item.markplayed",
        "Item": {
            "Type": "Episode",
            "SeriesName": "Test Anime",
            "ParentIndexNumber": 1,
            "IndexNumber": 1,
        },
        "User": {"Name": "testuser"},
    }

    with patch("app.services.sync_service.BangumiApi") as mock_api:
        mock_instance = MagicMock()
        mock_instance.bgm_search.return_value = [{"id": 123, "name": "Test"}]
        mock_instance.get_target_season_episode_id.return_value = (123, 1)
        mock_instance.mark_episode_watched.return_value = 1
        mock_api.return_value = mock_instance

        with patch.object(
            service,
            "_get_bangumi_config_for_user",
            return_value={
                "username": "testuser",
                "access_token": "test_token",
                "private": True,
            },
        ):
            result = service.sync_emby_item(emby_data)

    assert result.status == "success"


def test_emby_sync_item_missing_field(mock_config, mock_database):
    """测试 Emby 同步 - 缺少字段"""
    service = SyncService()

    emby_data = {
        "Event": "item.markplayed",
        "Item": {
            "Type": "Episode",
            # 缺少 SeriesName
        },
        "User": {"Name": "testuser"},
    }

    result = service.sync_emby_item(emby_data)

    assert result.status == "error"
    assert "缺少" in result.message


def test_jellyfin_sync_item_success(mock_config, mock_database, mock_bangumi_api):
    """测试 Jellyfin 同步 - 成功"""
    service = SyncService()

    # 提供 extract_jellyfin_data 所需的字段
    jellyfin_data = {
        "NotificationType": "PlaybackStop",
        "PlayedToCompletion": "True",
        "ItemType": "Episode",
        "SeriesName": "Test Anime",
        "SeasonNumber": 1,
        "EpisodeNumber": 1,
        "UserName": "testuser",
        # extract_jellyfin_data 需要的字段
        "media_type": "episode",
        "title": "Test Anime",
        "ori_title": "",
        "season": 1,
        "episode": 1,
        "user_name": "testuser",
    }

    with patch("app.services.sync_service.BangumiApi") as mock_api:
        mock_instance = MagicMock()
        mock_instance.bgm_search.return_value = [{"id": 123, "name": "Test"}]
        mock_instance.get_target_season_episode_id.return_value = (123, 1)
        mock_instance.mark_episode_watched.return_value = 1
        mock_api.return_value = mock_instance

        with patch.object(
            service,
            "_get_bangumi_config_for_user",
            return_value={
                "username": "testuser",
                "access_token": "test_token",
                "private": True,
            },
        ):
            result = service.sync_jellyfin_item(jellyfin_data)

    assert result.status == "success"


def test_jellyfin_sync_item_ignored_event(mock_config, mock_database):
    """测试 Jellyfin 同步 - 忽略非播放停止事件"""
    service = SyncService()

    jellyfin_data = {
        "NotificationType": "PlaybackStart",  # 非播放停止
        "PlayedToCompletion": "True",
        "ItemType": "Episode",
    }

    result = service.sync_jellyfin_item(jellyfin_data)

    assert result.status == "ignored"


def test_jellyfin_sync_item_not_played_completion(mock_config, mock_database):
    """测试 Jellyfin 同步 - 未播放完成"""
    service = SyncService()

    jellyfin_data = {
        "NotificationType": "PlaybackStop",
        "PlayedToCompletion": "False",  # 未播放完成
        "ItemType": "Episode",
    }

    result = service.sync_jellyfin_item(jellyfin_data)

    assert result.status == "ignored"


def test_find_subject_id_season_gt1_date_matched_sets_season_flag():
    with _patched_sync_service_deps() as cfg:
        sid, flag, _ = _find_subject_via_bangumi_data(
            cfg, ("99", "标题", True), season=2
        )
        assert sid == "99"
        assert flag is True


def test_find_subject_id_season_gt1_title_has_season_info():
    with _patched_sync_service_deps() as cfg:
        sid, flag, _ = _find_subject_via_bangumi_data(
            cfg, ("88", "某番 第2季", False), season=2
        )
        assert sid == "88"
        assert flag is True


def test_find_subject_id_season_gt1_no_date_no_season_keyword():
    with _patched_sync_service_deps() as cfg:
        sid, flag, _ = _find_subject_via_bangumi_data(
            cfg, ("77", "无季标", False), season=2
        )
        assert sid == "77"
        assert flag is False


def test_find_subject_id_season1_sets_season_matched_true():
    with _patched_sync_service_deps() as cfg:
        sid, flag, _ = _find_subject_via_bangumi_data(
            cfg, ("66", "第一季", False), season=1
        )
        assert sid == "66"
        assert flag is True


def test_find_subject_id_find_bangumi_id_exception_falls_through_to_api():
    with _patched_sync_service_deps() as cfg:

        def get_side_effect(section, key, fallback=None):
            if section == "bangumi_data" and key == "enabled":
                return True
            return fallback

        cfg.get.side_effect = get_side_effect
        service = SyncService()
        mock_data = MagicMock()
        mock_data.find_bangumi_id.side_effect = RuntimeError("parse fail")
        bgm = MagicMock()
        bgm.bgm_search.return_value = [{"id": 42}]
        with patch.object(mapping_service, "find_mapping", return_value=("", "", "")):
            with patch.object(service, "_get_bangumi_data", return_value=mock_data):
                with patch.object(
                    service, "_get_bangumi_api_for_user", return_value=bgm
                ):
                    sid, flag, _ = service._find_subject_id(
                        _branch_custom_item_for_find()
                    )
        assert sid == 42 or sid == "42"
        assert flag is False


def test_find_subject_id_api_top_is_movie_falls_back_to_related_mainline():
    """完美世界场景：bangumi-data 未命中，API 搜索首条是剧场版（movie），
    候选列表无 episode，通过关联条目改选到主线剧集。

    模拟真实数据：
    - bgm_search 返回首条 542046 完美世界剧场版 九劫焚天（movie）
    - get_related_subjects(542046) 返回关联条目含 577198 完美世界 第六季（主线故事）
    - get_subject(577198) 返回完整条目
    - 应改选到 577198，而非保持 542046
    """
    with _patched_sync_service_deps() as cfg:

        def get_side_effect(section, key, fallback=None):
            if section == "bangumi_data" and key == "enabled":
                return True
            if section == "sync" and key == "enable_real_action":
                return False
            return fallback

        cfg.get.side_effect = get_side_effect
        service = SyncService()
        mock_data = MagicMock()
        mock_data.find_bangumi_id.return_value = None  # bangumi-data 未命中

        bgm = MagicMock()
        # bgm_search 返回首条是剧场版（标题命中"剧场版"关键词 → detect = movie）
        bgm.bgm_search.return_value = [
            {
                "id": 542046,
                "name": "完美世界剧场版 九劫焚天",
                "name_cn": "完美世界剧场版 九劫焚天",
                "platform": "剧场版",
                "date": "",
            }
        ]
        # 关联条目返回 577198 第六季（主线故事）
        bgm.get_related_subjects.return_value = [
            {
                "id": 485902,
                "name": "完美世界剧场版 火之灰烬",
                "name_cn": "",
                "relation": "前传",
                "type": 2,
            },
            {
                "id": 577198,
                "name": "完美世界 第六季",
                "name_cn": "",
                "relation": "主线故事",
                "type": 2,
            },
        ]
        # get_subject 返回 577198 的完整信息
        bgm.get_subject.return_value = {
            "id": 577198,
            "name": "完美世界 第六季",
            "name_cn": "完美世界 第六季",
            "type": 2,
            "platform": "WEB",
            "date": "2025-10-03",
        }

        with patch.object(mapping_service, "find_mapping", return_value=("", "", "")):
            with patch.object(service, "_get_bangumi_data", return_value=mock_data):
                with patch.object(
                    service, "_get_bangumi_api_for_user", return_value=bgm
                ):
                    sid, flag, _ = service._find_subject_id(
                        _branch_custom_item_for_find(
                            title="完美世界",
                            ori_title="",
                            season=1,
                            media_type="episode",
                            release_date="",
                        )
                    )
        # 应改选到 577198（主线故事），而非保持 542046
        assert str(sid) == "577198"
        assert flag is True
        # 验证调用了 get_related_subjects
        bgm.get_related_subjects.assert_called_once()
        # 验证 get_subject 被调用以获取关联条目详情
        bgm.get_subject.assert_called_with(577198)


def test_find_subject_id_api_top_is_movie_no_related_keeps_first():
    """首条候选是 movie 但关联条目里没有 episode 时，保持首条不报错。"""
    with _patched_sync_service_deps() as cfg:

        def get_side_effect(section, key, fallback=None):
            if section == "bangumi_data" and key == "enabled":
                return True
            if section == "sync" and key == "enable_real_action":
                return False
            return fallback

        cfg.get.side_effect = get_side_effect
        service = SyncService()
        mock_data = MagicMock()
        mock_data.find_bangumi_id.return_value = None

        bgm = MagicMock()
        bgm.bgm_search.return_value = [
            {
                "id": 542046,
                "name": "完美世界剧场版 九劫焚天",
                "name_cn": "完美世界剧场版 九劫焚天",
                "platform": "剧场版",
                "date": "",
            }
        ]
        # 关联条目为空
        bgm.get_related_subjects.return_value = []
        bgm.get_subject.return_value = {
            "id": 542046,
            "name": "完美世界剧场版 九劫焚天",
            "name_cn": "完美世界剧场版 九劫焚天",
        }

        with patch.object(mapping_service, "find_mapping", return_value=("", "", "")):
            with patch.object(service, "_get_bangumi_data", return_value=mock_data):
                with patch.object(
                    service, "_get_bangumi_api_for_user", return_value=bgm
                ):
                    sid, flag, _ = service._find_subject_id(
                        _branch_custom_item_for_find(
                            title="完美世界",
                            ori_title="",
                            season=1,
                            media_type="episode",
                            release_date="",
                        )
                    )
        # 关联条目为空，保持首条 542046
        assert str(sid) == "542046"


def test_find_subject_id_api_top_movie_picks_mainline_over_derivative():
    """完美世界场景：首条是剧场版（movie），候选中有衍生短番（双食记 6 集）
    和多季主线剧集，应优先选主线剧集（按 eps 择优），而非衍生短番。

    模拟真实搜索结果：
    - 542046 完美世界剧场版 九劫焚天（movie，detect 排除）
    - 175141 完美世界双食记（episode，但 eps=6 是衍生短番）
    - 403251 完美世界 第三季（episode，eps=52 主线剧集）
    - 345811 完美世界 第二季（episode，eps=52 主线剧集）

    应选 403251 或 345811（eps 最大），而非 175141。
    """
    with _patched_sync_service_deps() as cfg:

        def get_side_effect(section, key, fallback=None):
            if section == "bangumi_data" and key == "enabled":
                return True
            if section == "sync" and key == "enable_real_action":
                return False
            return fallback

        cfg.get.side_effect = get_side_effect
        service = SyncService()
        mock_data = MagicMock()
        mock_data.find_bangumi_id.return_value = None

        bgm = MagicMock()
        bgm.bgm_search.return_value = [
            {
                "id": 542046,
                "name": "完美世界剧场版 九劫焚天",
                "name_cn": "完美世界剧场版 九劫焚天",
                "platform": "剧场版",
                "eps": 1,
            },
            {
                "id": 175141,
                "name": "完美世界双食记",
                "name_cn": "完美世界双食记",
                "platform": "WEB",
                "eps": 6,
            },
            {
                "id": 403251,
                "name": "完美世界 第三季",
                "name_cn": "完美世界 第三季",
                "platform": "WEB",
                "eps": 52,
            },
            {
                "id": 345811,
                "name": "完美世界 第二季",
                "name_cn": "完美世界 第二季",
                "platform": "WEB",
                "eps": 52,
            },
        ]

        with patch.object(mapping_service, "find_mapping", return_value=("", "", "")):
            with patch.object(service, "_get_bangumi_data", return_value=mock_data):
                with patch.object(
                    service, "_get_bangumi_api_for_user", return_value=bgm
                ):
                    sid, flag, _ = service._find_subject_id(
                        _branch_custom_item_for_find(
                            title="完美世界",
                            ori_title="",
                            season=1,
                            media_type="episode",
                            release_date="",
                        )
                    )
        # 应改选到主线剧集（403251 或 345811），而非衍生短番 175141
        sid_str = str(sid)
        assert sid_str in {"403251", "345811"}, f"应改选到主线剧集，实际命中 {sid_str}"
        assert sid_str != "175141", "不应命中衍生短番双食记"


def test_pick_mainline_episode_candidate_prefers_exact_title_match():
    """_pick_mainline_episode_candidate：标题精确匹配优先。"""
    service = SyncService()
    candidates = [
        {"id": 175141, "name": "完美世界双食记", "name_cn": "完美世界双食记", "eps": 6},
        {"id": 244224, "name": "完美世界", "name_cn": "完美世界", "eps": 26},
    ]
    result = service._pick_mainline_episode_candidate(candidates, "完美世界")
    assert result["id"] == 244224


def test_pick_mainline_episode_candidate_prefers_season_keyword():
    """_pick_mainline_episode_candidate：无精确匹配时优先含"第N季"声明的候选。"""
    service = SyncService()
    candidates = [
        {"id": 175141, "name": "完美世界双食记", "name_cn": "完美世界双食记", "eps": 6},
        {
            "id": 403251,
            "name": "完美世界 第三季",
            "name_cn": "完美世界 第三季",
            "eps": 52,
        },
    ]
    result = service._pick_mainline_episode_candidate(candidates, "完美世界")
    assert result["id"] == 403251


def test_pick_mainline_episode_candidate_falls_back_to_max_eps():
    """_pick_mainline_episode_candidate：无精确匹配且无季番声明时按 eps 择优。"""
    service = SyncService()
    candidates = [
        {"id": 1, "name": "完美世界A", "name_cn": "完美世界A", "eps": 10},
        {"id": 2, "name": "完美世界B", "name_cn": "完美世界B", "eps": 100},
        {"id": 3, "name": "完美世界C", "name_cn": "完美世界C", "eps": 50},
    ]
    result = service._pick_mainline_episode_candidate(candidates, "完美世界")
    assert result["id"] == 2


def test_find_subject_id_api_disabled_no_bgm_instance():
    with _patched_sync_service_deps() as cfg:

        def get_side_effect(section, key, fallback=None):
            if section == "bangumi_data" and key == "enabled":
                return False
            return fallback

        cfg.get.side_effect = get_side_effect
        service = SyncService()
        with patch.object(mapping_service, "find_mapping", return_value=("", "", "")):
            with patch.object(service, "_get_bangumi_api_for_user", return_value=None):
                sid, flag, err = service._find_subject_id(
                    _branch_custom_item_for_find()
                )
        assert sid is None
        assert flag is False
        assert err == "无法创建 Bangumi API 实例，无法搜索条目"


def test_find_subject_id_api_search_exception_returns_none():
    with _patched_sync_service_deps() as cfg:

        def get_side_effect(section, key, fallback=None):
            if section == "bangumi_data" and key == "enabled":
                return False
            return fallback

        cfg.get.side_effect = get_side_effect
        service = SyncService()
        bgm = MagicMock()
        bgm.bgm_search.side_effect = OSError("net")
        with patch.object(mapping_service, "find_mapping", return_value=("", "", "")):
            with patch.object(service, "_get_bangumi_api_for_user", return_value=bgm):
                sid, flag, err = service._find_subject_id(
                    _branch_custom_item_for_find()
                )
        assert sid is None
        assert "Bangumi API 搜索出错" in err


def test_sync_custom_item_no_bgm_api_after_find_subject():
    with _patched_sync_service_deps():
        svc = SyncService()
        with patch.object(svc, "_check_user_permission", return_value=(True, "")):
            with patch.object(svc, "_is_title_blocked", return_value=False):
                with patch.object(
                    svc, "_find_subject_id", return_value=("123", False, "")
                ):
                    with patch.object(
                        svc, "_get_bangumi_api_for_user", return_value=None
                    ):
                        r = svc.sync_custom_item(
                            _branch_custom_item_for_find(), "custom"
                        )
        assert r.status == "error"
        assert "bangumi" in r.message and "错误" in r.message


def test_sync_custom_item_get_target_season_value_error_auth_message():
    with _patched_sync_service_deps():
        svc = SyncService()
        bgm = MagicMock()
        bgm.get_target_season_episode_id.side_effect = ValueError(
            "认证失败 access_token 过期"
        )
        with patch.object(svc, "_check_user_permission", return_value=(True, "")):
            with patch.object(svc, "_is_title_blocked", return_value=False):
                with patch.object(
                    svc, "_find_subject_id", return_value=("1", False, "")
                ):
                    with patch.object(
                        svc, "_get_bangumi_api_for_user", return_value=bgm
                    ):
                        r = svc.sync_custom_item(
                            _branch_custom_item_for_find(), "custom"
                        )
        assert r.status == "error"
        assert "access_token" in r.message or "认证" in r.message


def test_sync_custom_item_mark_value_error_auth_message():
    with _patched_sync_service_deps():
        svc = SyncService()
        bgm = MagicMock()
        bgm.get_target_season_episode_id.return_value = ("1", "10")
        bgm.mark_episode_watched.side_effect = ValueError("access_token 无效")
        with patch.object(svc, "_find_subject_id", return_value=("1", False, "")):
            with patch.object(svc, "_get_bangumi_api_for_user", return_value=bgm):
                r = svc.sync_custom_item(_branch_custom_item_for_find(), "custom")
        assert r.status == "error"


def test_sync_custom_item_outer_exception_returns_error():
    with _patched_sync_service_deps():
        svc = SyncService()
        with patch.object(
            svc, "_check_user_permission", side_effect=RuntimeError("perm boom")
        ):
            r = svc.sync_custom_item(_branch_custom_item_for_find(), "custom")
        assert r.status == "error"
        assert "处理失败" in r.message


def test_find_subject_id_api_search_season_gt1_title_matched():
    """测试 API 搜索返回结果标题包含季度信息时，正确设置 is_season_matched_id = True"""
    with _patched_sync_service_deps() as cfg:
        # 强制禁用本地 bangumi-data，确保代码走进 API 搜索分支
        def get_side_effect(section, key, fallback=None):
            if section == "bangumi_data" and key == "enabled":
                return False
            return fallback

        cfg.get.side_effect = get_side_effect
        service = SyncService()
        bgm = MagicMock()

        # 模拟 API 搜索完美命中了包含季度信息的标题
        bgm.bgm_search.return_value = [
            {
                "id": 451757,
                "name": "Rick and Morty Season 9",
                "name_cn": "瑞克和莫蒂 第九季",
            }
        ]

        with patch.object(mapping_service, "find_mapping", return_value=("", "", "")):
            with patch.object(service, "_get_bangumi_api_for_user", return_value=bgm):
                sid, flag, err = service._find_subject_id(
                    _branch_custom_item_for_find(season=9, title="瑞克和莫蒂")
                )

    assert sid == 451757
    assert flag is True  # 核心断言：智能判定成功生效，阻止后续的链式爬取
    assert err == ""


def test_find_subject_id_api_search_season_gt1_title_not_matched():
    """测试 API 搜索返回结果标题不包含季度信息时，保留 is_season_matched_id = False"""
    with _patched_sync_service_deps() as cfg:

        def get_side_effect(section, key, fallback=None):
            if section == "bangumi_data" and key == "enabled":
                return False
            return fallback

        cfg.get.side_effect = get_side_effect
        service = SyncService()
        bgm = MagicMock()

        # 模拟 API 搜索由于模糊匹配，只返回了第一季的条目（标题中不含 Season 9）
        bgm.bgm_search.return_value = [
            {"id": 146457, "name": "Rick and Morty", "name_cn": "瑞克和莫蒂"}
        ]

        with patch.object(mapping_service, "find_mapping", return_value=("", "", "")):
            with patch.object(service, "_get_bangumi_api_for_user", return_value=bgm):
                sid, flag, err = service._find_subject_id(
                    _branch_custom_item_for_find(season=9, title="瑞克和莫蒂")
                )

    assert sid == 146457
    assert flag is False  # 核心断言：智能判定未生效，安全地回退给后续的关系链爬虫去处理
    assert err == ""
