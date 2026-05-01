"""
Trakt 同步服务测试
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.trakt.models import TraktHistoryItem, TraktSyncResult
from app.services.trakt.sync_service import TraktSyncService


def _make_service():
    return TraktSyncService()


def _make_episode_item(
    show_tmdb="123",
    ep_season=1,
    ep_number=1,
    ep_first_aired=None,
    show_first_aired=None,
    watched_at="2024-06-01T12:00:00.000Z",
    trakt_id=100,
):
    show = {"ids": {"tmdb": show_tmdb}, "title": "Test Show"}
    episode = {"season": ep_season, "number": ep_number, "ids": {"trakt": trakt_id}}
    if ep_first_aired:
        episode["first_aired"] = ep_first_aired
    if show_first_aired:
        show["first_aired"] = show_first_aired
    return TraktHistoryItem(
        id=1,
        watched_at=watched_at,
        action="watch",
        type="episode",
        show=show,
        episode=episode,
    )


def _make_movie_item(
    title="Test Movie",
    tmdb="456",
    released="2024-01-15",
    year=None,
    watched_at="2024-06-01T12:00:00.000Z",
    trakt_id=200,
    original_title=None,
):
    movie = {"title": title, "ids": {"trakt": trakt_id, "tmdb": tmdb}}
    if released:
        movie["released"] = released
    if year:
        movie["year"] = year
    if original_title:
        movie["original_title"] = original_title
    return TraktHistoryItem(
        id=2,
        watched_at=watched_at,
        action="watch",
        type="movie",
        movie=movie,
    )


class TestSyncUserTraktData:
    """sync_user_trakt_data 主流程测试"""

    @pytest.mark.asyncio
    async def test_no_config(self):
        service = _make_service()
        with patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth:
            mock_auth.get_user_trakt_config.return_value = None
            result = await service.sync_user_trakt_data("user1")
            assert result.success is False
            assert "配置不存在" in result.message

    @pytest.mark.asyncio
    async def test_no_access_token(self):
        service = _make_service()
        with patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth:
            cfg = MagicMock()
            cfg.access_token = None
            mock_auth.get_user_trakt_config.return_value = cfg
            result = await service.sync_user_trakt_data("user1")
            assert result.success is False
            assert "未授权" in result.message

    @pytest.mark.asyncio
    async def test_token_expired_refresh_fail(self):
        service = _make_service()
        with patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth:
            cfg = MagicMock()
            cfg.access_token = "tok"
            cfg.is_token_expired.return_value = True
            mock_auth.get_user_trakt_config.return_value = cfg
            mock_auth.refresh_token = AsyncMock(return_value=False)
            result = await service.sync_user_trakt_data("user1")
            assert result.success is False
            assert "刷新失败" in result.message

    @pytest.mark.asyncio
    async def test_token_expired_refresh_success_none_config(self):
        service = _make_service()
        with patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth:
            cfg = MagicMock()
            cfg.access_token = "tok"
            cfg.is_token_expired.return_value = True
            mock_auth.get_user_trakt_config.side_effect = [cfg, None]
            mock_auth.refresh_token = AsyncMock(return_value=True)
            result = await service.sync_user_trakt_data("user1")
            assert result.success is False

    @pytest.mark.asyncio
    async def test_create_client_fail(self):
        service = _make_service()
        with (
            patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth,
            patch("app.services.trakt.sync_service.TraktClientFactory") as mock_factory,
        ):
            cfg = MagicMock()
            cfg.access_token = "tok"
            cfg.is_token_expired.return_value = False
            mock_auth.get_user_trakt_config.return_value = cfg
            mock_factory.create_client = AsyncMock(return_value=None)
            result = await service.sync_user_trakt_data("user1")
            assert result.success is False
            assert "创建 Trakt 客户端失败" in result.message

    @pytest.mark.asyncio
    async def test_sync_exception_outer(self):
        """覆盖 lines 174-176: 外层 try/except"""
        service = _make_service()
        with (
            patch("app.services.trakt.sync_service.trakt_auth_service") as mock_auth,
            patch("app.services.trakt.sync_service.TraktClientFactory") as mock_factory,
            patch.object(
                service,
                "_sync_watched_history",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
        ):
            cfg = MagicMock()
            cfg.access_token = "tok"
            cfg.is_token_expired.return_value = False
            mock_auth.get_user_trakt_config.return_value = cfg
            mock_client = AsyncMock()
            mock_client.close = AsyncMock()
            mock_factory.create_client = AsyncMock(return_value=mock_client)
            result = await service.sync_user_trakt_data("user1")
            assert result.success is False
            assert "同步失败" in result.message


class TestSyncWatchedHistory:
    """_sync_watched_history 内部逻辑测试"""

    @pytest.mark.asyncio
    async def test_no_history(self):
        service = _make_service()
        mock_client = AsyncMock()
        mock_client.get_all_watched_history = AsyncMock(return_value=[])
        result = await service._sync_watched_history(
            "u", mock_client, MagicMock(), False
        )
        assert result.success is True
        assert result.synced_count == 0

    @pytest.mark.asyncio
    async def test_episode_item_synced(self):
        """剧集正常同步"""
        service = _make_service()
        item = _make_episode_item()
        mock_client = AsyncMock()
        mock_client.get_all_watched_history = AsyncMock(return_value=[item])
        with (
            patch("app.services.trakt.sync_service.database_manager") as mock_db,
            patch.object(
                service,
                "_convert_trakt_history_to_custom_item",
                return_value=MagicMock(
                    media_type="episode",
                    title="T",
                    season=1,
                    episode=1,
                ),
            ),
            patch("app.services.trakt.sync_service.sync_service") as mock_sync,
        ):
            mock_db.get_trakt_synced_set.return_value = set()
            mock_sync.sync_custom_item_async = AsyncMock(return_value="task1")
            result = await service._sync_watched_history(
                "u", mock_client, MagicMock(), False
            )
            assert result.synced_count == 1

    @pytest.mark.asyncio
    async def test_already_synced_skip(self):
        """覆盖 lines 245-246: 已同步条目跳过"""
        service = _make_service()
        item = _make_episode_item()
        mock_client = AsyncMock()
        mock_client.get_all_watched_history = AsyncMock(return_value=[item])
        with patch("app.services.trakt.sync_service.database_manager") as mock_db:
            mock_db.get_trakt_synced_set.return_value = {
                (item.trakt_item_id, item.watched_timestamp)
            }
            result = await service._sync_watched_history(
                "u", mock_client, MagicMock(), False
            )
            assert result.skipped_count == 1
            assert result.synced_count == 0

    @pytest.mark.asyncio
    async def test_custom_item_none_skip(self):
        """覆盖 lines 254-255: 转换返回 None"""
        service = _make_service()
        item = _make_episode_item()
        mock_client = AsyncMock()
        mock_client.get_all_watched_history = AsyncMock(return_value=[item])
        with (
            patch("app.services.trakt.sync_service.database_manager") as mock_db,
            patch.object(
                service,
                "_convert_trakt_history_to_custom_item",
                return_value=None,
            ),
        ):
            mock_db.get_trakt_synced_set.return_value = set()
            result = await service._sync_watched_history(
                "u", mock_client, MagicMock(), False
            )
            assert result.skipped_count == 1

    @pytest.mark.asyncio
    async def test_item_sync_exception(self):
        """覆盖 lines 279-281: 单条同步异常"""
        service = _make_service()
        item = _make_episode_item()
        mock_client = AsyncMock()
        mock_client.get_all_watched_history = AsyncMock(return_value=[item])
        with (
            patch("app.services.trakt.sync_service.database_manager") as mock_db,
            patch.object(
                service,
                "_convert_trakt_history_to_custom_item",
                return_value=MagicMock(
                    media_type="episode",
                    title="T",
                    season=1,
                    episode=1,
                ),
            ),
            patch("app.services.trakt.sync_service.sync_service") as mock_sync,
        ):
            mock_db.get_trakt_synced_set.return_value = set()
            mock_sync.sync_custom_item_async = AsyncMock(
                side_effect=RuntimeError("sync error")
            )
            result = await service._sync_watched_history(
                "u", mock_client, MagicMock(), False
            )
            assert result.error_count == 1

    @pytest.mark.asyncio
    async def test_movie_filter_no_title_no_tmdb(self):
        """覆盖 lines 230-232: 电影缺少标题和TMDB"""
        service = _make_service()
        item = TraktHistoryItem(
            id=1,
            watched_at="2024-06-01T12:00:00Z",
            action="watch",
            type="movie",
            movie={"title": "", "ids": {}},
        )
        mock_client = AsyncMock()
        mock_client.get_all_watched_history = AsyncMock(return_value=[item])
        with patch("app.services.trakt.sync_service.database_manager") as mock_db:
            mock_db.get_trakt_synced_set.return_value = set()
            result = await service._sync_watched_history(
                "u", mock_client, MagicMock(), False
            )
            assert result.skipped_count == 1

    @pytest.mark.asyncio
    async def test_unsupported_type_skip(self):
        """覆盖 line 232: 不支持的类型"""
        service = _make_service()
        item = TraktHistoryItem(
            id=1,
            watched_at="2024-06-01T12:00:00Z",
            action="watch",
            type="show",
        )
        mock_client = AsyncMock()
        mock_client.get_all_watched_history = AsyncMock(return_value=[item])
        with patch("app.services.trakt.sync_service.database_manager") as mock_db:
            mock_db.get_trakt_synced_set.return_value = set()
            result = await service._sync_watched_history(
                "u", mock_client, MagicMock(), False
            )
            assert result.skipped_count == 1

    @pytest.mark.asyncio
    async def test_outer_exception(self):
        """_sync_watched_history 外层异常"""
        service = _make_service()
        mock_client = AsyncMock()
        mock_client.get_all_watched_history = AsyncMock(
            side_effect=RuntimeError("network")
        )
        result = await service._sync_watched_history(
            "u", mock_client, MagicMock(), False
        )
        assert result.success is False


class TestConvertTraktHistoryToCustomItem:
    """_convert_trakt_history_to_custom_item 测试"""

    def test_movie_type_delegates(self):
        """电影类型委托到 _trakt_movie_history_to_custom_item"""
        service = _make_service()
        item = _make_movie_item()
        with patch.object(
            service,
            "_trakt_movie_history_to_custom_item",
            return_value=MagicMock(),
        ) as mock_movie:
            result = service._convert_trakt_history_to_custom_item("u", item)
            assert result is not None
            mock_movie.assert_called_once()

    def test_non_episode_type(self):
        """覆盖 lines 364-365: 非剧集类型"""
        service = _make_service()
        item = TraktHistoryItem(
            id=1,
            watched_at="2024-06-01T12:00:00Z",
            action="watch",
            type="show",
        )
        result = service._convert_trakt_history_to_custom_item("u", item)
        assert result is None

    def test_episode_no_show(self):
        """episode 没有 show 数据"""
        service = _make_service()
        item = TraktHistoryItem(
            id=1,
            watched_at="2024-06-01T12:00:00Z",
            action="watch",
            type="episode",
            episode={"season": 1, "number": 1},
        )
        result = service._convert_trakt_history_to_custom_item("u", item)
        assert result is None

    def test_episode_no_tmdb(self):
        """episode show 无 TMDB ID"""
        service = _make_service()
        item = TraktHistoryItem(
            id=1,
            watched_at="2024-06-01T12:00:00Z",
            action="watch",
            type="episode",
            show={"title": "S"},
            episode={"season": 1, "number": 1},
        )
        result = service._convert_trakt_history_to_custom_item("u", item)
        assert result is None

    def test_episode_no_title_in_bangumi(self):
        """TMDB 查不到标题"""
        service = _make_service()
        item = _make_episode_item()
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = None
            result = service._convert_trakt_history_to_custom_item("u", item)
            assert result is None

    def test_episode_with_first_aired(self):
        """覆盖 line 396: episode.first_aired"""
        service = _make_service()
        item = _make_episode_item(ep_first_aired="2024-05-01T00:00:00Z")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = "标题"
            result = service._convert_trakt_history_to_custom_item("u", item)
            assert result is not None
            assert "2024-05-01" in result.release_date

    def test_episode_with_show_first_aired(self):
        """show.first_aired 作为 release_date"""
        service = _make_service()
        item = _make_episode_item(show_first_aired="2024-03-01T00:00:00Z")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = "标题"
            result = service._convert_trakt_history_to_custom_item("u", item)
            assert result is not None
            assert "2024-03-01" in result.release_date

    def test_episode_release_from_watched_at(self):
        """覆盖 lines 403-06: 从 watched_at 提取日期"""
        service = _make_service()
        item = _make_episode_item(watched_at="2024-07-15T18:30:00Z")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = "标题"
            result = service._convert_trakt_history_to_custom_item("u", item)
            assert result is not None
            assert "2024-07-15" in result.release_date

    def test_episode_conversion_exception(self):
        """覆盖 lines 420-422: 转换异常"""
        service = _make_service()
        item = _make_episode_item()
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.side_effect = RuntimeError("err")
            result = service._convert_trakt_history_to_custom_item("u", item)
            assert result is None


class TestTraktMovieHistoryToCustomItem:
    """_trakt_movie_history_to_custom_item 测试"""

    def test_movie_no_data(self):
        """覆盖 lines 429-430: 电影数据缺失"""
        service = _make_service()
        item = TraktHistoryItem(
            id=1,
            watched_at="2024-06-01T12:00:00Z",
            action="watch",
            type="movie",
            movie=None,
        )
        result = service._trakt_movie_history_to_custom_item("u", item)
        assert result is None

    def test_movie_tmdb_lookup_primary(self):
        """TMDB movie/xxx 查到标题"""
        service = _make_service()
        item = _make_movie_item()
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = "电影标题"
            result = service._trakt_movie_history_to_custom_item("u", item)
            assert result is not None
            assert result.title == "电影标题"
            assert result.media_type == "movie"

    def test_movie_tmdb_fallback_str(self):
        """movie/xxx 查不到，用 str(tmdb) 查"""
        service = _make_service()
        item = _make_movie_item()
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.side_effect = [None, "Fallback标题"]
            result = service._trakt_movie_history_to_custom_item("u", item)
            assert result is not None
            assert result.title == "Fallback标题"

    def test_movie_title_from_movie_dict(self):
        """TMDB 全查不到，用 movie.title"""
        service = _make_service()
        item = _make_movie_item(title="直接标题")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = None
            result = service._trakt_movie_history_to_custom_item("u", item)
            assert result is not None
            assert result.title == "直接标题"

    def test_movie_no_title_at_all(self):
        """全无标题返回 None"""
        service = _make_service()
        item = _make_movie_item(title="")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = None
            result = service._trakt_movie_history_to_custom_item("u", item)
            assert result is None

    def test_movie_release_from_year(self):
        """覆盖 year 分支"""
        service = _make_service()
        item = _make_movie_item(released=None, year=2023)
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = "标题"
            result = service._trakt_movie_history_to_custom_item("u", item)
            assert result is not None
            assert "2023" in result.release_date

    def test_movie_release_from_watched_at(self):
        """覆盖 lines 457-458: 从 watched_at 提取"""
        service = _make_service()
        item = _make_movie_item(
            released=None, year=None, watched_at="2024-08-20T10:00:00Z"
        )
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = "标题"
            result = service._trakt_movie_history_to_custom_item("u", item)
            assert result is not None
            assert "2024-08-20" in result.release_date

    def test_movie_original_title(self):
        """original_title 被保留"""
        service = _make_service()
        item = _make_movie_item(original_title="Original")
        with patch("app.services.trakt.sync_service.bangumi_data") as mock_bd:
            mock_bd.get_title_by_tmdb_id.return_value = "标题"
            result = service._trakt_movie_history_to_custom_item("u", item)
            assert result is not None
            assert result.ori_title == "Original"


class TestRecordSyncHistory:
    """_record_sync_history 测试"""

    def test_record_success(self):
        service = _make_service()
        item = _make_episode_item()
        with patch("app.services.trakt.sync_service.database_manager") as mock_db:
            mock_db.save_trakt_sync_history.return_value = True
            service._record_sync_history("u", item, "task1")
            mock_db.save_trakt_sync_history.assert_called_once()

    def test_record_save_fail(self):
        service = _make_service()
        item = _make_episode_item()
        with patch("app.services.trakt.sync_service.database_manager") as mock_db:
            mock_db.save_trakt_sync_history.return_value = False
            service._record_sync_history("u", item, "task1")

    def test_record_exception(self):
        service = _make_service()
        item = _make_episode_item()
        with patch("app.services.trakt.sync_service.database_manager") as mock_db:
            mock_db.save_trakt_sync_history.side_effect = RuntimeError("db err")
            service._record_sync_history("u", item, "task1")


class TestStartUserSyncTask:
    """start_user_sync_task 测试"""

    @pytest.mark.asyncio
    async def test_start_returns_task_id(self):
        service = _make_service()
        with patch.object(
            service, "sync_user_trakt_data", new_callable=AsyncMock
        ) as mock_sync:
            mock_sync.return_value = TraktSyncResult(
                success=True,
                message="ok",
                synced_count=0,
                skipped_count=0,
                error_count=0,
            )
            task_id = await service.start_user_sync_task("u")
            assert task_id.startswith("trakt_sync_u_")
            # 等待内部任务执行
            await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_task_exception_recorded(self):
        """覆盖 lines 484-486: 内部任务异常"""
        service = _make_service()
        with patch.object(
            service, "sync_user_trakt_data", new_callable=AsyncMock
        ) as mock_sync:
            mock_sync.side_effect = RuntimeError("fatal")
            task_id = await service.start_user_sync_task("u")
            await asyncio.sleep(0.1)
            result = service.get_sync_result(task_id)
            assert result is not None
            assert result.success is False


class TestGetSyncResult:
    """get_sync_result / get_active_sync_tasks 测试"""

    def test_get_sync_result_none(self):
        service = _make_service()
        assert service.get_sync_result("nonexist") is None

    def test_get_active_sync_tasks_empty(self):
        service = _make_service()
        assert service.get_active_sync_tasks() == {}
