"""
同步服务完整流程 HTTP Mock 测试
使用 responses 和 mock 测试完整的同步流程
"""

from unittest.mock import MagicMock, patch

import pytest

from app.models.sync import CustomItem
from app.services.sync_service import SyncService


@pytest.fixture
def mock_config():
    """创建模拟的 config"""
    with patch("app.services.sync_service.config_manager") as mock_cm:
        # 使用 side_effect 根据不同参数返回不同值
        def get_side_effect(section, key, fallback=None):
            if section == "sync" and key == "mode":
                return "single"
            elif section == "sync" and key == "single_username":
                return "testuser"
            elif section == "sync" and key == "blocked_keywords":
                return ""
            return fallback or ""

        mock_cm.get.side_effect = get_side_effect
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

        mock_instance.get_target_season_episode_id.return_value = (123, 1)

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
        title="Test Movie",
        ori_title="",
        season=1,
        episode=1,
        media_type="movie",  # 不支持的类型
        release_date="2024-01-01",
    )

    result = service.sync_custom_item(item, "custom")

    assert result.status == "error"
    assert "不支持" in result.message


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
        elif section == "sync" and key == "single_username":
            return "testuser"
        elif section == "sync" and key == "blocked_keywords":
            return "blocked"
        return fallback or ""

    with patch("app.services.sync_service.config_manager") as cm:
        cm.get.side_effect = get_side_effect
        cm.get_user_mappings.return_value = {}
        cm.get_bangumi_configs.return_value = {}
        result = service.sync_custom_item(item, "custom")

    assert result.status == "ignored"
    assert "屏蔽" in result.message


def test_sync_custom_item_no_permission(mock_config, mock_database):
    """测试同步 - 无权限"""
    service = SyncService()

    # 单用户模式，未配置用户名
    with patch("app.services.sync_service.config_manager") as cm:
        cm.get.side_effect = lambda *args, **kwargs: ""
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
    assert "无权限" in result.message


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


def test_find_subject_id_from_mapping(mock_config, mock_database):
    """测试从自定义映射查找 subject ID"""
    service = SyncService()

    # Mock 自定义映射
    with patch.object(
        service, "_load_custom_mappings", return_value={"Test Anime": "12345"}
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
