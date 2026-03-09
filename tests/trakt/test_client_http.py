"""
Trakt 客户端 HTTP Mock 测试
使用 respx 库模拟 Trakt API 调用 (httpx)
"""

from unittest.mock import patch

import httpx
import pytest
import respx

from app.services.trakt.client import TraktClient, TraktClientFactory


@pytest.fixture
def mock_config():
    """Mock config_manager"""
    with patch("app.services.trakt.client.config_manager") as mock_cm:
        mock_cm.get_trakt_config.return_value = {"client_id": "test_client_id"}
        yield mock_cm


@pytest.mark.asyncio
@respx.mock
async def test_get_user_profile(mock_config):
    """测试获取用户信息"""
    mock_route = respx.get("https://api.trakt.tv/users/me").mock(
        return_value=httpx.Response(
            200,
            json={"username": "testuser", "id": 12345},
            headers={
                "Content-Type": "application/json",
                "X-RateLimit-Remaining": "999",
                "X-RateLimit-Reset": "9999999999",
            },
        )
    )

    client = TraktClient(access_token="test_token")
    async with client:
        result = await client.get_user_profile()

    assert result["username"] == "testuser"
    assert result["id"] == 12345
    assert mock_route.called


@pytest.mark.asyncio
@respx.mock
async def test_get_watched_history(mock_config):
    """测试获取观看历史"""
    mock_route = respx.get("https://api.trakt.tv/sync/history").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 123456,
                    "watched_at": "2024-01-01T12:00:00.000Z",
                    "action": "watch",
                    "type": "episode",
                    "episode": {
                        "season": 1,
                        "number": 1,
                        "title": "Episode 1",
                        "ids": {"trakt": 123, "tvdb": 123456},
                    },
                    "show": {"title": "Test Show", "ids": {"trakt": 456}},
                }
            ],
            headers={
                "Content-Type": "application/json",
                "X-RateLimit-Remaining": "999",
                "X-RateLimit-Reset": "9999999999",
            },
        )
    )

    client = TraktClient(access_token="test_token")
    async with client:
        result = await client.get_watched_history()

    assert len(result) == 1
    assert result[0].show["title"] == "Test Show"


@pytest.mark.asyncio
@respx.mock
async def test_get_ratings(mock_config):
    """测试获取用户评分"""
    mock_route = respx.get("https://api.trakt.tv/sync/ratings/shows").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "rated_at": "2024-01-01T12:00:00.000Z",
                    "rating": 8,
                    "type": "show",
                    "show": {"title": "Test Show", "ids": {"trakt": 123}},
                }
            ],
            headers={
                "Content-Type": "application/json",
                "X-RateLimit-Remaining": "999",
                "X-RateLimit-Reset": "9999999999",
            },
        )
    )

    client = TraktClient(access_token="test_token")
    async with client:
        result = await client.get_ratings("shows")

    assert len(result) == 1
    assert result[0].rating == 8


@pytest.mark.asyncio
@respx.mock
async def test_get_collection(mock_config):
    """测试获取用户收藏"""
    mock_route = respx.get("https://api.trakt.tv/sync/collection/shows").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "collected_at": "2024-01-01T12:00:00.000Z",
                    "type": "show",
                    "show": {"title": "Test Show", "ids": {"trakt": 123}},
                }
            ],
            headers={
                "Content-Type": "application/json",
                "X-RateLimit-Remaining": "999",
                "X-RateLimit-Reset": "9999999999",
            },
        )
    )

    client = TraktClient(access_token="test_token")
    async with client:
        result = await client.get_collection("shows")

    assert len(result) == 1
    assert result[0].show["title"] == "Test Show"


@pytest.mark.asyncio
@respx.mock
async def test_get_movie_info(mock_config):
    """测试获取电影信息"""
    mock_route = respx.get("https://api.trakt.tv/movies/123").mock(
        return_value=httpx.Response(
            200,
            json={
                "title": "Test Movie",
                "year": 2024,
                "ids": {"trakt": 123, "imdb": "tt1234567"},
            },
            headers={
                "Content-Type": "application/json",
                "X-RateLimit-Remaining": "999",
                "X-RateLimit-Reset": "9999999999",
            },
        )
    )

    client = TraktClient(access_token="test_token")
    async with client:
        result = await client.get_movie_info(123)

    assert result["title"] == "Test Movie"
    assert result["year"] == 2024


