"""
Bangumi API HTTP Mock 测试
使用 responses 库模拟外部 API 调用
"""

import pytest
import responses

from app.utils.bangumi_api import BangumiApi


@responses.activate
def test_search_success():
    """测试搜索成功"""
    responses.add(
        responses.POST,
        "https://api.bgm.tv/v0/search/subjects",
        json={"data": [{"id": 1, "name": "Test Anime", "name_cn": "测试动画"}]},
        status=200,
    )

    api = BangumiApi()
    result = api.search("test", "2024-01-01", "2024-12-31")

    assert len(result) == 1
    assert result[0]["id"] == 1
    assert result[0]["name"] == "Test Anime"


@responses.activate
def test_search_empty_result():
    """测试搜索无结果"""
    responses.add(
        responses.POST,
        "https://api.bgm.tv/v0/search/subjects",
        json={"data": []},
        status=200,
    )

    api = BangumiApi()
    result = api.search("nonexistent", "2024-01-01", "2024-12-31")

    assert result == []


@responses.activate
def test_get_subject():
    """测试获取条目详情"""
    responses.add(
        responses.GET,
        "https://api.bgm.tv/v0/subjects/123",
        json={
            "id": 123,
            "name": "Test Anime",
            "name_cn": "测试动画",
            "type": 2,
            "eps": 12,
        },
        status=200,
    )

    api = BangumiApi()
    result = api.get_subject(123)

    assert result["id"] == 123
    assert result["name_cn"] == "测试动画"
    assert result["eps"] == 12


@responses.activate
def test_get_episodes():
    """测试获取剧集列表"""
    responses.add(
        responses.GET,
        "https://api.bgm.tv/v0/episodes",
        json={
            "data": [
                {"id": 1, "ep": 1, "sort": 1, "name": "第1话"},
                {"id": 2, "ep": 2, "sort": 2, "name": "第2话"},
            ],
            "total": 2,
        },
        status=200,
    )

    api = BangumiApi()
    result = api.get_episodes(123)

    assert result["total"] == 2
    assert len(result["data"]) == 2
    assert result["data"][0]["ep"] == 1


@responses.activate
def test_mark_episode_watched_add_collection():
    """测试标记观看 - 未收藏情况"""
    # Mock get_subject_collection 返回空（未收藏）
    responses.add(
        responses.GET,
        "https://api.bgm.tv/v0/users/testuser/collections/123",
        json={},
        status=404,
    )
    # Mock add_collection_subject
    responses.add(
        responses.POST,
        "https://api.bgm.tv/v0/users/-/collections/123",
        json={"status": "ok"},
        status=200,
    )
    # Mock change_episode_state
    responses.add(
        responses.PUT,
        "https://api.bgm.tv/v0/users/-/collections/-/episodes/1",
        json={"status": "ok"},
        status=200,
    )

    api = BangumiApi(username="testuser", access_token="test_token")
    result = api.mark_episode_watched("123", "1")

    # 返回 2 表示添加到收藏并标记为看过
    assert result == 2


@responses.activate
def test_mark_episode_watched_already_watched():
    """测试标记观看 - 已看过"""
    # Mock get_subject_collection 返回已看过
    responses.add(
        responses.GET,
        "https://api.bgm.tv/v0/users/testuser/collections/123",
        json={"type": 2, "subject_id": 123},
        status=200,
    )

    api = BangumiApi(username="testuser", access_token="test_token")
    result = api.mark_episode_watched("123", "1")

    # 返回 0 表示已看过，跳过
    assert result == 0


@responses.activate
def test_mark_episode_watched_already_episode_watched():
    """测试标记观看 - 单集已看过"""
    # Mock get_subject_collection 返回在 看
    responses.add(
        responses.GET,
        "https://api.bgm.tv/v0/users/testuser/collections/123",
        json={"type": 3, "subject_id": 123},
        status=200,
    )
    # Mock get_ep_collection 返回已看过
    responses.add(
        responses.GET,
        "https://api.bgm.tv/v0/users/-/collections/-/episodes/1",
        json={"type": 2, "episode_id": 1},
        status=200,
    )

    api = BangumiApi(username="testuser", access_token="test_token")
    result = api.mark_episode_watched("123", "1")

    # 返回 0 表示已看过，跳过
    assert result == 0


@responses.activate
def test_mark_episode_watched_success():
    """测试标记观看 - 成功标记"""
    # Mock get_subject_collection 返回在 看
    responses.add(
        responses.GET,
        "https://api.bgm.tv/v0/users/testuser/collections/123",
        json={"type": 3, "subject_id": 123},
        status=200,
    )
    # Mock get_ep_collection 返回未看过
    responses.add(
        responses.GET,
        "https://api.bgm.tv/v0/users/-/collections/-/episodes/1",
        json={},
        status=404,
    )
    # Mock change_episode_state
    responses.add(
        responses.PUT,
        "https://api.bgm.tv/v0/users/-/collections/-/episodes/1",
        json={"status": "ok"},
        status=200,
    )

    api = BangumiApi(username="testuser", access_token="test_token")
    result = api.mark_episode_watched("123", "1")

    # 返回 1 表示成功标记
    assert result == 1


@responses.activate
def test_get_related_subjects():
    """测试获取关联条目"""
    responses.add(
        responses.GET,
        "https://api.bgm.tv/v0/subjects/123/subjects",
        json=[
            {"id": 456, "relation": "续集", "name": "Test Anime Season 2"},
        ],
        status=200,
    )

    api = BangumiApi()
    result = api.get_related_subjects(123)

    assert len(result) == 1
    assert result[0]["relation"] == "续集"


@responses.activate
def test_get_subject_collection():
    """测试获取条目收藏状态"""
    responses.add(
        responses.GET,
        "https://api.bgm.tv/v0/users/testuser/collections/123",
        json={"type": 3, "subject_id": 123, "private": False},
        status=200,
    )

    api = BangumiApi(username="testuser", access_token="test_token")
    result = api.get_subject_collection("123")

    assert result["type"] == 3


@responses.activate
def test_get_ep_collection():
    """测试获取单集收藏状态"""
    responses.add(
        responses.GET,
        "https://api.bgm.tv/v0/users/-/collections/-/episodes/1",
        json={"type": 2, "episode_id": 1},
        status=200,
    )

    api = BangumiApi(username="testuser", access_token="test_token")
    result = api.get_ep_collection("1")

    assert result["type"] == 2


@responses.activate
def test_get_me():
    """测试获取当前用户信息"""
    responses.add(
        responses.GET,
        "https://api.bgm.tv/v0/me",
        json={"id": 1, "username": "testuser", "nickname": "Test"},
        status=200,
    )

    api = BangumiApi(username="testuser", access_token="test_token")
    result = api.get_me()

    assert result["username"] == "testuser"


@responses.activate
def test_api_auth_error():
    """测试 API 认证错误"""
    responses.add(
        responses.GET,
        "https://api.bgm.tv/v0/me",
        json={"error": "Unauthorized"},
        status=401,
    )

    api = BangumiApi(username="testuser", access_token="invalid_token")

    with pytest.raises(ValueError, match="认证失败"):
        api.get_me()
