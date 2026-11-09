"""集级 TMDB 起点：条目归属与条目内集号换算

bangumi-data 用 ``tv/{id}/season/{s}/episode/{start}`` 表达「TMDB 未把作品
拆成多季」时，某条目从合并季的第 start 集开始。若只按 site id 字面建 key，
``get_begin_by_tmdb_id("tv/278043", season=1)`` 只能取到第一季 begin，
S01E13 便会带着第一季标题与第一季日期进入匹配，落到第二季末集。

本文件固定集边界索引的行为：从合并季集号推算出目标条目与条目内集号，
且不影响季级 key、基 key、无集级 key 等既有路径。
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from app.models.sync import CustomItem
from app.services.matching.context import MatchContext
from app.services.matching.pipeline import MatchPipeline
from app.services.matching.steps.bangumi_data import BangumiDataStep
from app.services.matching.steps.custom_mapping import CustomMappingStep
from app.services.sync_service.context import ExecutionContext
from app.services.sync_service.match_trace import MatchTrace
from app.services.sync_service.steps.episode_resolve import EpisodeResolveStep
from app.services.trakt.models import TraktHistoryItem
from app.services.trakt.sync_service import TraktSyncService
from app.utils.bangumi_api import BangumiApi
from app.utils.bangumi_data import BangumiData
from app.utils.bangumi_data.index import IndexMixin, TmdbEpisodeMatch

S1_ID = "525565"
S2_ID = "638497"
BASE = "tv/278043"
S1_BEGIN = "2026-01-11T08:00:00.000Z"
S2_BEGIN = "2026-07-05T08:00:00.000Z"


def _item(title: str, tmdb_id: str, bangumi_id: str | None, begin: str) -> dict:
    """构造一条 bangumi-data 条目（可缺 bangumi id 以覆盖脏数据）。"""
    sites = [{"site": "tmdb", "id": tmdb_id}]
    if bangumi_id:
        sites.append({"site": "bangumi", "id": bangumi_id})
    return {
        "title": title,
        "titleTranslate": {"zh-Hans": [title]},
        "begin": begin,
        "sites": sites,
    }


def _case_items() -> list[dict]:
    """本案例真实数据形态：基 key + 集级起点（TMDB 不分季）。"""
    return [
        _item("正反対な君と僕", BASE, S1_ID, S1_BEGIN),
        _item("正反対な君と僕(第2期)", f"{BASE}/season/1/episode/13", S2_ID, S2_BEGIN),
    ]


def _index(items: list[dict]) -> IndexMixin:
    """隔离的 IndexMixin 实例，避免污染全局单例与其它测试的缓存配置。"""
    obj = IndexMixin()
    obj._cache_tmdb_mapping = {}
    obj._cache_tmdb_begin = {}
    obj._cache_tmdb_episode_ranges = {}
    obj._data_cache = items
    obj._build_lock = threading.Lock()
    obj._parse_data = lambda: items
    obj._build_tmdb_mapping()
    return obj


def _full_data(items: list[dict]) -> BangumiData:
    """构建只含给定条目的完整 BangumiData 实例。

    与 test_issue269_season_episode.py 一致：不用全局单例，避免其它测试
    就地改写 local_cache_path / use_cache 带来的顺序污染。
    """
    with (
        patch.object(BangumiData, "_check_and_download_cache_on_startup"),
        patch.object(BangumiData, "_preload_data_to_memory"),
        patch.object(BangumiData, "_build_tmdb_mapping"),
    ):
        data = BangumiData()
    data._data_cache = list(items)
    data.local_cache_path = ""
    data.use_cache = False
    data._build_tmdb_mapping()
    with patch.object(data, "_ensure_fresh_data"):
        data._build_title_index()
    return data


def _trakt_item(number: int, season: int = 1, tmdb: int | None = 278043):
    """构造一条 Trakt 观看历史记录（TMDB 不分季，两期共用同一 show id）。"""
    show = {
        "title": "You and I Are Polar Opposites",
        "year": 2026,
        "ids": {"trakt": 264998},
    }
    if tmdb is not None:
        show["ids"]["tmdb"] = tmdb
    episode = {
        "season": season,
        "number": number,
        "title": "Christmas Eve",
        "first_aired": None,
    }
    return TraktHistoryItem(
        id=1,
        watched_at="2026-11-09T19:12:00.000Z",
        action="watch",
        type="episode",
        show=show,
        episode=episode,
    )


def _match_ctx(number: int, tmdb_id: str | None = BASE) -> MatchContext:
    """构造本案例的匹配上下文（集级命中用）。"""
    service = MagicMock()
    item = CustomItem(
        media_type="episode",
        title="正反対な君と僕",
        season=1,
        episode=number,
        release_date="2026-07-05",
        user_name="u",
        source="test",
        tmdb_id=tmdb_id,
    )
    return MatchContext(item=item, bgm=None, trace=MatchTrace(), service=service)


def _run_bangumi_data_step(ctx: MatchContext):
    """以启用状态执行 BangumiDataStep。"""
    with patch("app.services.sync_service.config_manager") as cfg:
        cfg.get.side_effect = lambda s, k, fallback=None: (
            True if (s, k) == ("bangumi_data", "enabled") else fallback
        )
        return BangumiDataStep().execute(ctx)


class TestResolveTmdbEpisode:
    """区间换算：取不大于请求集号的最大起点。"""

    def test_episode_before_first_start_returns_none(self):
        """E1-E12 落在首个起点之前 → None，基 key 条目沿用既有匹配链。"""
        idx = _index(_case_items())
        for number in (1, 6, 12):
            assert idx.resolve_tmdb_episode(BASE, 1, number) is None

    def test_episode_at_start_maps_to_target_first_episode(self):
        """E13 恰在起点 → 目标条目 638497，条目内第 1 集。"""
        idx = _index(_case_items())
        match = idx.resolve_tmdb_episode(BASE, 1, 13)
        assert match == TmdbEpisodeMatch(
            subject_id=S2_ID,
            title="正反対な君と僕(第2期)",
            begin=S2_BEGIN,
            start_episode=13,
            target_episode=1,
        )

    def test_episodes_after_start_keep_offset(self):
        """E14-E25 依次换算成条目内第 2-13 集。"""
        idx = _index(_case_items())
        for number, expected in ((14, 2), (20, 8), (25, 13)):
            match = idx.resolve_tmdb_episode(BASE, 1, number)
            assert match is not None
            assert match.subject_id == S2_ID
            assert match.target_episode == expected

    def test_multiple_starts_pick_largest_not_exceeding_episode(self):
        """多起点时按区间取最大起点，不取第一个也不取最后一个。"""
        items = [
            _item("本篇", BASE, S1_ID, S1_BEGIN),
            _item("特别篇A", f"{BASE}/season/0/episode/4", "1001", "1998-01-01"),
            _item("特别篇B", f"{BASE}/season/0/episode/7", "1002", "1999-01-01"),
            _item("特别篇C", f"{BASE}/season/0/episode/13", "1003", "2000-01-01"),
        ]
        idx = _index(items)

        def resolve(episode):
            match = idx.resolve_tmdb_episode(BASE, 0, episode)
            return None if match is None else (match.subject_id, match.target_episode)

        assert resolve(3) is None
        assert resolve(4) == ("1001", 1)
        assert resolve(6) == ("1001", 3)
        assert resolve(7) == ("1002", 1)
        assert resolve(12) == ("1002", 6)
        assert resolve(13) == ("1003", 1)

    def test_season_is_part_of_the_key(self):
        """起点只作用于声明的季；请求别的季不命中。"""
        idx = _index(_case_items())
        assert idx.resolve_tmdb_episode(BASE, 2, 13) is None
        assert idx.resolve_tmdb_episode(BASE, 0, 13) is None

    def test_unknown_base_and_invalid_episode_are_safe(self):
        """无集级 key 的 base、非法集号均返回 None。"""
        idx = _index(_case_items())
        assert idx.resolve_tmdb_episode("tv/1668", 6, 6) is None
        assert idx.resolve_tmdb_episode(BASE, 1, 0) is None
        assert idx.resolve_tmdb_episode(BASE, 1, -3) is None
        assert idx.resolve_tmdb_episode(BASE, 1, None) is None
        assert idx.resolve_tmdb_episode(BASE, 1, "13") is None
        assert idx.resolve_tmdb_episode(BASE, None, 13) is None
        assert idx.resolve_tmdb_episode(BASE, -1, 13) is None
        assert idx.resolve_tmdb_episode(None, 1, 13) is None
        assert idx.resolve_tmdb_episode("", 1, 13) is None

    def test_episode_one_start_is_ignored(self):
        """起点 1 不表达分段，交由基 key 与既有链处理。"""
        items = [
            _item("本篇", BASE, S1_ID, S1_BEGIN),
            _item("异条目", f"{BASE}/season/1/episode/1", "9999", S1_BEGIN),
        ]
        idx = _index(items)
        assert idx.resolve_tmdb_episode(BASE, 1, 1) is None

    def test_range_without_bangumi_id_is_skipped(self):
        """缺 bangumi id 的集级条目无法安全定位 subject，不参与集边界。"""
        items = [
            _item("本篇", BASE, S1_ID, S1_BEGIN),
            _item("缺 id", f"{BASE}/season/1/episode/13", None, S2_BEGIN),
        ]
        idx = _index(items)
        assert idx.resolve_tmdb_episode(BASE, 1, 13) is None

    def test_non_string_tmdb_id_is_skipped(self):
        """site id 非字符串时不参与集边界，避免异常类型引发崩溃。"""
        items = [
            _item("本篇", BASE, S1_ID, S1_BEGIN),
            _item("异常 id", f"{BASE}/season/1/episode/13", "9999", S2_BEGIN),
        ]
        items[1]["sites"][0]["id"] = 12345
        idx = _index(items)
        assert idx.resolve_tmdb_episode(BASE, 1, 13) is None

    def test_season_level_query_resolves_by_season(self):
        """季级 key（形如 tv/94664/season/3）按季解析，与集级索引互不影响。"""
        items = [
            _item("無職転生", "tv/94664", "100", "2021-01-10"),
            _item("無職転生Ⅲ", "tv/94664/season/3", "300", "2026-07-05"),
        ]
        idx = _index(items)
        assert idx.get_title_by_tmdb_id("tv/94664", season=3) == "無職転生Ⅲ"
        assert idx.get_begin_by_tmdb_id("tv/94664", season=3) == "2026-07-05"
        assert idx.get_begin_by_tmdb_id("tv/94664", season=1) == "2021-01-10"

    def test_rebuild_is_idempotent(self):
        """重复构建不叠加集边界（幂等，跟随 #228 对称重建语义）。"""
        idx = _index(_case_items())
        first = list(idx._cache_tmdb_episode_ranges[(BASE, 1)])
        idx._build_tmdb_mapping()
        assert idx._cache_tmdb_episode_ranges[(BASE, 1)] == first
        assert len(idx._cache_tmdb_episode_ranges[(BASE, 1)]) == 1

    def test_lazy_rebuild_restores_ranges(self):
        """配置刷新清空映射后，首次查询随对称重建恢复集边界。"""
        idx = _index(_case_items())
        idx._cache_tmdb_mapping.clear()
        idx._cache_tmdb_begin.clear()
        idx._cache_tmdb_episode_ranges.clear()
        match = idx.resolve_tmdb_episode(BASE, 1, 13)
        assert match is not None
        assert match.subject_id == S2_ID
        assert match.target_episode == 1


