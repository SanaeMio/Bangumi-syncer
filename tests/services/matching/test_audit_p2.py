"""P2 轻微问题验证测试

验证 6 个 P2 轻微问题：
- P2-1: subject_id str/int 类型混用
- P2-2: _match_target_ep_rows 返回类型注解错误
- P2-3: _post_search_reselect 原地修改 bgm_data[0] 导致 candidates[0] 与 final_subject_id 不一致（已修复）
- P2-4: DateExactSearchStep movie 分支覆写 ctx.end_date_str 的日志误导
- P2-5: archive 续集链/前传链空链处理不对称
- P2-6: _pick_mainline_episode_candidate 空输入返回 {}

已修复问题用普通测试（断言修复后行为且通过）。
待修复问题中，断言"期望行为"但当前代码不满足的用 @pytest.mark.xfail 标记
（测试失败，不破坏 CI）。确认"当前行为存在"的用普通测试（测试通过，证明行为属实）。
"""

from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.models.sync import CustomItem
from app.services.matching.context import MatchContext
from app.services.matching.steps.api_search import DateExactSearchStep
from app.services.matching.steps.api_search_main import APISearchStep
from app.services.sync_service.match_trace import MatchTrace
from app.utils.bangumi_api import BangumiApi
from app.utils.bangumi_api._archive_shortcut import ShortcutResult

# ----------------------------------------------------------------------
# 辅助函数（参考 test_audit_p1.py 的 mock 模式）
# ----------------------------------------------------------------------


def _build_ctx(
    title: str = "测试番剧",
    ori_title: str | None = None,
    season: int = 1,
    media_type: str = "episode",
    release_date: str = "2024-01-15",
) -> MatchContext:
    """构建测试用 MatchContext"""
    service = MagicMock()
    return MatchContext(
        item=CustomItem(
            media_type=media_type,
            title=title,
            ori_title=ori_title,
            season=season,
            episode=1,
            release_date=release_date,
            user_name="u",
            source="test",
        ),
        bgm=None,
        trace=MatchTrace(),
        service=service,
    )


def _make_bgm_mock(
    candidates: list[dict] | None = None,
    last_hit_source: str = "",
    last_match_method: str = "",
    title_diff_ratio: float = 0.95,
) -> MagicMock:
    """构建 bgm MagicMock"""
    bgm = MagicMock()
    bgm.bgm_search.return_value = candidates if candidates is not None else []
    bgm.last_hit_source = last_hit_source
    bgm.last_match_method = last_match_method
    bgm.title_diff_ratio.return_value = title_diff_ratio
    return bgm


def _configure_service_mock(
    service: MagicMock,
    bgm: MagicMock,
    threshold: float = 0.6,
    pick_mainline_return: dict | None = None,
) -> None:
    """配置 service MagicMock 的匹配辅助方法"""
    service._get_bangumi_api_for_user.return_value = bgm
    service._sort_candidates_by_platform.side_effect = lambda data, **kw: data
    service._get_match_confidence_threshold.return_value = threshold
    service._check_season_info_in_title.return_value = False
    service._get_explicit_season_from_title.return_value = 0
    if pick_mainline_return is not None:
        service._pick_mainline_episode_candidate.return_value = pick_mainline_return
    else:
        first = bgm.bgm_search.return_value[0] if bgm.bgm_search.return_value else {}
        service._pick_mainline_episode_candidate.return_value = first


# ======================================================================
# P2-1: subject_id str/int 类型混用
# ======================================================================


