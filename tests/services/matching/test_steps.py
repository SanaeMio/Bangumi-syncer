"""阶段三 4 个 step 单测：NormalizeStep / CustomMappingStep / BangumiDataStep / APISearchStep

重点验证各 step 的 outcome 状态、ctx 字段设置与 stage_override 行为。
APISearchStep 的 post_search 改选逻辑由 test_sync_service_full.py 的集成测试覆盖，
此处只验证关键分支（bgm 缺失 / archive 命中 / low_confidence）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.models.sync import CustomItem
from app.services.matching.context import MatchContext
from app.services.matching.steps.api_search_main import APISearchStep
from app.services.matching.steps.bangumi_data import BangumiDataStep
from app.services.matching.steps.custom_mapping import CustomMappingStep
from app.services.matching.steps.normalize import NormalizeStep
from app.services.sync_service.match_trace import MatchTrace


def _build_ctx(
    title="测试番剧", ori_title=None, season=1, media_type="episode"
) -> MatchContext:
    service = MagicMock()
    return MatchContext(
        item=CustomItem(
            media_type=media_type,
            title=title,
            ori_title=ori_title,
            season=season,
            episode=1,
            release_date="2024-01-15",
            user_name="u",
            source="test",
        ),
        bgm=None,
        trace=MatchTrace(),
        service=service,
    )


class TestNormalizeStep:
    def test_writes_normalized_title_and_continues(self):
        ctx = _build_ctx(title="[发布组] 测试番剧 1080p")
        ctx.service.normalize_title.return_value = "测试番剧"

        outcome = NormalizeStep().execute(ctx)

        assert ctx.normalized_title == "测试番剧"
        ctx.service.normalize_title.assert_called_once_with("[发布组] 测试番剧 1080p")
        assert outcome.status == "hit"
        assert outcome.is_terminal is False

    def test_empty_title_passthrough(self):
        ctx = _build_ctx(title="")
        ctx.service.normalize_title.return_value = ""

        outcome = NormalizeStep().execute(ctx)

        assert ctx.normalized_title == ""
        assert outcome.status == "hit"


class TestCustomMappingStep:
    def test_hit_sets_subject_id_and_stage(self):
        ctx = _build_ctx(title="Test Anime")
        with patch(
            "app.services.mapping_service.mapping_service.find_mapping",
            return_value=("12345", "exact", "自定义映射命中：Test Anime=12345"),
        ):
            outcome = CustomMappingStep().execute(ctx)

        assert outcome.status == "hit"
        assert outcome.subject_id == "12345"
        assert outcome.is_terminal is True
        assert ctx.subject_id == "12345"
        assert ctx.match_stage == "custom_mapping"
        assert ctx.is_season_matched_id is False

    def test_miss_continues_pipeline(self):
        ctx = _build_ctx(title="未知番剧")
        with patch(
            "app.services.mapping_service.mapping_service.find_mapping",
            return_value=("", "", ""),
        ):
            outcome = CustomMappingStep().execute(ctx)

        assert outcome.status == "miss"
        assert outcome.is_terminal is False
        assert ctx.subject_id is None


class TestBangumiDataStep:
    def test_disabled_returns_skipped(self):
        ctx = _build_ctx()
        with patch("app.services.sync_service.config_manager") as mock_cfg:
            mock_cfg.get.side_effect = lambda s, k, fallback=None: (
                False if (s, k) == ("bangumi_data", "enabled") else fallback
            )
            outcome = BangumiDataStep().execute(ctx)

        assert outcome.status == "skipped"
        assert outcome.is_terminal is False
        ctx.service._get_bangumi_data.assert_not_called()

    def test_hit_sets_subject_id_and_stage(self):
        ctx = _build_ctx(season=1)
        mock_bgm_data = MagicMock()
        mock_bgm_data.find_bangumi_id.return_value = (
            "66",
            "测试番剧",
            True,
        )
        ctx.service._get_bangumi_data.return_value = mock_bgm_data

        with patch("app.services.sync_service.config_manager") as mock_cfg:
            mock_cfg.get.side_effect = lambda s, k, fallback=None: (
                True if (s, k) == ("bangumi_data", "enabled") else fallback
            )
            outcome = BangumiDataStep().execute(ctx)

        assert outcome.status == "hit"
        assert outcome.subject_id == "66"
        assert outcome.is_terminal is True
        assert ctx.subject_id == "66"
        assert ctx.match_stage == "bangumi_data"

    def test_miss_returns_candidates(self):
        ctx = _build_ctx()
        mock_bgm_data = MagicMock()
        mock_bgm_data.find_bangumi_id.return_value = None
        mock_bgm_data.find_bangumi_candidates.return_value = [
            {"id": "111", "name": "候选A", "name_cn": "候选A", "score": 0.8},
        ]
        ctx.service._get_bangumi_data.return_value = mock_bgm_data

        with patch("app.services.sync_service.config_manager") as mock_cfg:
            mock_cfg.get.side_effect = lambda s, k, fallback=None: (
                True if (s, k) == ("bangumi_data", "enabled") else fallback
            )
            outcome = BangumiDataStep().execute(ctx)

        assert outcome.status == "miss"
        assert outcome.is_terminal is False
        assert len(outcome.candidates) == 1
        assert outcome.candidates[0].subject_id == "111"

    def test_exception_returns_error(self):
        ctx = _build_ctx()
        mock_bgm_data = MagicMock()
        mock_bgm_data.find_bangumi_id.side_effect = RuntimeError("db corrupted")
        ctx.service._get_bangumi_data.return_value = mock_bgm_data

        with patch("app.services.sync_service.config_manager") as mock_cfg:
            mock_cfg.get.side_effect = lambda s, k, fallback=None: (
                True if (s, k) == ("bangumi_data", "enabled") else fallback
            )
            outcome = BangumiDataStep().execute(ctx)

        assert outcome.status == "error"
        assert outcome.is_terminal is False
        assert "db corrupted" in outcome.reason
        assert outcome.error_detail["type"] == "RuntimeError"


class TestAPISearchStep:
    def test_no_bgm_instance_returns_error(self):
        ctx = _build_ctx()
        ctx.service._get_bangumi_api_for_user.return_value = None

        outcome = APISearchStep().execute(ctx)

        assert outcome.status == "error"
        assert outcome.is_terminal is True
        assert ctx.failure_detail == "无法创建 Bangumi API 实例，无法搜索条目"

    def test_archive_hit_sets_stage_override(self):
        ctx = _build_ctx()
        bgm = MagicMock()
        bgm.bgm_search.return_value = [
            {
                "id": 12345,
                "name": "测试番剧",
                "name_cn": "测试番剧",
                "platform": "TV",
                "date": "2024-01-15",
            }
        ]
        bgm.last_hit_source = "archive"
        bgm.title_diff_ratio.return_value = 0.95
        ctx.service._get_bangumi_api_for_user.return_value = bgm
        ctx.service._sort_candidates_by_platform.side_effect = lambda data, **kw: data
        ctx.service._get_match_confidence_threshold.return_value = 0.6
        ctx.service._check_season_info_in_title.return_value = False
        ctx.service._get_explicit_season_from_title.return_value = 0
        ctx.service._pick_mainline_episode_candidate.return_value = (
            bgm.bgm_search.return_value[0]
        )

        outcome = APISearchStep().execute(ctx)

        assert outcome.status == "hit"
        assert outcome.subject_id == 12345
        assert outcome.stage_override == "archive"
        assert ctx.match_stage == "archive"
        assert ctx.match_method_detail == "exact"

    def test_low_confidence_does_not_set_subject_id(self):
        ctx = _build_ctx()
        bgm = MagicMock()
        bgm.bgm_search.return_value = [
            {
                "id": 67890,
                "name": "模糊番剧",
                "name_cn": "模糊番剧",
                "platform": "TV",
                "date": "2024-01-15",
            }
        ]
        bgm.last_hit_source = ""
        bgm.title_diff_ratio.return_value = 0.3
        ctx.service._get_bangumi_api_for_user.return_value = bgm
        ctx.service._sort_candidates_by_platform.side_effect = lambda data, **kw: data
        ctx.service._get_match_confidence_threshold.return_value = 0.6
        ctx.service._check_season_info_in_title.return_value = False
        ctx.service._get_explicit_season_from_title.return_value = 0

        outcome = APISearchStep().execute(ctx)

        assert outcome.status == "low_confidence"
        assert outcome.is_terminal is True
        assert ctx.subject_id is None
        assert "below threshold" in ctx.failure_detail