class TestTraktEpisodeRangeConversion:
    """转换层：TMDB 引用与集级 begin（零请求）。"""

    def test_s01e13_carries_tmdb_id_and_second_season_date(self):
        idx = _index(_case_items())
        with patch("app.services.trakt.sync_service.bangumi_data", idx):
            item = TraktSyncService()._convert_trakt_history_to_custom_item(
                "u", _trakt_item(13)
            )
        assert item.tmdb_id == BASE
        assert item.release_date == "2026-07-05"
        assert item.season == 1 and item.episode == 13

    def test_s01e01_keeps_first_season_date(self):
        """E1 落在起点之前 → 沿用 get_begin_by_tmdb_id 的第一季日期。"""
        idx = _index(_case_items())
        with patch("app.services.trakt.sync_service.bangumi_data", idx):
            item = TraktSyncService()._convert_trakt_history_to_custom_item(
                "u", _trakt_item(1)
            )
        assert item.release_date == "2026-01-11"

    def test_first_aired_outranks_episode_begin(self):
        """回退链前两级（真实首播日期）优先级不变。"""
        idx = _index(_case_items())
        raw = _trakt_item(13)
        raw.episode["first_aired"] = "2026-08-01T00:00:00.000Z"
        with patch("app.services.trakt.sync_service.bangumi_data", idx):
            item = TraktSyncService()._convert_trakt_history_to_custom_item("u", raw)
        assert item.release_date == "2026-08-01"

    def test_show_first_aired_outranks_episode_begin(self):
        """show.first_aired 存在时既有第二级仍优先，集级 begin 不越级。

        /sync/history 不带 extended，真实响应里这两级恒为空；此用例固化
        既有回退链顺序，避免把本分支的日期来源误当成最高优先级。
        """
        idx = _index(_case_items())
        raw = _trakt_item(13)
        raw.show["first_aired"] = "2026-01-11T08:00:00.000Z"
        with patch("app.services.trakt.sync_service.bangumi_data", idx):
            item = TraktSyncService()._convert_trakt_history_to_custom_item("u", raw)
        assert item.release_date == "2026-01-11"
        assert idx.resolve_tmdb_episode(BASE, 1, 13).target_episode == 1

    def test_no_tmdb_id_falls_back_to_year(self):
        """无 TMDB id 时不进入集边界换算，沿用 show.year 兜底。"""
        idx = _index([])
        with patch("app.services.trakt.sync_service.bangumi_data", idx):
            item = TraktSyncService()._convert_trakt_history_to_custom_item(
                "u", _trakt_item(13, tmdb=None)
            )
        assert item.tmdb_id is None
        assert item.release_date == "2026-01-01"

    def test_mocked_bangumi_data_is_not_mistaken_for_a_match(self):
        """bangumi_data 为 mock 对象时返回的占位值不得被当成集级命中。"""
        mock_bd = MagicMock()
        mock_bd.get_title_by_tmdb_id.return_value = "标题"
        mock_bd.get_begin_by_tmdb_id.return_value = ""
        with patch("app.services.trakt.sync_service.bangumi_data", mock_bd):
            item = TraktSyncService()._convert_trakt_history_to_custom_item(
                "u", _trakt_item(13)
            )
        assert item.release_date == "2026-01-01"


