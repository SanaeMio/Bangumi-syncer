"""TraktClient：_make_request、速率限制、部分 API 错误路径（mock httpx）。"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.trakt.client import TraktClient, TraktClientFactory


@pytest.fixture
def client():
    with patch(
        "app.services.trakt.client.config_manager.get_trakt_config",
        return_value={"client_id": "cid"},
    ):
        return TraktClient("access-token-xyz")


@pytest.mark.asyncio
async def test_make_request_200_and_204(client):
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"x": 1}
    ok.headers = httpx.Headers({})
    no_content = MagicMock(status_code=204)
    no_content.headers = httpx.Headers({})
    client._client = MagicMock()
    client._client.request = AsyncMock(side_effect=[ok, no_content])
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            assert await client._make_request("GET", "/a") == {"x": 1}
            assert await client._make_request("GET", "/b") == {}


@pytest.mark.asyncio
async def test_make_request_401_raises(client):
    resp = MagicMock(status_code=401)
    resp.headers = httpx.Headers({})
    client._client = MagicMock()
    client._client.request = AsyncMock(return_value=resp)
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            with pytest.raises(ValueError, match="认证失败"):
                await client._make_request("GET", "/x")


@pytest.mark.asyncio
async def test_make_request_429_retries_then_ok(client):
    r429 = MagicMock(status_code=429)
    r429.headers = httpx.Headers({"Retry-After": "0"})
    r200 = MagicMock(status_code=200)
    r200.json.return_value = []
    r200.headers = httpx.Headers({})
    client._client = MagicMock()
    client._client.request = AsyncMock(side_effect=[r429, r200])
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            with patch(
                "app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock
            ):
                out = await client._make_request("GET", "/sync/history")
    assert out == []


@pytest.mark.asyncio
async def test_make_request_429_invalid_retry_after_uses_default(client):
    r429 = MagicMock(status_code=429)
    r429.headers = httpx.Headers({"Retry-After": "bad"})
    r200 = MagicMock(status_code=200)
    r200.json.return_value = {}
    r200.headers = httpx.Headers({})
    client._client = MagicMock()
    client._client.request = AsyncMock(side_effect=[r429, r200])
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            with patch(
                "app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock
            ):
                await client._make_request("GET", "/y")


@pytest.mark.asyncio
async def test_make_request_other_status_retries_then_none(client):
    r500 = MagicMock(status_code=500, text="err")
    r500.headers = httpx.Headers({})
    client._client = MagicMock()
    client._client.request = AsyncMock(return_value=r500)
    client.max_retries = 2
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            with patch(
                "app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock
            ):
                assert await client._make_request("GET", "/z") is None


@pytest.mark.asyncio
async def test_make_request_request_error_retries(client):
    client._client = MagicMock()
    client._client.request = AsyncMock(
        side_effect=httpx.RequestError("x", request=MagicMock())
    )
    client.max_retries = 2
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            with patch(
                "app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock
            ):
                assert await client._make_request("GET", "/e") is None


@pytest.mark.asyncio
async def test_make_request_generic_exception_retries(client):
    client._client = MagicMock()
    client._client.request = AsyncMock(side_effect=RuntimeError("oops"))
    client.max_retries = 2
    with patch.object(client, "_check_rate_limit", new_callable=AsyncMock):
        with patch.object(client, "_update_rate_limit"):
            with patch(
                "app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock
            ):
                assert await client._make_request("GET", "/g") is None


def test_update_rate_limit_invalid_headers(client):
    h = httpx.Headers({"X-RateLimit-Remaining": "nan", "X-RateLimit-Reset": "x"})
    client._update_rate_limit(h)


@pytest.mark.asyncio
async def test_check_rate_limit_waits_when_low_quota(client):
    client.rate_limit_remaining = 5
    client.rate_limit_reset = time.time() + 0.05
    with patch("app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock) as sl:
        await client._check_rate_limit()
    sl.assert_awaited()


@pytest.mark.asyncio
async def test_get_watched_history_skips_bad_items(client):
    with patch.object(
        client,
        "_make_request",
        new_callable=AsyncMock,
        return_value=[{"invalid": True}],
    ):
        items = await client.get_watched_history()
    assert items == []


# ===== get_watched_history extra tests =====


@pytest.mark.asyncio
async def test_get_watched_history_empty_data(client):
    """_make_request返回None或空列表"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, return_value=None
    ):
        assert await client.get_watched_history() == []

    with patch.object(
        client, "_make_request", new_callable=AsyncMock, return_value="not a list"
    ):
        assert await client.get_watched_history() == []


