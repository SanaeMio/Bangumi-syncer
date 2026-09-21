"""ArchiveShortcutStep 类型冲突补召回测试

覆盖：请求「动画剧集」但 archive 精确匹配只命中三次元条目时，
用纯动画类型补查一次并把同名动画候选并入（凡人修仙传场景）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.models.sync import CustomItem
from app.services.matching.context import MatchContext
from app.services.matching.steps.archive_shortcut import ArchiveShortcutStep
from app.services.sync_service.match_trace import MatchTrace
from app.utils.bangumi_api._archive_shortcut import ShortcutResult

REAL_ROW = {"id": 434076, "name": "凡人修仙传", "name_cn": "凡人修仙传", "type": 6}
ANIME_ROW = {
    "id": 348240,
    "name": "凡人修仙传 年番",
    "name_cn": "凡人修仙传 年番",
    "type": 2,
}
ANIME_ROW2 = {
    "id": 406306,
    "name": "凡人修仙传 新年番",
    "name_cn": "凡人修仙传 新年番",
    "type": 2,
}


def _shortcut(data, method="exact") -> ShortcutResult:
    return ShortcutResult(True, data, "archive_hit", method)


def _build_ctx(media_type="episode", title="凡人修仙传") -> MatchContext:
    return MatchContext(
        item=CustomItem(
            media_type=media_type,
            title=title,
            ori_title=None,
            season=1,
            episode=81,
            release_date="",
            user_name="u",
            source="test",
        ),
        bgm=None,
        trace=MatchTrace(),
        service=MagicMock(),
    )


def _bgm_with(responses: list) -> MagicMock:
    """responses：try_search 的逐次返回值（可含 Exception）"""
    bgm = MagicMock()
    bgm._archive.enabled = True
    bgm._archive.try_search.side_effect = responses
    bgm.title_diff_ratio.return_value = 0.9
    return bgm


def _run(ctx: MatchContext, bgm: MagicMock, enable_real=True):
    ctx.service._get_bangumi_api_for_user.return_value = bgm
    with patch("app.services.sync_service.config_manager") as mock_cfg:
        mock_cfg.get.side_effect = lambda s, k, fallback=None: (
            enable_real if (s, k) == ("sync", "enable_real_action") else fallback
        )
        return ArchiveShortcutStep().execute(ctx)


class TestAnimeTypeConflictRecall:
    def test_recalls_anime_when_hit_is_all_real(self):
        """命中全为三次元 → 用 types=[2] 补查，动画候选排到前面"""
        ctx = _build_ctx()
        bgm = _bgm_with([_shortcut([REAL_ROW]), _shortcut([ANIME_ROW, ANIME_ROW2])])

        outcome = _run(ctx, bgm)

        assert bgm._archive.try_search.call_count == 2
        assert bgm._archive.try_search.call_args.kwargs["subject_types"] == [2]
        ids = [c["id"] for c in ctx.bgm_data]
        assert ids == [348240, 406306, 434076]
        assert ctx.bgm_data[0]["type"] == 2
        assert outcome.status == "hit"
        assert outcome.subject_id == "348240"

    def test_no_recall_when_anime_already_present(self):
        """候选里已有动画 → 不补查"""
        ctx = _build_ctx()
        bgm = _bgm_with([_shortcut([ANIME_ROW, REAL_ROW])])

        _run(ctx, bgm)

        assert bgm._archive.try_search.call_count == 1

    def test_no_recall_when_real_action_request(self):
        """请求三次元 → subject_types=[6]，不补查"""
        ctx = _build_ctx(media_type="real_action")
        bgm = _bgm_with([_shortcut([REAL_ROW])])

        _run(ctx, bgm)

        assert bgm._archive.try_search.call_count == 1

    def test_no_recall_when_real_action_disabled(self):
        """enable_real_action=false → subject_types=[2]，不补查"""
        ctx = _build_ctx()
        bgm = _bgm_with([_shortcut([ANIME_ROW])])

        _run(ctx, bgm, enable_real=False)

        assert bgm._archive.try_search.call_count == 1

    def test_keeps_original_when_recall_empty(self):
        """补查无结果 → 沿用原候选"""
        ctx = _build_ctx()
        bgm = _bgm_with(
            [_shortcut([REAL_ROW]), ShortcutResult(False, None, "archive_miss")]
        )

        _run(ctx, bgm)

        assert [c["id"] for c in ctx.bgm_data] == [434076]

    def test_keeps_original_when_recall_raises(self):
        """补查抛异常 → 不阻断，沿用原候选"""
        ctx = _build_ctx()
        bgm = _bgm_with([_shortcut([REAL_ROW]), RuntimeError("boom")])

        _run(ctx, bgm)

        assert [c["id"] for c in ctx.bgm_data] == [434076]

    def test_recall_dedupes_existing_ids(self):
        """补查返回的 id 若已存在 → 不重复追加"""
        ctx = _build_ctx()
        bgm = _bgm_with([_shortcut([REAL_ROW]), _shortcut([ANIME_ROW, REAL_ROW])])

        _run(ctx, bgm)

        assert [c["id"] for c in ctx.bgm_data] == [348240, 434076]

    def test_no_recall_for_movie_request(self):
        """电影请求不做补召回（只针对 episode）"""
        ctx = _build_ctx(media_type="movie")
        bgm = _bgm_with([_shortcut([REAL_ROW])])

        _run(ctx, bgm)

        assert bgm._archive.try_search.call_count == 1