class TestBangumiDataStepEpisodeRange:
    """匹配层：集级确定性分支与既有链的边界。"""

    def test_s01e13_hits_second_season_first_episode(self):
        idx = _index(_case_items())
        ctx = _match_ctx(13)
        ctx.service._get_bangumi_data.return_value = idx
        outcome = _run_bangumi_data_step(ctx)
        assert outcome.status == "hit"
        assert outcome.subject_id == S2_ID
        assert outcome.is_terminal is True
        assert ctx.mapping_target_episode == 1
        assert ctx.is_season_matched_id is True
        assert ctx.mapping_is_explicit is False
        assert outcome.outputs["match_method"] == "tmdb_episode_range"
        assert outcome.outputs["target_episode"] == "1"
        assert outcome.outputs["tmdb_start_episode"] == 13
        assert outcome.outputs["matched_title"] == "正反対な君と僕(第2期)"
        assert outcome.inputs["tmdb_id"] == BASE
        # C4：命中必须产出候选并标注来源
        assert len(outcome.candidates) == 1
        assert outcome.candidates[0].subject_id == S2_ID
        assert outcome.candidates[0].source == "bangumi_data"
        assert outcome.candidates[0].score == 1.0

    def test_no_tmdb_id_skips_episode_range(self):
        """未携带 TMDB id 的请求不进入集级分支，完全沿用既有链。"""
        idx = _index(_case_items())
        ctx = _match_ctx(13, tmdb_id=None)
        ctx.service._get_bangumi_data.return_value = idx
        outcome = _run_bangumi_data_step(ctx)
        assert ctx.mapping_target_episode is None
        assert outcome.outputs.get("match_method") != "tmdb_episode_range"

    def test_episode_before_start_skips_episode_range(self):
        """E1 落在起点之前 → 集级分支不命中，仍由标题匹配定位基 key 条目。"""
        idx = _index(_case_items())
        ctx = _match_ctx(1)
        ctx.service._get_bangumi_data.return_value = idx
        outcome = _run_bangumi_data_step(ctx)
        assert ctx.mapping_target_episode is None
        assert outcome.outputs.get("match_method") != "tmdb_episode_range"

    def test_magic_mock_bangumi_data_is_not_mistaken_for_a_match(self):
        """bgm_data 为 mock 对象时不得误判为集级命中。"""
        ctx = _match_ctx(13)
        outcome = _run_bangumi_data_step(ctx)
        assert ctx.mapping_target_episode is None
        assert outcome.outputs.get("match_method") != "tmdb_episode_range"

    def test_index_error_falls_back_to_existing_chain(self):
        """索引查询抛异常时回落既有匹配链，不中断同步。"""
        idx = _index(_case_items())

        def _raise(*_args, **_kwargs):
            raise RuntimeError("索引不可用")

        idx.resolve_tmdb_episode = _raise
        ctx = _match_ctx(13)
        ctx.service._get_bangumi_data.return_value = idx
        outcome = _run_bangumi_data_step(ctx)
        assert outcome.outputs.get("match_method") != "tmdb_episode_range"
        assert ctx.mapping_target_episode is None

    def test_disabled_bangumi_data_skips_episode_range(self):
        cfg = MagicMock()
        cfg.get.return_value = False
        ctx = _match_ctx(13)
        with patch("app.services.sync_service.config_manager", cfg):
            outcome = BangumiDataStep().execute(ctx)
        assert outcome.status == "skipped"
        assert ctx.mapping_target_episode is None

    def test_episode_range_does_not_consult_date_guard(self):
        """集级起点是确定性映射，与自定义映射一致，不受请求日期差影响。

        日期硬护栏只针对标题近似匹配可能采信同名异条目；这里的证据是
        TMDB 基 id + 季 + 集号，且不采信时会回落到既有匹配链。
        """
        idx = _index(_case_items())
        for release_date in ("2026-07-05", "1990-01-01", ""):
            ctx = _match_ctx(13)
            ctx.item.release_date = release_date
            ctx.service._get_bangumi_data.return_value = idx
            outcome = _run_bangumi_data_step(ctx)
            assert outcome.status == "hit"
            assert outcome.subject_id == S2_ID
            assert ctx.mapping_target_episode == 1