@pytest.mark.asyncio
@respx.mock
async def test_get_show_info(mock_config):
    """测试获取剧集信息"""
    mock_route = respx.get("https://api.trakt.tv/shows/123").mock(
        return_value=httpx.Response(
            200,
            json={
                "title": "Test Show",
                "year": 2024,
                "ids": {"trakt": 123, "tvdb": 123456},
            },
            headers={
                "Content-Type": "application/json",
                "X-RateLimit-Remaining": "999",
                "X-RateLimit-Reset": "9999999999",
            },
        )
    )

    client = TraktClient(access_token="test_token")
    async with client:
        result = await client.get_show_info(123)

    assert result["title"] == "Test Show"


@pytest.mark.asyncio
@respx.mock
async def test_get_episode_info(mock_config):
    """测试获取剧集详情"""
    mock_route = respx.get("https://api.trakt.tv/shows/123/seasons/1/episodes/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "season": 1,
                "number": 1,
                "title": "Episode 1",
                "show": {"title": "Test Show"},
            },
            headers={
                "Content-Type": "application/json",
                "X-RateLimit-Remaining": "999",
                "X-RateLimit-Reset": "9999999999",
            },
        )
    )

    client = TraktClient(access_token="test_token")
    async with client:
        result = await client.get_episode_info(123, 1, 1)

    assert result["title"] == "Episode 1"


@pytest.mark.asyncio
@respx.mock
async def test_test_connection(mock_config):
    """测试连接"""
    mock_route = respx.get("https://api.trakt.tv/users/me").mock(
        return_value=httpx.Response(
            200,
            json={"username": "testuser"},
            headers={
                "Content-Type": "application/json",
                "X-RateLimit-Remaining": "999",
                "X-RateLimit-Reset": "9999999999",
            },
        )
    )

    client = TraktClient(access_token="test_token")
    async with client:
        result = await client.test_connection()

    assert result is True


@pytest.mark.asyncio
@respx.mock
async def test_test_connection_failure(mock_config):
    """测试连接失败"""
    mock_route = respx.get("https://api.trakt.tv/users/me").mock(
        return_value=httpx.Response(
            401,
            json={"error": "Unauthorized"},
            headers={
                "Content-Type": "application/json",
                "X-RateLimit-Remaining": "999",
                "X-RateLimit-Reset": "9999999999",
            },
        )
    )

    client = TraktClient(access_token="invalid_token")
    async with client:
        result = await client.test_connection()

    assert result is False


from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_client_factory(mock_config):
    """测试客户端工厂"""
    with patch("app.services.trakt.client.TraktClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.test_connection.return_value = True
        mock_client.close.return_value = None
        mock_client_cls.return_value = mock_client

        result = await TraktClientFactory.create_client("test_token")

        assert result is not None


@pytest.mark.asyncio
async def test_client_factory_failure(mock_config):
    """测试客户端工厂 - 连接失败"""
    with patch("app.services.trakt.client.TraktClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.test_connection.return_value = False
        mock_client.close.return_value = None
        mock_client_cls.return_value = mock_client

        result = await TraktClientFactory.create_client("invalid_token")

        assert result is None


def test_ensure_client(mock_config):
    """测试确保客户端初始化"""
    import asyncio

    client = TraktClient(access_token="test_token")

    # 在事件循环中测试
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(client._ensure_client())
        assert client._client is not None
    finally:
        loop.run_until_complete(client.close())
        loop.close()


def test_close_client(mock_config):
    """测试关闭客户端"""
    import asyncio

    client = TraktClient(access_token="test_token")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(client._ensure_client())
        assert client._client is not None

        loop.run_until_complete(client.close())
        assert client._client is None
    finally:
        loop.close()


def test_rate_limit_update(mock_config):
    """测试速率限制更新"""
    client = TraktClient(access_token="test_token")

    # 模拟 httpx Headers
    headers = httpx.Headers(
        {
            "X-RateLimit-Remaining": "500",
            "X-RateLimit-Reset": "9999999999",
        }
    )

    client._update_rate_limit(headers)

    assert client.rate_limit_remaining == 500