class TestVerifyP01SubjectIdTypeConsistency:
    """P2-1: ctx.subject_id 注解为 str | None，但 APISearchStep 存入 int

    疑似问题：``MatchContext.subject_id`` 注解为 ``str | None``（context.py line 39），
    但 ``APISearchStep.execute`` 在 line 245 执行 ``ctx.subject_id = bgm_data[0]["id"]``，
    而 Bangumi API / archive 返回的 ``id`` 字段为 int。其他 step
    （ArchiveShortcutStep / VariantFallbackSearchStep / SearchFinalizeStep）
    用 ``str(...)`` 转换，唯独 APISearchStep 命中分支不转换。

    场景：bgm_search 返回 ``{"id": 12345}``（int），运行 APISearchStep.execute，
    期望 ``ctx.subject_id`` 为 str，实际为 int。
    """

    @pytest.mark.xfail(
        reason=(
            "P2-1 Confirmed: APISearchStep.execute line 245 "
            "ctx.subject_id = bgm_data[0]['id'] 存入 int（API/archive 返回 int），"
            "未用 str() 转换，与注解 str | None 不一致。"
            "应在赋值时 str(bgm_data[0]['id'])，与其他 step 保持一致。"
        )
    )
    def test_verify_subject_id_type_consistency(self) -> None:
        """subject_id 应为 str 类型（与注解一致）

        构造 bgm_search 返回 int id 的候选，运行 APISearchStep。
        期望 ctx.subject_id 为 str，实际为 int。
        """
        ctx = _build_ctx(title="测试番剧", season=1, media_type="episode")
        bgm = _make_bgm_mock(
            candidates=[
                {
                    "id": 12345,  # int，模拟 API/archive 返回值
                    "type": 2,  # SUBJECT_TYPE_ANIME → detect = episode，不触发改选
                    "name": "测试番剧",
                    "name_cn": "测试番剧",
                    "platform": "TV",
                    "date": "2024-01-15",
                }
            ],
            last_hit_source="",
        )
        _configure_service_mock(ctx.service, bgm, threshold=0.6)

        with patch("app.services.sync_service.config_manager") as mock_cfg:
            mock_cfg.get.side_effect = lambda s, k, fallback=None: (
                False if (s, k) == ("sync", "enable_real_action") else fallback
            )
            outcome = APISearchStep().execute(ctx)

        # 期望行为：subject_id 为 str（与注解一致）
        assert outcome.status == "hit"
        assert isinstance(ctx.subject_id, str), (
            f"期望 str，实际 {type(ctx.subject_id).__name__}: {ctx.subject_id!r}"
        )


# ======================================================================
# P2-2: _match_target_ep_rows 返回类型注解错误
# ======================================================================


class TestVerifyP02MatchTargetEpRowsAnnotation:
    """P2-2: _match_target_ep_rows 注解为 dict | None，实际返回 list[dict]

    疑似问题：``_match_target_ep_rows``（episodes.py line 416-427）注解为
    ``-> dict[str, Any] | None``，但函数体构建 ``rows``（list）并 ``return rows``，
    实际返回 ``list[dict]``。注解与实际返回类型不符。

    验证方法：用 inspect.signature 读取返回注解，断言应指示 list 类型。
    """

    @pytest.mark.xfail(
        reason=(
            "P2-2 Confirmed: _match_target_ep_rows 注解为 dict[str, Any] | None"
            "（episodes.py line 418），但函数体 return rows（list[dict]）。"
            "注解应改为 list[dict[str, Any]]。"
        )
    )
    def test_verify_match_target_ep_rows_annotation(self) -> None:
        """_match_target_ep_rows 返回注解应指示 list 类型

        读取函数返回注解，期望包含 "list"（正确描述返回类型），
        实际注解为 "dict[str, Any] | None"（不含 "list"）。
        """
        sig = inspect.signature(BangumiApi._match_target_ep_rows)
        return_annotation = sig.return_annotation

        # 期望行为：注解应指示 list 类型（函数实际返回 list[dict]）
        # episodes.py 有 from __future__ import annotations，注解为字符串
        annotation_str = (
            return_annotation
            if isinstance(return_annotation, str)
            else str(return_annotation)
        )
        assert "list" in annotation_str.lower(), (
            f"期望注解包含 'list'（函数返回 list[dict]），实际注解: {annotation_str!r}"
        )

    def test_verify_match_target_ep_rows_returns_list(self) -> None:
        """_match_target_ep_rows 实际返回 list[dict]（确认行为）

        直接调用函数，验证返回值为 list 而非 dict/None。
        这是行为确认型测试（通过），证明注解确实错误。
        """
        api = BangumiApi()
        ep_info = [
            {"id": 1, "sort": 5, "ep": 5, "type": 0},
            {"id": 2, "sort": 10, "ep": 10, "type": 0},
        ]
        result = api._match_target_ep_rows(ep_info, target_ep=5)

        # 实际行为：返回 list[dict]（与注解 dict|None 不符）
        assert isinstance(result, list), (
            f"期望 list（实际返回类型），实际 {type(result).__name__}: {result!r}"
        )
        assert len(result) == 1
        assert result[0]["id"] == 1


# ======================================================================
# P2-3: _post_search_reselect 原地修改 bgm_data[0] 导致 candidates[0] 不一致
# ======================================================================