class TestExecutionTargetEpisode:
    """执行层：集级换算集号经既有通道送达集数解析，请求季集号不被改写。"""

    @staticmethod
    def _exec_ctx(match_ctx: MatchContext) -> ExecutionContext:
        """按编排器的注入方式，把匹配阶段产物搬进执行上下文。"""
        return ExecutionContext(
            item=match_ctx.item,
            bgm=None,
            trace=MatchTrace(),
            service=match_ctx.service,
            actual_source="trakt",
            subject_id=match_ctx.subject_id,
            is_season_matched_id=match_ctx.is_season_matched_id,
            mapping_is_explicit=match_ctx.mapping_is_explicit,
            mapping_target_episode=match_ctx.mapping_target_episode,
        )

    def test_episode_range_target_reaches_episode_resolve(self):
        """集级命中后，集数解析收到的 target_episode 是换算后的条目内集号。

        mapping_is_explicit 保持 False，跨季链兜底（allow_chain_fallback）
        随之保持开启。
        """
        idx = _index(_case_items())
        match_ctx = _match_ctx(13)
        match_ctx.service._get_bangumi_data.return_value = idx
        _run_bangumi_data_step(match_ctx)
        match_ctx.service._resolve_season_episode.return_value = (S2_ID, 1704756)

        exec_ctx = self._exec_ctx(match_ctx)
        outcome = EpisodeResolveStep().execute(exec_ctx)

        kwargs = match_ctx.service._resolve_season_episode.call_args.kwargs
        assert kwargs["target_episode"] == 1
        assert kwargs["mapping_is_explicit"] is False
        assert outcome.status == "hit"
        assert outcome.subject_id == S2_ID
        assert outcome.outputs["episode_id"] == "1704756"

    def test_request_season_and_episode_survive_the_pipeline(self):
        """端到端跑完后，请求携带的季集号仍保持原值（展示与去重口径）。"""
        idx = _index(_case_items())
        match_ctx = _match_ctx(13)
        match_ctx.service._get_bangumi_data.return_value = idx
        _run_bangumi_data_step(match_ctx)

        assert match_ctx.item.season == 1
        assert match_ctx.item.episode == 13

    def test_mapping_hit_terminates_before_bangumi_data_step(self):
        """两类来源不会同时写入：CustomMapping 命中即终止，BangumiData 不执行。"""
        idx = _index(_case_items())
        ctx = _match_ctx(13)
        ctx.service._get_bangumi_data.return_value = idx
        with (
            patch(
                "app.services.mapping_service.mapping_service.find_episode_mapping",
                return_value=("999999", 5, "显式分段"),
            ),
            patch(
                "app.services.mapping_service.mapping_service.find_mapping",
                return_value=("999999", "segment", "显式映射", True),
            ),
        ):
            pipeline = MatchPipeline([CustomMappingStep(), BangumiDataStep()])
            result = pipeline.run(ctx)
        assert result.subject_id == "999999"
        assert result.mapping_is_explicit is True
        assert ctx.mapping_target_episode == 5
        assert ctx.match_stage == "custom_mapping"