@pytest.mark.asyncio
async def test_get_watched_history_with_start_date(client):
    """带start_date参数"""
    from datetime import datetime

    with patch.object(
        client, "_make_request", new_callable=AsyncMock, return_value=[]
    ) as mock_req:
        await client.get_watched_history(start_date=datetime(2024, 1, 1))
        call_args = mock_req.call_args
        assert call_args[0][0] == "GET"
        assert call_args[0][1] == "/sync/history"
        assert "start_at" in call_args[0][2]


@pytest.mark.asyncio
async def test_get_watched_history_exception(client):
    """获取历史记录时抛出异常"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, side_effect=RuntimeError("err")
    ):
        assert await client.get_watched_history() == []


# ===== get_all_watched_history extra tests =====


@pytest.mark.asyncio
async def test_get_all_watched_history_pagination(client):
    """分页获取并去重"""
    from app.services.trakt.models import TraktHistoryItem

    item1 = TraktHistoryItem(
        id=1,
        watched_at="2024-01-01T00:00:00.000Z",
        action="watch",
        type="episode",
        episode={"season": 1, "number": 1, "ids": {"trakt": 100}},
        show={"title": "Show", "ids": {"trakt": 200}},
    )
    item2 = TraktHistoryItem(
        id=2,
        watched_at="2024-01-02T00:00:00.000Z",
        action="watch",
        type="episode",
        episode={"season": 1, "number": 2, "ids": {"trakt": 101}},
        show={"title": "Show", "ids": {"trakt": 200}},
    )

    # page 1 must return 1000 items to trigger page 2
    full_page = [item1] * 1000

    async def mock_history(start_date=None, limit=1000, page=1):
        if page == 1:
            return full_page
        if page == 2:
            return [item2]
        return []

    with patch.object(client, "get_watched_history", side_effect=mock_history):
        with patch("app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.get_all_watched_history(max_pages=3)

    # 1000 copies of item1 + 1 copy of item2 → dedup → 2 unique
    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_all_watched_history_dedup(client):
    """去重逻辑"""
    from app.services.trakt.models import TraktHistoryItem

    item = TraktHistoryItem(
        id=1,
        watched_at="2024-01-01T00:00:00.000Z",
        action="watch",
        type="episode",
        episode={"season": 1, "number": 1, "ids": {"trakt": 100}},
        show={"title": "Show", "ids": {"trakt": 200}},
    )

    # page 1 returns 1000 of the same item, page 2 returns 1000 of the same item
    full_page = [item] * 1000

    call_count = 0

    async def mock_history(start_date=None, limit=1000, page=1):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return full_page
        return []

    with patch.object(client, "get_watched_history", side_effect=mock_history):
        with patch("app.services.trakt.client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.get_all_watched_history(max_pages=3)

    assert len(result) == 1


@pytest.mark.asyncio
async def test_get_all_watched_history_exception_in_page(client):
    """某页获取失败时停止分页"""

    async def mock_history(start_date=None, limit=1000, page=1):
        if page == 1:
            raise RuntimeError("page error")
        return []

    with patch.object(client, "get_watched_history", side_effect=mock_history):
        result = await client.get_all_watched_history(max_pages=3)

    assert result == []


# ===== get_ratings extra tests =====


@pytest.mark.asyncio
async def test_get_ratings_empty(client):
    """评分为空"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, return_value=None
    ):
        assert await client.get_ratings() == []


