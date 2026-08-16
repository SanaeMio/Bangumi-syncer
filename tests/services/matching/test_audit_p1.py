"""P1 中等问题验证测试

验证 6 个 P1 中等问题：
- P1-1: 置信度阈值检查使用改选前的 top 分数（已修复）
- P1-2: is_ambiguous 检测与通知候选不一致（已修复）
- P1-3: VariantFallbackSearchStep 全 miss 清空 bgm_data 导致候选无法沉淀
- P1-4: bgm_search 子 step trace 不写入主 trace
- P1-5: archive 续集链/前传链非空未找到目标时无 API 兜底
- P1-6: find_episode_across_seasons 空 sorts 直接返回 None

已修复问题用普通测试（断言修复后行为且通过）。
待修复问题用 @pytest.mark.xfail 标记（断言期望行为但当前失败，不破坏 CI）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.models.sync import CustomItem
from app.services.matching.context import MatchContext
from app.services.matching.pipeline import MatchPipeline
from app.services.matching.steps.api_search_main import APISearchStep
from app.services.sync_service.match_trace import MatchTrace
from app.utils.bangumi_api import BangumiApi
from app.utils.bangumi_api._archive_shortcut import ShortcutResult

# ----------------------------------------------------------------------
# 辅助函数（参考 test_steps.py / test_audit_verification.py 的 mock 模式）
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
# P1-1: 置信度阈值检查使用改选前的 top 分数
# ======================================================================


class TestVerifyP01ConfidenceUsesPreReselectScore:
    """P1-1: 置信度阈值检查应使用改选后的 top 分数（已修复）

    修复前问题：APISearchStep.execute 在 line 225 使用 ``real_conf = candidates[0].score``，
    但 candidates 列表是在 _post_search_reselect 之前构建的（line 191-204）。
    若 _post_search_reselect 把 bgm_data[0] 改选为更优候选，置信度检查仍用
    原 top 的低分 → 错误标记 low_confidence。

    修复后：_post_search_reselect 后若 bgm_data[0] 改变，重建 candidates[0]
    以反映改选后的 top 候选，置信度检查使用正确的 score。

    场景：bgm_search 返回 2 个候选：
    - bgm_data[0] (id=434076, type=6 真人剧): score=0.3（低相似度）
    - bgm_data[1] (id=406306, type=2 动画): score=0.95（高相似度）
    媒体类型改选将 bgm_data[0] 改为 id=406306（score=0.95）。
    期望 outcome.status == "hit"（改选后高置信度）。
    """

    def test_verify_confidence_uses_pre_reselect_score(self) -> None:
        """置信度阈值检查应使用改选后的 top 分数（已修复）

        构造媒体类型改选场景：原 top 为真人剧（type=6, score=0.3），
        改选为动画版（type=2, score=0.95）。期望改选后高置信度命中。
        """
        ctx = _build_ctx(title="凡人修仙传", season=1, media_type="episode")
        bgm = _make_bgm_mock(
            candidates=[
                {
                    "id": 434076,
                    "type": 6,  # SUBJECT_TYPE_REAL → detect = real_action
                    "name": "凡人修仙传",
                    "name_cn": "凡人修仙传",
                    "platform": "TV",
                    "date": "2025-07-27",
                },
                {
                    "id": 406306,
                    "type": 2,  # SUBJECT_TYPE_ANIME → detect = episode
                    "name": "凡人修仙传 新年番",
                    "name_cn": "",
                    "platform": "TV",
                    "date": "2023-11-25",
                },
            ],
            last_hit_source="",
        )

        # title_diff_ratio 返回不同分数：原 top 0.3，动画版 0.95
        def fake_ratio(title, ori_title, bgm_data):
            return 0.3 if bgm_data.get("id") == 434076 else 0.95

        bgm.title_diff_ratio.side_effect = fake_ratio

        # _pick_mainline_episode_candidate 返回动画版（触发改选）
        _configure_service_mock(
            ctx.service, bgm, threshold=0.6, pick_mainline_return={"id": 406306}
        )

        with patch("app.services.sync_service.config_manager") as mock_cfg:
            mock_cfg.get.side_effect = lambda s, k, fallback=None: (
                False if (s, k) == ("sync", "enable_real_action") else fallback
            )
            outcome = APISearchStep().execute(ctx)

        # 期望行为：改选后高置信度命中
        assert outcome.status == "hit"
        assert outcome.subject_id == 406306


# ======================================================================
# P1-2: is_ambiguous 检测与通知候选不一致
# ======================================================================


class TestVerifyP02IsAmbiguousUsesPreReselectTop2:
    """P1-2: is_ambiguous 检测应使用改选后的 top-2（已修复）

    修复前问题：APISearchStep 用 candidates[0] 与 candidates[1]（post_search
    改选前的 top-2）算 ``< 0.05`` 设 is_ambiguous（line 257-263）；
    但 _maybe_notify_match_ambiguous 用 _collect_candidates_from_trace
    收集所有 step 的候选按 score 降序取 top-2。

    修复后：is_ambiguous 检测改为按 score 降序取 top-2（与
    _collect_candidates_from_trace 行为一致），避免漏判歧义。

    场景：改选前 top-2 差大（0.9 vs 0.3，差 0.6 > 0.05）但改选后
    top-2 差小（0.95 vs 0.91，差 0.04 < 0.05）。
    期望 is_ambiguous == True（改选后应歧义）。
    """

    def test_verify_is_ambiguous_uses_pre_reselect_top2(self) -> None:
        """is_ambiguous 应基于改选后的 top-2 候选判断（已修复）

        构造场景：原 top-2 差大（0.9 vs 0.3）但改选后追加的高分候选
        top-2 差小（0.95 vs 0.91）。期望 is_ambiguous=True。
        """
        ctx = _build_ctx(title="测试番剧", season=1, media_type="episode")
        bgm = _make_bgm_mock(
            candidates=[
                {
                    "id": 1,
                    "type": 6,  # real_action → 触发媒体类型改选
                    "name": "测试番剧",
                    "name_cn": "测试番剧",
                    "platform": "TV",
                    "date": "2024-01-15",
                },
                {
                    "id": 2,
                    "type": 2,  # episode
                    "name": "测试番剧 第二季",
                    "name_cn": "测试番剧 第二季",
                    "platform": "TV",
                    "date": "2024-01-15",
                },
                {
                    "id": 3,
                    "type": 2,  # episode
                    "name": "测试番剧 第三季",
                    "name_cn": "测试番剧 第三季",
                    "platform": "TV",
                    "date": "2024-01-15",
                },
            ],
            last_hit_source="",
        )

        # title_diff_ratio 按候选 id 返回不同分数：
        # 原始 top-2（id=1, id=2）差大：0.9 vs 0.3
        # 追加的 episode 候选（id=3）高分：0.95
        # 关联条目候选（id=4）高分：0.91
        def fake_ratio(title, ori_title, bgm_data):
            cid = bgm_data.get("id")
            if cid == 1:
                return 0.9
            if cid == 2:
                return 0.3
            if cid == 3:
                return 0.95
            if cid == 4:
                return 0.91
            return 0.5

        bgm.title_diff_ratio.side_effect = fake_ratio

        # _pick_mainline_episode_candidate 返回 id=1（等于原 top，不触发改选）
        _configure_service_mock(
            ctx.service, bgm, threshold=0.6, pick_mainline_return={"id": 1}
        )
        # get_related_subjects 返回空，避免关联条目改选干扰
        bgm.get_related_subjects.return_value = []

        with patch("app.services.sync_service.config_manager") as mock_cfg:
            mock_cfg.get.side_effect = lambda s, k, fallback=None: (
                False if (s, k) == ("sync", "enable_real_action") else fallback
            )
            outcome = APISearchStep().execute(ctx)

        # 期望行为：改选后 top-2 为 0.95 与 0.91，差 0.04 < 0.05 → 歧义
        assert outcome.status == "hit"
        assert ctx.is_ambiguous is True


# ======================================================================
# P1-3: VariantFallbackSearchStep 全 miss 清空 bgm_data 导致候选无法沉淀
# ======================================================================


class TestVerifyP03VariantMissClearsBgmData:
    """P1-3: VariantFallbackSearchStep 全 miss 时应保留低相似度精确搜索候选

    疑似问题：VariantFallbackSearchStep 全 miss 时 ``ctx.bgm_data = None``
    （line 219），丢弃 DateExactSearchStep 的低相似度候选。导致
    APISearchStep 返回 miss + is_terminal，不构建 candidates，无候选可沉淀。

    场景：DateExactSearchStep 命中 0.4 分候选（< PRIMARY=0.5，触发兜底），
    VariantFallbackSearchStep 全 miss（所有变体搜索返回空）。
    期望 bgm_data 保留 0.4 候选（供 APISearchStep 沉淀），实际被清空为 None。
    """

    @pytest.mark.xfail(
        reason=(
            "P1-3 Confirmed: VariantFallbackSearchStep 全 miss 时 ctx.bgm_data = None"
            "（line 219），丢弃 DateExactSearchStep 的低相似度候选。"
            "应在全 miss 时保留原精确搜索候选（即使低相似度），"
            "让 APISearchStep 有候选可沉淀供用户手动确认。"
        )
    )
    def test_verify_variant_miss_clears_bgm_data(self) -> None:
        """VariantFallbackSearchStep 全 miss 应保留精确搜索候选

        通过 bgm.bgm_search 包装函数运行 4 个子 step 管道：
        DateExactSearchStep 命中 0.4 分候选 + VariantFallbackSearchStep 全 miss。
        期望 bgm_search 返回非空候选列表，实际返回 None。
        """
        api = BangumiApi()
        candidate = {
            "id": 1,
            "name": "测试番剧",
            "name_cn": "测试番剧",
            "platform": "TV",
            "date": "2024-01-15",
        }

        # search: DateExactSearchStep（start_date 非空）返回候选，
        #         VariantFallbackSearchStep（start_date 为空）返回空
        def fake_search(**kw):
            if kw.get("start_date"):
                return [candidate]
            return []

        with (
            patch.object(api, "search", side_effect=fake_search),
            patch.object(
                api,
                "title_diff_ratio",
                return_value=0.4,  # < PRIMARY(0.5) 触发兜底
            ),
        ):
            result = api.bgm_search(
                title="测试番剧",
                ori_title="",
                premiere_date="2024-01-15",
                is_movie=False,
                subject_types=[2],
            )

        # 期望行为：保留 0.4 分候选，返回非空列表
        assert result is not None
        assert len(result) > 0


# ======================================================================
# P1-4: bgm_search 子 step trace 不写入主 trace
# ======================================================================


class TestVerifyP04SubstepsTraceInMain:
    """P1-4: bgm_search 子 step trace 应写入主 trace

    疑似问题：bgm_search（search.py line 235-260）创建独立 MatchContext + 空
    MatchTrace，4 个子 step 的 trace 全部丢弃。主 trace 只能看到
    APISearchStep 一条汇总记录。

    场景：运行完整 APISearchStep（bgm.bgm_search 实际执行 4 个子 step），
    检查 ctx.trace 的 steps。期望包含 stage="api_search_date_exact" 或
    "api_search_variant_fallback" 的子 step 记录，实际不包含。
    """

    @pytest.mark.xfail(
        reason=(
            "P1-4 Confirmed: bgm_search 创建独立 MatchContext + 空 MatchTrace"
            "（search.py line 235-248），4 个子 step 的 trace 全部丢弃。"
            "主 trace 只能看到 APISearchStep 一条汇总记录，无法展示子 step 过程。"
            "应将主 ctx.trace 传入 bgm_search，或让子 step 通过 ctx.parent_trace"
            "追加记录。"
        )
    )
    def test_verify_substeps_trace_in_main(self) -> None:
        """bgm_search 子 step trace 应写入主 trace

        使用真实 BangumiApi（mock search 方法）运行 APISearchStep，
        bgm_search 内部 4 个子 step 实际执行但 trace 写入内部 ctx，
        主 ctx.trace 不包含子 step 记录。
        """
        ctx = _build_ctx(title="测试番剧", season=1, media_type="episode")
        api = BangumiApi()
        candidate = {
            "id": 12345,
            "name": "测试番剧",
            "name_cn": "测试番剧",
            "platform": "TV",
            "date": "2024-01-15",
        }

        def fake_search(**kw):
            if kw.get("start_date"):
                return [candidate]
            return []

        # 配置 ctx.service 使用真实 BangumiApi
        ctx.service._get_bangumi_api_for_user.return_value = api
        ctx.service._sort_candidates_by_platform.side_effect = lambda data, **kw: data
        ctx.service._get_match_confidence_threshold.return_value = 0.6
        ctx.service._check_season_info_in_title.return_value = False
        ctx.service._get_explicit_season_from_title.return_value = 0
        ctx.service._pick_mainline_episode_candidate.return_value = candidate

        with (
            patch.object(api, "search", side_effect=fake_search),
            patch.object(api, "title_diff_ratio", return_value=0.95),
            patch("app.services.sync_service.config_manager") as mock_cfg,
        ):
            mock_cfg.get.side_effect = lambda s, k, fallback=None: (
                False if (s, k) == ("sync", "enable_real_action") else fallback
            )
            pipeline = MatchPipeline([APISearchStep()])
            pipeline.run(ctx)

        # 收集主 trace 中所有 step 的 stage
        stages = [s.stage for s in ctx.trace.steps]
        # 期望行为：主 trace 包含子 step 记录
        assert any(
            s in stages
            for s in ("api_search_date_exact", "api_search_variant_fallback")
        ), f"主 trace 不包含子 step 记录，stages={stages}"


# ======================================================================
# P1-5: archive 续集链非空未找到目标时无 API 兜底
# ======================================================================


class TestVerifyP05ArchiveSequelChainNonemptyMissNoFallback:
    """P1-5: archive 续集链非空未找到目标时应降级逐跳 API

    疑似问题：sequel 方向 archive 链非空但未找到目标 episode → ``return None``
    （episodes.py line 762），不再逐跳 API。

    场景：archive_store.try_find_sequel_chain 返回 hit=True, data=[1001, 1002]
    （都不含目标 sort），_find_episode_in_chain 返回 None。期望降级调用
    get_related_subjects 逐跳查找，实际直接返回 None。
    """

    @pytest.mark.xfail(
        reason=(
            "P1-5 Confirmed: _walk_chain_for_episode sequel 方向 archive 链非空"
            "但未找到目标时 return None（line 762），不再逐跳 API。"
            "应在 archive 链未找到时降级到逐跳 _find_related_id_by_relation 路径，"
            "与 archive miss / 空链时行为一致。"
        )
    )
    def test_verify_archive_sequel_chain_nonempty_miss_no_fallback(self) -> None:
        """archive 续集链非空未找到目标应降级逐跳 API

        构造 archive 续集链命中但未找到目标，逐跳 API 能找到目标的场景。
        期望降级到逐跳并返回结果，实际直接返回 None。
        """
        api = BangumiApi()
        api._archive = MagicMock()
        # archive 续集链命中（非空）
        api._archive.try_find_sequel_chain.return_value = ShortcutResult(
            True, [1001, 1002], "archive_hit"
        )
        # archive 关联查找 miss，强制走 get_related_subjects
        api._archive.try_find_related_id_by_relation.return_value = ShortcutResult(
            False, None, "archive_miss"
        )

        def fake_find_by_sort(subject_id, target_sort, _type=0):
            # 原始 subject 未命中
            if subject_id == 1:
                return None
            # 逐跳找到的目标 subject 命中
            if subject_id == 1001:
                return {"id": 5001, "sort": target_sort}
            return None

        with (
            patch.object(api, "_find_episode_by_sort", side_effect=fake_find_by_sort),
            patch.object(
                api,
                "get_episodes",
                return_value={
                    "data": [
                        {"type": 0, "sort": 200},
                        {"type": 0, "sort": 250},
                    ],
                    "total": 2,
                },
            ),
            patch.object(
                api,
                "_find_episode_in_chain",
                return_value=None,  # archive 链未找到目标
            ),
            patch.object(
                api,
                "get_related_subjects",
                return_value=[{"id": 1001, "relation": "续集"}],
            ) as mock_related,
            patch.object(
                api,
                "get_subject",
                return_value={
                    "id": 1001,
                    "type": 2,
                    "name": "test",
                    "name_cn": "测试",
                },
            ),
        ):
            result = api.find_episode_across_seasons(subject_id=1, target_ep=300)

        # 期望行为：降级逐跳 API 查找
        assert mock_related.called, "应降级调用 get_related_subjects 逐跳查找"
        # 期望行为：逐跳路径找到目标
        assert result is not None, "应通过逐跳 API 找到目标"


# ======================================================================
# P1-6: find_episode_across_seasons 空 sorts 直接返回 None
# ======================================================================


class TestVerifyP06EmptySortsReturnsNoneDirectly:
    """P1-6: find_episode_across_seasons 空 sorts 应尝试 prequel/sequel 方向

    疑似问题：type0_rows 提取 sort 后若 ``not sorts`` 直接 ``return None``
    （episodes.py line 690-693），不尝试 prequel/sequel 方向。

    场景：get_episodes 返回空列表或全 SP 章节（无 type=0），mock 关联季查找
    能找到目标 sort。期望尝试 prequel/sequel 方向并返回结果，实际直接返回 None。
    """

    @pytest.mark.xfail(
        reason=(
            "P1-6 Confirmed: find_episode_across_seasons 在 sorts 为空时"
            "直接 return None（line 692-693），不尝试 prequel/sequel 方向。"
            "应在 sorts 为空时仍尝试遍历关联季，或至少两个方向都试。"
        )
    )
    def test_verify_empty_sorts_returns_none_directly(self) -> None:
        """空 sorts 应尝试 prequel/sequel 方向

        构造 get_episodes 返回空（无 type=0 章节），mock 续集链能找到目标。
        期望尝试 sequel 方向并返回结果，实际直接返回 None。
        """
        api = BangumiApi()
        api._archive = MagicMock()
        # archive 续集链命中（含目标 subject）
        api._archive.try_find_sequel_chain.return_value = ShortcutResult(
            True, [1001], "archive_hit"
        )

        def fake_find_by_sort(subject_id, target_sort, _type=0):
            # 原始 subject 未命中
            if subject_id == 1:
                return None
            # 续集链中的 subject 命中
            if subject_id == 1001:
                return {"id": 5001, "sort": target_sort}
            return None

        with (
            patch.object(api, "_find_episode_by_sort", side_effect=fake_find_by_sort),
            patch.object(
                api,
                "get_episodes",
                return_value={"data": [], "total": 0},  # 空 → sorts 为空
            ),
            patch.object(
                api,
                "_find_episode_in_chain",
                return_value=(1001, 5001),  # 续集链找到目标
            ),
        ):
            result = api.find_episode_across_seasons(subject_id=1, target_ep=5)

        # 期望行为：尝试 sequel 方向并返回结果
        assert result is not None, "应尝试 sequel 方向查找"
        assert result == (1001, 5001)