class TestRegularRequestScenarios:
    """非目标场景的常规请求：集级分支不介入，结果与标题匹配链一致。

    这些用例覆盖其它驱动、剧场版、落在首个起点之前的集号、季不匹配与
    无集级 key 的情形，均应为 miss 且不写入换算集号。
    """

    _data = None

    @classmethod
    def _bangumi_data(cls):
        if cls._data is None:
            cls._data = _full_data(_case_items())
        return cls._data

    @classmethod
    def _run(cls, item):
        svc = MagicMock()
        svc._get_bangumi_data.return_value = cls._bangumi_data()
        ctx = MatchContext(item=item, bgm=None, trace=MatchTrace(), service=svc)
        outcome = _run_bangumi_data_step(ctx)
        return outcome, ctx

    @staticmethod
    def _custom_item(**kw):
        fields = {
            "media_type": "episode",
            "title": "某动画",
            "season": 1,
            "episode": 1,
            "release_date": "2024-01-15",
            "user_name": "u",
            "source": "emby",
        }
        fields.update(kw)
        return CustomItem(**fields)

    def test_other_drivers_without_tmdb_id_keep_miss(self):
        """Emby / Plex / fongmi / Jellyfin 的普通请求不进入集级分支。"""
        for source, season, episode in (
            ("emby", 1, 1),
            ("plex", 2, 5),
            ("fongmi", 1, 12),
            ("jellyfin", 3, 7),
        ):
            outcome, ctx = self._run(
                self._custom_item(source=source, season=season, episode=episode)
            )
            assert outcome.status == "miss"
            assert outcome.subject_id is None
            assert ctx.mapping_target_episode is None
            assert outcome.outputs.get("match_method") != "tmdb_episode_range"

    def test_movie_request_keeps_miss(self):
        """剧场版请求不进入集级分支。"""
        outcome, ctx = self._run(self._custom_item(media_type="movie"))
        assert outcome.status == "miss"
        assert ctx.mapping_target_episode is None

    def test_movie_with_large_episode_does_not_enter_episode_range(self):
        """剧场版即使集号落在集边界内也不映射（无集号概念）。"""
        ctx = _match_ctx(13)
        ctx.item.media_type = "movie"
        ctx.service._get_bangumi_data.return_value = self._bangumi_data()
        outcome = _run_bangumi_data_step(ctx)
        assert outcome.outputs.get("match_method") != "tmdb_episode_range"
        assert ctx.mapping_target_episode is None

    def test_ova_request_can_use_episode_range(self):
        """OVA / OAD 等剧集形态仍可享受集边界换算。"""
        for media_type in ("ova", "oad", "real_action"):
            ctx = _match_ctx(13)
            ctx.item.media_type = media_type
            ctx.service._get_bangumi_data.return_value = self._bangumi_data()
            outcome = _run_bangumi_data_step(ctx)
            assert outcome.status == "hit"
            assert outcome.subject_id == S2_ID
            assert ctx.mapping_target_episode == 1

    def test_episode_before_start_or_other_season_keeps_miss(self):
        """携带 TMDB id 但落在起点前 / 季不匹配 / 无集级 key 均不命中。"""
        for tmdb_id, season, episode in (
            (BASE, 1, 5),
            (BASE, 2, 13),
            (BASE, 0, 13),
            ("tv/1668", 6, 6),
        ):
            outcome, ctx = self._run(
                self._custom_item(tmdb_id=tmdb_id, season=season, episode=episode)
            )
            assert outcome.status == "miss"
            assert ctx.mapping_target_episode is None
            assert outcome.outputs.get("match_method") != "tmdb_episode_range"

    def test_missing_begin_yields_empty_air_date(self):
        """集级条目缺 begin 时命中照常，候选的 air_date 为空串。"""
        data = _full_data(
            [
                _item("本篇", BASE, S1_ID, S1_BEGIN),
                _item("第2期", f"{BASE}/season/1/episode/13", S2_ID, ""),
            ]
        )
        svc = MagicMock()
        svc._get_bangumi_data.return_value = data
        ctx = MatchContext(
            item=self._custom_item(episode=13, tmdb_id=BASE),
            bgm=None,
            trace=MatchTrace(),
            service=svc,
        )
        outcome = _run_bangumi_data_step(ctx)
        assert outcome.status == "hit"
        assert outcome.candidates[0].air_date == ""

    def test_blank_tmdb_id_is_normalized_to_none(self):
        """空串 TMDB id 按 CustomItem 的既有归一化规则变成 None。"""
        assert self._custom_item(tmdb_id="").tmdb_id is None
        assert self._custom_item(tmdb_id="  ").tmdb_id is None