@pytest.mark.asyncio
async def test_get_ratings_exception(client):
    """获取评分异常"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, side_effect=RuntimeError("err")
    ):
        assert await client.get_ratings() == []


@pytest.mark.asyncio
async def test_get_ratings_skips_bad_items(client):
    """跳过解析失败的评分项"""
    with patch.object(
        client,
        "_make_request",
        new_callable=AsyncMock,
        return_value=[{"invalid": True}],
    ):
        assert await client.get_ratings() == []


# ===== get_collection extra tests =====


@pytest.mark.asyncio
async def test_get_collection_empty(client):
    """收藏为空"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, return_value=None
    ):
        assert await client.get_collection() == []


@pytest.mark.asyncio
async def test_get_collection_exception(client):
    """获取收藏异常"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, side_effect=RuntimeError("err")
    ):
        assert await client.get_collection() == []


@pytest.mark.asyncio
async def test_get_collection_skips_bad_items(client):
    """跳过解析失败的收藏项"""
    with patch.object(
        client,
        "_make_request",
        new_callable=AsyncMock,
        return_value=[{"invalid": True}],
    ):
        assert await client.get_collection() == []


# ===== get_user_profile extra tests =====


@pytest.mark.asyncio
async def test_get_user_profile_returns_non_dict(client):
    """返回非dict时返回None"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, return_value=[1, 2]
    ):
        assert await client.get_user_profile() is None


@pytest.mark.asyncio
async def test_get_user_profile_exception(client):
    """获取用户信息异常"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, side_effect=RuntimeError("err")
    ):
        assert await client.get_user_profile() is None


# ===== get_movie_info extra tests =====


@pytest.mark.asyncio
async def test_get_movie_info_non_dict(client):
    """返回非dict时返回None"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, return_value=[1]
    ):
        assert await client.get_movie_info(123) is None


@pytest.mark.asyncio
async def test_get_movie_info_exception(client):
    """获取电影信息异常"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, side_effect=RuntimeError("err")
    ):
        assert await client.get_movie_info(123) is None


# ===== get_show_info extra tests =====


@pytest.mark.asyncio
async def test_get_show_info_non_dict(client):
    """返回非dict时返回None"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, return_value=None
    ):
        assert await client.get_show_info(123) is None


@pytest.mark.asyncio
async def test_get_show_info_exception(client):
    """获取剧集信息异常"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, side_effect=RuntimeError("err")
    ):
        assert await client.get_show_info(123) is None


# ===== get_episode_info extra tests =====


@pytest.mark.asyncio
async def test_get_episode_info_non_dict(client):
    """返回非dict时返回None"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, return_value=None
    ):
        assert await client.get_episode_info(1, 1, 1) is None


@pytest.mark.asyncio
async def test_get_episode_info_exception(client):
    """获取剧集详情异常"""
    with patch.object(
        client, "_make_request", new_callable=AsyncMock, side_effect=RuntimeError("err")
    ):
        assert await client.get_episode_info(1, 1, 1) is None


# ===== test_connection extra tests =====


@pytest.mark.asyncio
async def test_test_connection_exception(client):
    """测试连接时异常"""
    with patch.object(
        client,
        "get_user_profile",
        new_callable=AsyncMock,
        side_effect=RuntimeError("err"),
    ):
        assert await client.test_connection() is False


# ===== TraktClientFactory extra tests =====


@pytest.mark.asyncio
async def test_client_factory_exception():
    """创建客户端时抛出异常"""
    with patch(
        "app.services.trakt.client.TraktClient",
        side_effect=RuntimeError("init error"),
    ):
        result = await TraktClientFactory.create_client("token")
    assert result is None
