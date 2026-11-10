"""Trakt 整季播出日期：本地数据取不到日期时的兜底取数

不在 bangumi-data 的剧集（如真人剧）无法从本地索引得到该集播出日期，
转换层只能回落到 ``show.year`` 兜底（如「老友记」得到 1994-01-01）。
本文件固定整季取数的触发条件与回填行为：

- 仅当 Trakt 记录与 bangumi-data 都取不到日期时才发起请求；
- 同一季的多条观看记录只请求一次；
- 类型过滤会跳过的条目不请求；
- 取数结果回填到 ``episode.first_aired``，转换层按既有回退链取用。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.services.trakt.client import TraktClient
from app.services.trakt.models import TraktHistoryItem
from app.services.trakt.sync_service import TraktSyncService


def _episode_item(
    trakt_id: str = "1657",
    tmdb_id: str | None = "1668",
    season: int = 6,
    number: int = 6,
    episode_first_aired: str | None = None,
    show_first_aired: str | None = None,
    title: str = "Friends",
) -> TraktHistoryItem:
    """构造一条剧集观看记录（默认无任何播出日期）。"""
    ids: dict[str, object] = {"trakt": trakt_id}
    if tmdb_id is not None:
        ids["tmdb"] = tmdb_id
    show = {"title": title, "year": 1994, "ids": ids}
    if show_first_aired:
        show["first_aired"] = show_first_aired
    episode: dict[str, object] = {
        "season": season,
        "number": number,
        "ids": {"trakt": 1},
    }
    if episode_first_aired:
        episode["first_aired"] = episode_first_aired
    return TraktHistoryItem(
        id=1,
        watched_at="2026-11-09T07:41:00.000Z",
        action="watch",
        type="episode",
        show=show,
        episode=episode,
    )


def _movie_item() -> TraktHistoryItem:
    """构造一条电影观看记录。"""
    return TraktHistoryItem(
        id=2,
        watched_at="2026-11-09T07:41:00.000Z",
        action="watch",
        type="movie",
        movie={"title": "Inception", "ids": {"trakt": 1, "tmdb": 2}},
    )


def _collect(
    items: list[TraktHistoryItem],
    *,
    sync_filter_enabled: bool = True,
    show_genres: dict[str, list[str]] | None = None,
    show_original_titles: dict[str, str] | None = None,
    custom_mappings: dict | None = None,
) -> list[tuple[str, int]]:
    """调用收集逻辑（bangumi-data 视为取不到日期）。"""
    with patch("app.services.trakt.sync_service.bangumi_data") as bd:
        bd.resolve_tmdb_episode.return_value = None
        bd.get_begin_by_tmdb_id.return_value = ""
        bd.get_title_by_tmdb_id.return_value = "Friends"
        return TraktSyncService()._collect_season_air_date_tasks(
            items,
            sync_filter_enabled,
            show_genres or {},
            show_original_titles or {},
            custom_mappings or {},
        )


class TestCollectSeasonAirDateTasks:
    """收集条件：只在本地确实取不到日期时才发起请求。"""

    def test_show_without_local_date_is_collected(self):
        """本地无日期的剧集（真人剧）纳入取数。"""
        assert _collect([_episode_item()]) == [("1657", 6)]

    def test_bangumi_data_date_avoids_request(self):
        """bangumi-data 能给出日期时不请求（零请求优先）。"""
        with patch("app.services.trakt.sync_service.bangumi_data") as bd:
            bd.resolve_tmdb_episode.return_value = None
            bd.get_begin_by_tmdb_id.return_value = "2026-07-05T08:00:00.000Z"
            bd.get_title_by_tmdb_id.return_value = "Friends"
            tasks = TraktSyncService()._collect_season_air_date_tasks(
                [_episode_item()], True, {}, {}, {}
            )
        assert tasks == []

    def test_episode_range_begin_avoids_request(self):
        """bangumi-data 集级起点能给出日期时同样不请求。"""
        from app.utils.bangumi_data.index import TmdbEpisodeMatch

        with patch("app.services.trakt.sync_service.bangumi_data") as bd:
            bd.resolve_tmdb_episode.return_value = TmdbEpisodeMatch(
                subject_id="638497",
                title="正反対な君と僕(第2期)",
                begin="2026-07-05T08:00:00.000Z",
                start_episode=13,
                target_episode=1,
            )
            bd.get_begin_by_tmdb_id.return_value = ""
            tasks = TraktSyncService()._collect_season_air_date_tasks(
                [_episode_item()], True, {}, {}, {}
            )
        assert tasks == []

    def test_episode_first_aired_avoids_request(self):
        """Trakt 记录已带 episode.first_aired 时不请求。"""
        item = _episode_item(episode_first_aired="1999-10-07T00:00:00.000Z")
        assert _collect([item]) == []

    def test_show_first_aired_avoids_request(self):
        """Trakt 记录已带 show.first_aired 时不请求。"""
        item = _episode_item(show_first_aired="1999-09-23T00:00:00.000Z")
        assert _collect([item]) == []

    def test_same_season_is_deduplicated(self):
        """同一季的多条观看记录只请求一次。"""
        items = [
            _episode_item(number=6),
            _episode_item(number=7),
            _episode_item(number=8),
        ]
        assert _collect(items) == [("1657", 6)]

    def test_different_seasons_are_separate_tasks(self):
        """不同季分别取数。"""
        items = [_episode_item(season=6), _episode_item(season=7)]
        assert _collect(items) == [("1657", 6), ("1657", 7)]

    def test_genre_filtered_item_is_not_collected(self):
        """类型过滤会跳过的条目不请求，避免为不参与同步的剧集发请求。"""
        item = _episode_item()
        tasks = _collect([item], show_genres={"1657": ["comedy", "romance"]})
        assert tasks == []

    def test_genre_filtered_item_is_collected_when_mapping_hits(self):
        """命中自定义映射的条目豁免类型过滤，仍需取日期。"""
        item = _episode_item(title="Friends")
        tasks = _collect(
            [item],
            show_genres={"1657": ["comedy", "romance"]},
            custom_mappings={"Friends": "12345"},
        )
        assert tasks == [("1657", 6)]

    def test_animation_genre_is_collected(self):
        """动画类型条目照常取数。"""
        item = _episode_item()
        tasks = _collect([item], show_genres={"1657": ["anime", "comedy"]})
        assert tasks == [("1657", 6)]

    def test_movie_is_not_collected(self):
        """电影记录没有集号概念，不纳入取数。"""
        assert _collect([_movie_item()]) == []

    def test_missing_identifiers_are_skipped(self):
        """缺 Trakt id / 季号 / 集号的条目不纳入取数。"""
        no_trakt = _episode_item(trakt_id="")
        no_tmdb = _episode_item(tmdb_id=None)
        assert _collect([no_trakt]) == []
        assert _collect([no_tmdb]) == [("1657", 6)]

    def test_non_integer_season_is_skipped(self):
        """季号非数字时跳过，不因转换异常中断收集。"""
        item = _episode_item()
        item.episode["season"] = "six"
        assert _collect([item]) == []

    def test_non_integer_episode_number_is_skipped(self):
        """集号非数字时跳过。"""
        item = _episode_item()
        item.episode["number"] = "six"
        assert _collect([item]) == []

    def test_missing_season_or_number_is_skipped(self):
        """缺季号或集号的记录不纳入取数。"""
        for field in ("season", "number"):
            item = _episode_item()
            del item.episode[field]
            assert _collect([item]) == []

    def test_item_without_show_ids_is_skipped(self):
        """show.ids 缺失时不纳入取数。"""
        item = _episode_item()
        item.show.pop("ids")
        assert _collect([item]) == []


class TestFetchSeasonAirDatesBatch:
    """回填行为：把整季数据中的该集日期写回观看记录。"""

    @staticmethod
    def _client(episodes: list[dict]) -> MagicMock:
        client = MagicMock()
        client.get_season_episodes = AsyncMock(return_value=episodes)
        return client

    @pytest.mark.asyncio
    async def test_air_date_is_filled_back(self):
        """该集在整季数据中的 first_aired 回填到观看记录。"""
        item = _episode_item()
        client = self._client(
            [
                {"number": 5, "first_aired": "1999-09-30T00:00:00.000Z"},
                {"number": 6, "first_aired": "1999-10-07T00:00:00.000Z"},
            ]
        )
        await TraktSyncService()._fetch_season_air_dates_batch(
            [("1657", 6)], client, [item]
        )
        assert item.episode["first_aired"] == "1999-10-07T00:00:00.000Z"

    @pytest.mark.asyncio
    async def test_episode_absent_from_season_is_not_filled(self):
        """整季数据里没有该集时保持原样（不写入错误日期）。"""
        item = _episode_item()
        client = self._client(
            [{"number": 5, "first_aired": "1999-09-30T00:00:00.000Z"}]
        )
        await TraktSyncService()._fetch_season_air_dates_batch(
            [("1657", 6)], client, [item]
        )
        assert "first_aired" not in item.episode

    @pytest.mark.asyncio
    async def test_existing_first_aired_is_not_overwritten(self):
        """记录自带 first_aired 时不被整季数据覆盖。"""
        item = _episode_item(episode_first_aired="2000-01-01T00:00:00.000Z")
        client = self._client(
            [{"number": 6, "first_aired": "1999-10-07T00:00:00.000Z"}]
        )
        await TraktSyncService()._fetch_season_air_dates_batch(
            [("1657", 6)], client, [item]
        )
        assert item.episode["first_aired"] == "2000-01-01T00:00:00.000Z"

    @pytest.mark.asyncio
    async def test_season_without_air_dates_is_ignored(self):
        """整季数据缺少 first_aired 字段时不写入任何日期。"""
        item = _episode_item()
        client = self._client([{"number": 6}, {"number": 7}])
        await TraktSyncService()._fetch_season_air_dates_batch(
            [("1657", 6)], client, [item]
        )
        assert "first_aired" not in item.episode

    @pytest.mark.asyncio
    async def test_request_failure_leaves_items_unchanged(self):
        """取数失败时观看记录保持原样，转换层按既有回退链处理。"""
        item = _episode_item()
        client = MagicMock()
        client.get_season_episodes = AsyncMock(side_effect=RuntimeError("network"))
        await TraktSyncService()._fetch_season_air_dates_batch(
            [("1657", 6)], client, [item]
        )
        assert "first_aired" not in item.episode

    @pytest.mark.asyncio
    async def test_auth_error_propagates(self):
        """401 向上传播，触发整体 token 刷新重试。"""
        from app.services.trakt.client import TraktAuthError

        item = _episode_item()
        client = MagicMock()
        client.get_season_episodes = AsyncMock(side_effect=TraktAuthError("401"))
        with pytest.raises(TraktAuthError):
            await TraktSyncService()._fetch_season_air_dates_batch(
                [("1657", 6)], client, [item]
            )

    @pytest.mark.asyncio
    async def test_empty_task_list_makes_no_request(self):
        """没有待取数的季时不发起任何请求。"""
        client = self._client([])
        await TraktSyncService()._fetch_season_air_dates_batch([], client, [])
        client.get_season_episodes.assert_not_called()

    @pytest.mark.asyncio
    async def test_season_entry_with_invalid_number_is_ignored(self):
        """整季数据里集号非数字的条目被跳过，不影响同季其它条目。"""
        item = _episode_item()
        client = self._client(
            [
                {"number": "six", "first_aired": "1999-10-07T00:00:00.000Z"},
                {"number": 6, "first_aired": "1999-10-07T00:00:00.000Z"},
            ]
        )
        await TraktSyncService()._fetch_season_air_dates_batch(
            [("1657", 6)], client, [item]
        )
        assert item.episode["first_aired"] == "1999-10-07T00:00:00.000Z"

    @pytest.mark.asyncio
    async def test_record_without_show_ids_is_not_filled(self):
        """记录缺少 show.ids 时跳过回填，不抛异常。"""
        item = _episode_item()
        item.show.pop("ids")
        client = self._client(
            [{"number": 6, "first_aired": "1999-10-07T00:00:00.000Z"}]
        )
        await TraktSyncService()._fetch_season_air_dates_batch(
            [("1657", 6)], client, [item]
        )
        assert "first_aired" not in item.episode

    @pytest.mark.asyncio
    async def test_record_without_show_is_not_filled(self):
        """记录缺少 show 时跳过回填，不抛异常。"""
        item = _episode_item()
        item.show = None
        client = self._client(
            [{"number": 6, "first_aired": "1999-10-07T00:00:00.000Z"}]
        )
        await TraktSyncService()._fetch_season_air_dates_batch(
            [("1657", 6)], client, [item]
        )
        assert "first_aired" not in item.episode


class TestSeasonAirDateInConversion:
    """转换层：回填后的日期按既有回退链取用。"""

    @pytest.mark.asyncio
    async def test_show_without_local_date_uses_trakt_season_date(self):
        """本地无日期的剧集，转换层取到回填的该集播出日期。"""
        item = _episode_item()
        client = MagicMock()
        client.get_season_episodes = AsyncMock(
            return_value=[{"number": 6, "first_aired": "1999-10-07T00:00:00.000Z"}]
        )
        svc = TraktSyncService()
        with patch("app.services.trakt.sync_service.bangumi_data") as bd:
            bd.resolve_tmdb_episode.return_value = None
            bd.get_begin_by_tmdb_id.return_value = ""
            bd.get_title_by_tmdb_id.return_value = "Friends"
            tasks = svc._collect_season_air_date_tasks([item], True, {}, {}, {})
            await svc._fetch_season_air_dates_batch(tasks, client, [item])
            custom_item = svc._convert_trakt_history_to_custom_item("u", item, {})
        assert item.episode["first_aired"] == "1999-10-07T00:00:00.000Z"
        assert custom_item.release_date == "1999-10-07"

    @pytest.mark.asyncio
    async def test_bangumi_data_path_makes_no_request(self):
        """bangumi-data 能给出日期时不发起取数请求。"""
        item = _episode_item()
        client = MagicMock()
        client.get_season_episodes = AsyncMock(return_value=[])
        svc = TraktSyncService()
        with patch("app.services.trakt.sync_service.bangumi_data") as bd:
            bd.resolve_tmdb_episode.return_value = None
            bd.get_begin_by_tmdb_id.return_value = "2026-07-05T08:00:00.000Z"
            bd.get_title_by_tmdb_id.return_value = "Friends"
            tasks = svc._collect_season_air_date_tasks([item], True, {}, {}, {})
            await svc._fetch_season_air_dates_batch(tasks, client, [item])
            custom_item = svc._convert_trakt_history_to_custom_item("u", item, {})
        client.get_season_episodes.assert_not_called()
        assert custom_item.release_date == "2026-07-05"

    @pytest.mark.asyncio
    async def test_year_fallback_remains_when_season_fetch_fails(self):
        """整季取数失败时仍按 show.year 兜底，不产生错误日期。"""
        item = _episode_item()
        client = MagicMock()
        client.get_season_episodes = AsyncMock(return_value=[])
        svc = TraktSyncService()
        with patch("app.services.trakt.sync_service.bangumi_data") as bd:
            bd.resolve_tmdb_episode.return_value = None
            bd.get_begin_by_tmdb_id.return_value = ""
            bd.get_title_by_tmdb_id.return_value = "Friends"
            tasks = svc._collect_season_air_date_tasks([item], True, {}, {}, {})
            await svc._fetch_season_air_dates_batch(tasks, client, [item])
            custom_item = svc._convert_trakt_history_to_custom_item("u", item, {})
        assert custom_item.release_date == "1994-01-01"

    @pytest.mark.asyncio
    async def test_release_date_uses_date_only_form(self):
        """release_date 按 CustomItem 约定取 YYYY-MM-DD，不带时间与时区。

        Trakt 的 first_aired 是带时区的完整时间戳，转换层只取日期部分，
        与其它驱动（如 Emby 的 PremiereDate）和文档约定一致。
        """
        item = _episode_item(episode_first_aired="1999-10-07T01:00:00.000Z")
        svc = TraktSyncService()
        with patch("app.services.trakt.sync_service.bangumi_data") as bd:
            bd.get_title_by_tmdb_id.return_value = "Friends"
            custom_item = svc._convert_trakt_history_to_custom_item("u", item, {})
        assert custom_item.release_date == "1999-10-07"

    @pytest.mark.asyncio
    async def test_show_first_aired_uses_date_only_form(self):
        """show.first_aired 同样只取日期部分。"""
        item = _episode_item(show_first_aired="1999-09-23T01:00:00.000Z")
        svc = TraktSyncService()
        with patch("app.services.trakt.sync_service.bangumi_data") as bd:
            bd.get_title_by_tmdb_id.return_value = "Friends"
            custom_item = svc._convert_trakt_history_to_custom_item("u", item, {})
        assert custom_item.release_date == "1999-09-23"


class TestRealRecordShape:
    """真实观看记录形态：show 携带多个 provider id，episode 字段齐全。

    取自生产同步的实际记录（Friends S06E07 与正反対な君と僕 S01E13）：
    show.ids 含 imdb / plex / slug / tmdb / tvdb / trakt，episode 带 title。
    """

    @staticmethod
    def _friends_record() -> TraktHistoryItem:
        return TraktHistoryItem(
            id=1,
            watched_at="2026-11-09T23:52:00.000Z",
            action="watch",
            type="episode",
            show={
                "title": "Friends",
                "year": 1994,
                "ids": {
                    "imdb": "tt0108778",
                    "plex": {"guid": "5d9c086d2192ba001f3101c6", "slug": "friends-1"},
                    "slug": "friends",
                    "tmdb": 1668,
                    "tvdb": 79168,
                    "trakt": 1657,
                },
            },
            episode={
                "season": 6,
                "number": 7,
                "title": "The One Where Phoebe Runs",
                "first_aired": "1999-11-12T01:00:00.000Z",
            },
        )

    @staticmethod
    def _polar_opposites_record() -> TraktHistoryItem:
        return TraktHistoryItem(
            id=2,
            watched_at="2026-11-09T23:51:00.000Z",
            action="watch",
            type="episode",
            show={
                "title": "You and I Are Polar Opposites",
                "year": 2026,
                "ids": {
                    "imdb": "tt36034547",
                    "plex": {
                        "guid": "6745cc419b783bf0880c593f",
                        "slug": "you-and-i-are-polar-opposites",
                    },
                    "slug": "you-and-i-are-polar-opposites",
                    "tmdb": 278043,
                    "tvdb": 457078,
                    "trakt": 264998,
                },
            },
            episode={
                "season": 1,
                "number": 13,
                "title": "Christmas Eve",
                "first_aired": None,
            },
        )

    def test_record_with_first_aired_needs_no_request(self):
        """记录自带 first_aired 时不发起取数请求。"""
        assert _collect([self._friends_record()]) == []

    def test_record_without_first_aired_uses_trakt_season(self):
        """记录缺 first_aired 且本地无日期时按 Trakt 季取数。"""
        item = self._polar_opposites_record()
        with patch("app.services.trakt.sync_service.bangumi_data") as bd:
            bd.resolve_tmdb_episode.return_value = None
            bd.get_begin_by_tmdb_id.return_value = ""
            tasks = TraktSyncService()._collect_season_air_date_tasks(
                [item], True, {}, {}, {}
            )
        assert tasks == [("264998", 1)]

    def test_extra_provider_ids_do_not_affect_collection(self):
        """show.ids 里的其它 provider id 不影响收集结果。"""
        item = self._polar_opposites_record()
        with patch("app.services.trakt.sync_service.bangumi_data") as bd:
            bd.resolve_tmdb_episode.return_value = None
            bd.get_begin_by_tmdb_id.return_value = ""
            tasks = TraktSyncService()._collect_season_air_date_tasks(
                [item], True, {}, {}, {}
            )
        assert tasks == [("264998", 1)]


class TestClientGetSeasonEpisodes:
    """客户端整季端点：响应形状与异常处理。"""

    @pytest.fixture
    def mock_config(self):
        with patch("app.services.trakt.client.config_manager") as mock_cm:
            mock_cm.get_trakt_config.return_value = {"client_id": "test_client_id"}
            yield mock_cm

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_episode_list(self, mock_config):
        """正常响应返回剧集列表。"""
        route = respx.get("https://api.trakt.tv/shows/1657/seasons/6").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "season": 6,
                        "number": 1,
                        "first_aired": "1999-09-23T00:00:00.000Z",
                    },
                    {
                        "season": 6,
                        "number": 2,
                        "first_aired": "1999-09-30T00:00:00.000Z",
                    },
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
            result = await client.get_season_episodes("1657", 6)

        assert len(result) == 2
        assert result[0]["number"] == 1
        assert result[0]["first_aired"] == "1999-09-23T00:00:00.000Z"
        assert route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_list_response_returns_empty(self, mock_config):
        """响应不是数组时返回空列表。"""
        respx.get("https://api.trakt.tv/shows/1657/seasons/6").mock(
            return_value=httpx.Response(
                200,
                json={"error": "not a list"},
                headers={
                    "Content-Type": "application/json",
                    "X-RateLimit-Remaining": "999",
                    "X-RateLimit-Reset": "9999999999",
                },
            )
        )

        client = TraktClient(access_token="test_token")
        async with client:
            result = await client.get_season_episodes("1657", 6)

        assert result == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_request_error_returns_empty(self, mock_config):
        """请求失败时返回空列表，不抛出异常。"""
        respx.get("https://api.trakt.tv/shows/1657/seasons/6").mock(
            side_effect=httpx.ConnectError("boom")
        )

        client = TraktClient(access_token="test_token")
        async with client:
            result = await client.get_season_episodes("1657", 6)

        assert result == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_dict_entries_are_filtered(self, mock_config):
        """数组中的非 dict 元素被过滤掉。"""
        respx.get("https://api.trakt.tv/shows/1657/seasons/6").mock(
            return_value=httpx.Response(
                200,
                json=[{"number": 1}, "not-a-dict"],
                headers={
                    "Content-Type": "application/json",
                    "X-RateLimit-Remaining": "999",
                    "X-RateLimit-Reset": "9999999999",
                },
            )
        )

        client = TraktClient(access_token="test_token")
        async with client:
            result = await client.get_season_episodes("1657", 6)

        assert result == [{"number": 1}]