class TestVerifyP03CandidatesTopAfterReselect:
    """P2-3: candidates[0] 在 _post_search_reselect 改选后应与 ctx.subject_id 一致（已修复）

    修复前问题：APISearchStep.execute 在 line 191-204 从 ``bgm_data[:5]`` 构建
    ``candidates`` 列表（拷贝 id 到 MatchCandidate）。随后 ``_post_search_reselect``
    在 line 364/452/473 执行 ``bgm_data[0] = cand``（原地替换），但 ``candidates``
    列表不会同步更新 → trace 中 ``candidates[0]`` 仍是原 top，与实际
    ``bgm_data[0]``（改选后）和 ``ctx.subject_id`` 不一致。

    修复后：_post_search_reselect 后若 bgm_data[0] 改变，重建 candidates[0]
    以反映改选后的 top 候选，确保 candidates[0].subject_id 与 ctx.subject_id 一致。

    场景：bgm_search 返回原 top id=100（type=6 真人剧），改选为 id=200（type=2 动画）。
    期望 candidates[0].subject_id == "200"（与 ctx.subject_id 一致）。
    """

    def test_verify_candidates_top_after_reselect(self) -> None:
        """candidates[0] 应在改选后与 ctx.subject_id 一致（已修复）

        构造媒体类型改选场景：原 top id=100（type=6 真人剧），
        改选为 id=200（type=2 动画）。期望 candidates[0].subject_id == "200"。
        """
        ctx = _build_ctx(title="凡人修仙传", season=1, media_type="episode")
        bgm = _make_bgm_mock(
            candidates=[
                {
                    "id": 100,
                    "type": 6,  # SUBJECT_TYPE_REAL → detect = real_action
                    "name": "凡人修仙传",
                    "name_cn": "凡人修仙传",
                    "platform": "TV",
                    "date": "2025-07-27",
                },
                {
                    "id": 200,
                    "type": 2,  # SUBJECT_TYPE_ANIME → detect = episode
                    "name": "凡人修仙传 新年番",
                    "name_cn": "凡人修仙传 新年番",
                    "platform": "TV",
                    "date": "2023-11-25",
                },
            ],
            last_hit_source="",
        )
        bgm.title_diff_ratio.return_value = 0.95
        bgm.get_related_subjects.return_value = []

        # _pick_mainline_episode_candidate 返回 id=200（触发改选 bgm_data[0] = cand）
        _configure_service_mock(
            ctx.service, bgm, threshold=0.6, pick_mainline_return={"id": 200}
        )

        with patch("app.services.sync_service.config_manager") as mock_cfg:
            mock_cfg.get.side_effect = lambda s, k, fallback=None: (
                False if (s, k) == ("sync", "enable_real_action") else fallback
            )
            outcome = APISearchStep().execute(ctx)

        # 期望行为：candidates[0] 与 ctx.subject_id 一致（改选后同步）
        assert outcome.status == "hit"
        assert ctx.subject_id == 200
        assert outcome.candidates, "candidates 不应为空"
        # 期望 candidates[0].subject_id == "200"（与 ctx.subject_id 一致）
        assert outcome.candidates[0].subject_id == str(ctx.subject_id), (
            f"candidates[0].subject_id={outcome.candidates[0].subject_id!r}, "
            f"ctx.subject_id={ctx.subject_id!r}，二者不一致"
        )


# ======================================================================
# P2-4: DateExactSearchStep movie 分支覆写 ctx.end_date_str
# ======================================================================