class TestEpisodeResolutionOutcome:
    """换算集号进入章节定位后的结果（目标集号取 1 与取 13 的差异）。"""

    @staticmethod
    def _rows() -> list[dict]:
        """638497 的真实章节形态：sort 13..25，条目内 ep 1..13。"""
        return [
            {"sort": s, "id": 1700000 + s, "ep": s - 12, "type": 0, "airdate": ""}
            for s in range(13, 26)
        ]

    def test_converted_target_hits_second_season_first_episode(self):
        """换算后的目标集号 1 → sort=13（第二季第一集）。"""
        api = BangumiApi(username="u", access_token="t")
        rows = api._match_target_ep_rows(self._rows(), 1)
        assert len(rows) == 1
        assert rows[0]["sort"] == 13
        assert rows[0]["ep"] == 1

    def test_unconverted_target_hits_last_episode(self):
        """未换算的情况：目标集号 13 → sort=25（第二季末集），即本案例的错标位置。"""
        api = BangumiApi(username="u", access_token="t")
        rows = api._match_target_ep_rows(self._rows(), 13)
        assert len(rows) == 1
        assert rows[0]["sort"] == 25
        assert rows[0]["ep"] == 13

    def test_later_segment_with_offset_sort_still_locates_first_episode(self):
        """后续条目的章节 sort 与集边界起点不成比例时，仍定位到条目内首集。

        真实 archive 形态（百姓贵族）：第二季集边界起点 15、章节 sort 15..26；
        第三季起点 25、章节 sort 29..40。目标集号按条目内相对集号解释，
        因此起点 25 的第 1 集应落在 sort=29，而不是 25。
        """
        api = BangumiApi(username="u", access_token="t")
        rows = [
            {"sort": s, "id": 2000000 + s, "ep": 0, "type": 0, "airdate": ""}
            for s in range(29, 41)
        ]
        assert api._first_normal_sort(rows) == 29
        hits = api._match_target_ep_rows(rows, 1)
        assert len(hits) == 1
        assert hits[0]["sort"] == 29