class TestVerifyP04DateExactMovieOverwriteEndDateStr:
    """P2-4: movie 未命中时 ctx.end_date_str 被覆写为 +200 天

    疑似问题：DateExactSearchStep（api_search.py line 124-133）在 movie 未命中时
    执行 ``ctx.end_date_str = movie_end_date.strftime(...)``，将 end_date_str
    从 ±2 天（line 81 设置）覆写为 +200 天。仅影响日志展示（end_date_str 用于
    debug 日志），不影响搜索逻辑。

    验证方法：构造 movie 媒体类型 + 未命中场景，断言 ctx.end_date_str 被覆写
    为 +200 天的值（确认行为存在）。
    """

    def test_verify_date_exact_movie_overwrite_end_date_str(self) -> None:
        """movie 未命中时 ctx.end_date_str 被覆写为 +200 天（确认行为存在）

        构造 movie + 有效 premiere_date + search 全返回空。
        期望 ctx.end_date_str 被覆写为 air_date + 200 天的值。
        """
        bgm = SimpleNamespace(
            last_hit_source="",
            last_match_method="",
            search=lambda **kw: [],  # 所有搜索返回空（未命中）
            title_diff_ratio=lambda *a, **kw: 0.9,
        )
        ctx = MatchContext(
            item=CustomItem(
                media_type="movie",
                title="测试电影",
                ori_title=None,
                season=1,
                episode=1,
                release_date="2024-01-15",
                user_name="u",
                source="test",
            ),
            bgm=bgm,
            trace=MatchTrace(),
            subject_types=[2],
        )

        outcome = DateExactSearchStep().execute(ctx)

        # 计算 +200 天的期望值
        air_date = datetime.datetime.fromisoformat("2024-01-15")
        expected_end_date = (air_date + datetime.timedelta(days=200)).strftime(
            "%Y-%m-%d"
        )
        # 原始 ±2 天的 end_date_str
        original_end_date = (air_date + datetime.timedelta(days=2)).strftime("%Y-%m-%d")

        # 确认行为：ctx.end_date_str 被覆写为 +200 天的值
        assert ctx.end_date_str == expected_end_date, (
            f"期望 end_date_str 被覆写为 +200 天 ({expected_end_date})，"
            f"实际 {ctx.end_date_str!r}"
        )
        assert ctx.end_date_str != original_end_date, (
            f"end_date_str 不应保持原始 ±2 天值 ({original_end_date})，"
            f"应被 movie 分支覆写为 +200 天"
        )
        # movie 分支搜索仍 miss
        assert outcome.status == "miss"


# ======================================================================
# P2-5: archive 续集链/前传链空链处理不对称
# ======================================================================


class TestVerifyP05ArchiveEmptyChainHandlingSymmetric:
    """P2-5: archive 空链降级处理结构不对称（行为对称）

    疑似问题：sequel 方向在 ``_walk_chain_for_episode`` 内联处理空链降级
    （episodes.py line 763 注释"archive 命中但续集链为空：降级到逐跳 API"，
    随后落入 line 778 逐跳循环）；prequel 方向在 ``search_previous_subjects``
    内部处理（relations.py line 54-58，archive miss/空链 → _walk_prequel_flat）。
    代码结构不对称，但行为是否对称需验证。

    验证方法：分别测试 sequel 空链和 prequel 空链场景，断言两条路径都降级到
    API（行为对称）。若行为对称则这是代码风格问题（Confirmed 但低优先级）。
    """

    def test_verify_archive_empty_chain_handling_symmetric(self) -> None:
        """sequel 空链和 prequel 空链都应降级到 API（行为对称）

        构造 archive 返回 hit=True, data=[]（空链）的场景，分别测试 sequel 和
        prequel 方向，断言两条路径都调用了 get_related_subjects（API 降级）。
        """
        target_subject = {
            "id": 1001,
            "type": 2,  # SUBJECT_TYPE_ANIME
            "name": "test",
            "name_cn": "测试",
        }
        target_episode = {"id": 5001, "sort": 300}

        # --- sequel 方向：archive 空链 ---
        api_sequel = BangumiApi()
        api_sequel._archive = MagicMock()
        # archive 续集链命中但为空
        api_sequel._archive.try_find_sequel_chain.return_value = ShortcutResult(
            True, [], "archive_hit"
        )
        # archive 关联查找 miss，强制走 get_related_subjects
        api_sequel._archive.try_find_related_id_by_relation.return_value = (
            ShortcutResult(False, None, "archive_miss")
        )

        with (
            patch.object(
                api_sequel,
                "get_related_subjects",
                return_value=[{"id": 1001, "relation": "续集"}],
            ) as mock_related_sequel,
            patch.object(api_sequel, "get_subject", return_value=target_subject),
            patch.object(
                api_sequel, "_find_episode_by_sort", return_value=target_episode
            ),
        ):
            sequel_result = api_sequel._walk_chain_for_episode(
                start_id=1,
                target_ep=300,
                direction="sequel",
                visited={1},
                max_depth=15,
                deadline=None,
            )

        # sequel 空链应降级到 API（逐跳循环调用 get_related_subjects）
        assert mock_related_sequel.called, (
            "sequel 方向 archive 空链应降级到 get_related_subjects（逐跳 API）"
        )
        assert sequel_result is not None, (
            "sequel 方向 archive 空链降级后应通过 API 找到目标"
        )

        # --- prequel 方向：archive 空链 ---
        api_prequel = BangumiApi()
        api_prequel._archive = MagicMock()
        # archive 前传链命中但为空
        api_prequel._archive.try_find_prequel_chain.return_value = ShortcutResult(
            True, [], "archive_hit"
        )
        # archive 关联查找 miss，强制走 get_related_subjects
        api_prequel._archive.try_find_related_id_by_relation.return_value = (
            ShortcutResult(False, None, "archive_miss")
        )

        def fake_get_related(subject_id):
            # subject 1 有前传 1001；1001 无更多前传
            if subject_id == 1:
                return [{"id": 1001, "relation": "前传"}]
            return []

        with (
            patch.object(
                api_prequel, "get_related_subjects", side_effect=fake_get_related
            ) as mock_related_prequel,
            patch.object(api_prequel, "get_subject", return_value=target_subject),
            patch.object(
                api_prequel,
                "_find_episode_by_sort",
                return_value={"id": 5001, "sort": 5},
            ),
        ):
            prequel_result = api_prequel._walk_chain_for_episode(
                start_id=1,
                target_ep=5,
                direction="prequel",
                visited={1},
                max_depth=15,
                deadline=None,
            )

        # prequel 空链应降级到 API（_walk_prequel_flat → _prequel_one_hop → get_related_subjects）
        assert mock_related_prequel.called, (
            "prequel 方向 archive 空链应降级到 get_related_subjects"
            "（_walk_prequel_flat → _prequel_one_hop）"
        )
        assert prequel_result is not None, (
            "prequel 方向 archive 空链降级后应通过 API 找到目标"
        )

        # 行为对称：两条路径都降级到 API
        # 这是代码风格问题（Confirmed 但低优先级）：sequel 内联处理、prequel 委托
        # search_previous_subjects 处理，结构不对称但行为对称。


# ======================================================================
# P2-6: _pick_mainline_episode_candidate 空输入返回 {}
# ======================================================================


class TestVerifyP06PickMainlineEmptyInput:
    """P2-6: _pick_mainline_episode_candidate 空输入返回 {}

    疑似问题：``_pick_mainline_episode_candidate``（sync_service/__init__.py
    line 996-997）在 ``candidates`` 为空时 ``return {}``（空 dict），
    带 ``# type: ignore[return-value]``。注解为 ``-> dict`` 但空输入返回 {}
    而非 None，调用方需额外判断。

    验证方法：调用 ``_pick_mainline_episode_candidate([])``，断言返回值为 {}
    （空 dict）。同时确认调用方 api_search_main.py line 447 有 ``if episode_candidates:``
    守卫，不会传入空列表。
    """

    def test_verify_pick_mainline_empty_input(self) -> None:
        """_pick_mainline_episode_candidate([]) 返回 {}（确认行为存在）

        调用静态方法传入空列表，断言返回值为空 dict（而非 None）。
        """
        from app.services.sync_service import SyncService

        result = SyncService._pick_mainline_episode_candidate([], "")

        # 确认行为：返回 {}（空 dict），而非 None
        assert result == {}, f"期望空 dict {{}}，实际 {result!r}"
        assert isinstance(result, dict), f"期望 dict 类型，实际 {type(result).__name__}"
        assert not result, "空 dict 应为 falsy"

    def test_verify_pick_mainline_call_site_has_guard(self) -> None:
        """调用方 api_search_main.py 有 if episode_candidates 守卫

        确认 _media_type_reselect 在调用 _pick_mainline_episode_candidate 前
        有 ``if episode_candidates:`` 守卫（line 447），不会传入空列表。
        通过读取源码断言守卫存在。
        """
        import app.services.matching.steps.api_search_main as mod

        source = inspect.getsource(mod.APISearchStep._media_type_reselect)
        # 守卫存在：if episode_candidates: ... _pick_mainline_episode_candidate(...)
        assert "if episode_candidates:" in source, (
            "_media_type_reselect 应有 'if episode_candidates:' 守卫，"
            "避免传入空列表给 _pick_mainline_episode_candidate"
        )
        assert "_pick_mainline_episode_candidate" in source, (
            "_media_type_reselect 应调用 _pick_mainline_episode_candidate"
        )
