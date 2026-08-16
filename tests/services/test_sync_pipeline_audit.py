"""端到端验证测试：4 个关键场景的实际行为验证

验证场景：
- A5-1: "凡人修仙传 S01E81" 完整路径（archive 命中真人剧 → 媒体类型不匹配降级 API → 改选动画版）
- A5-2: archive 单候选场景 final_match_method_detail 透传
- A5-3: 低置信度场景候选沉淀（trace.candidates 应非空）
- A5-4: 跨季查找场景 archive 链不完整（应降级 API）

Confirmed 问题（实际行为与期望不符）用 @pytest.mark.xfail 标记。
Refuted 问题保留为普通测试。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.models.sync import CustomItem
from app.services.matching.context import MatchContext
from app.services.matching.pipeline import MatchPipeline
from app.services.matching.steps.api_search_main import APISearchStep
from app.services.sync_service.match_trace import MatchTrace
from app.utils.bangumi_api import BangumiApi
from app.utils.bangumi_api._archive_shortcut import ShortcutResult

# ----------------------------------------------------------------------
# 辅助函数（参考 test_steps.py / test_audit_p1.py 的 mock 模式）
# ----------------------------------------------------------------------


def _build_ctx(
    title: str = "测试番剧",
    ori_title: str | None = None,
    season: int = 1,
    media_type: str = "episode",
    release_date: str = "2024-01-15",
    episode: int = 1,
) -> MatchContext:
    """构建测试用 MatchContext"""
    service = MagicMock()
    return MatchContext(
        item=CustomItem(
            media_type=media_type,
            title=title,
            ori_title=ori_title,
            season=season,
            episode=episode,
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
    bgm.get_related_subjects.return_value = []
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
# A5-1: 验证"凡人修仙传 S01E81"完整路径
# ======================================================================


class TestVerifyA51FanrenS01e81FullPath:
    """A5-1: archive 命中真人剧 → 媒体类型不匹配降级 API → 改选动画版

    场景：
    - archive 命中真人剧 434076（type=6, 标题完全匹配"凡人修仙传"）
    - APISearchStep 检测 ctx.bgm_data 顶部为 real_action 但无 episode 候选
      → 清空 ctx.bgm_data → 降级 API 搜索
    - API 返回 [434076 (type=6), 406306 (type=2)]
    - 媒体类型改选 → bgm_data[0] 改为 406306
    - 期望 outcome.subject_id == 406306（动画版）而非 434076（真人剧）

    依赖已修复的降级逻辑（_detect_candidate_media_type 优先用 subject type 字段 +
    archive 媒体类型不匹配降级 API）。
    """

    def test_verify_fanren_s01e81_full_path(self) -> None:
        """凡人修仙传 S01E81 完整路径：archive 真人剧 → 降级 API → 改选动画版"""
        ctx = _build_ctx(
            title="凡人修仙传",
            season=1,
            media_type="episode",
            episode=81,
        )

        # archive 命中真人剧（type=6, 标题完全匹配）
        archive_candidate = {
            "id": 434076,
            "type": 6,  # SUBJECT_TYPE_REAL → detect = real_action
            "name": "凡人修仙传",
            "name_cn": "凡人修仙传",
            "platform": "TV",
            "date": "2025-07-27",
        }

        # API 搜索返回：top 为真人剧，次为动画版
        api_candidates = [
            archive_candidate,
            {
                "id": 406306,
                "type": 2,  # SUBJECT_TYPE_ANIME → detect = episode
                "name": "凡人修仙传 新年番",
                "name_cn": "",
                "platform": "TV",
                "date": "2023-11-25",
            },
        ]

        bgm = _make_bgm_mock(
            candidates=api_candidates,
            last_hit_source="archive",  # archive 命中标记
            title_diff_ratio=0.95,
        )
        # _pick_mainline_episode_candidate 返回动画版（触发改选）
        _configure_service_mock(
            ctx.service, bgm, threshold=0.6, pick_mainline_return={"id": 406306}
        )

        # 模拟 ArchiveShortcutStep 已命中设置 ctx
        ctx.bgm_data = [archive_candidate]
        ctx.match_stage = "archive"
        ctx.match_method_detail = "exact"
        ctx.bgm = bgm

        with patch("app.services.sync_service.config_manager") as mock_cfg:
            mock_cfg.get.side_effect = lambda s, k, fallback=None: (
                False if (s, k) == ("sync", "enable_real_action") else fallback
            )
            outcome = APISearchStep().execute(ctx)

        # 期望：archive 命中但媒体类型不匹配 → 降级 API → 改选动画版 406306
        assert outcome.status == "hit", (
            f"期望 hit，实际 {outcome.status}：{outcome.reason}"
        )
        assert outcome.subject_id == 406306, (
            f"期望 subject_id=406306（动画版），实际 {outcome.subject_id}"
        )
        assert ctx.subject_id == 406306
        # archive 命中时 match_method_detail 保留 ArchiveShortcutStep 设置的 "exact"
        assert ctx.match_method_detail == "exact"


# ======================================================================
# A5-2: 验证 archive 单候选场景 final_match_method_detail 透传
# ======================================================================


class TestVerifyA52ArchiveSingleCandidateMethodDetail:
    """A5-2: archive 命中单候选 → APISearchStep 跳过 bgm_search →
    走候选排序 + post_search 改选 → 命中 → final_match_method_detail 透传

    期望行为：archive 命中的 match_method_detail（如 "exact"）应透传到
    trace.final_match_method_detail。这与 P0-1（API 命中时永远为 ""）不同，
    archive 路径会保留 ArchiveShortcutStep 设置的 match_method_detail。
    """

    def test_verify_archive_single_candidate_method_detail(self) -> None:
        """archive 单候选命中时 final_match_method_detail 应为 "exact" """
        ctx = _build_ctx(
            title="测试番剧",
            season=1,
            media_type="episode",
        )

        # archive 命中单候选（type=2 动画，media_type 匹配 episode，不触发降级）
        archive_candidate = {
            "id": 123,
            "type": 2,  # SUBJECT_TYPE_ANIME → detect = episode
            "name": "测试番剧",
            "name_cn": "测试番剧",
            "platform": "TV",
            "date": "2024-01-15",
        }

        bgm = _make_bgm_mock(
            candidates=[archive_candidate],
            last_hit_source="archive",
            title_diff_ratio=0.95,
        )
        _configure_service_mock(ctx.service, bgm, threshold=0.6)

        # 模拟 ArchiveShortcutStep 已命中设置 ctx
        ctx.bgm_data = [archive_candidate]
        ctx.match_stage = "archive"
        ctx.match_method_detail = "exact"
        ctx.bgm = bgm

        # 通过 MatchPipeline.run 触发 _record_trace，验证 trace 透传
        pipeline = MatchPipeline([APISearchStep()])
        with patch("app.services.sync_service.config_manager") as mock_cfg:
            mock_cfg.get.side_effect = lambda s, k, fallback=None: (
                False if (s, k) == ("sync", "enable_real_action") else fallback
            )
            pipeline.run(ctx)

        # 期望：archive 命中时 final_match_method_detail 透传为 "exact"
        assert ctx.trace.final_match_method_detail == "exact", (
            f"期望 final_match_method_detail='exact'，"
            f"实际 '{ctx.trace.final_match_method_detail}'"
        )
        assert ctx.trace.final_subject_id == 123


# ======================================================================
# A5-3: 验证低置信度场景候选沉淀
# ======================================================================


class TestVerifyA53LowConfidenceSedimentE2e:
    """A5-3: API 搜索返回低相似度候选（score < 阈值）→ 低置信度 →
    候选应沉淀到 trace 供 WebUI 待审

    期望行为：低置信度时 outcome.candidates 非空，trace.steps[-1].candidates 非空。
    若 candidates 为空则 Confirmed（与 P1-3 相关，但 P1-3 聚焦 VariantFallbackSearchStep
    全 miss 清空 bgm_data 的场景；本测试聚焦 APISearchStep 低置信度路径）。
    """

    def test_verify_low_confidence_sediment_e2e(self) -> None:
        """低置信度场景应沉淀候选到 trace"""
        ctx = _build_ctx(
            title="测试番剧",
            season=1,
            media_type="episode",
        )

        candidate = {
            "id": 100,
            "type": 2,
            "name": "测试",
            "name_cn": "测试",
            "platform": "TV",
            "date": "2024-01-15",
        }

        bgm = _make_bgm_mock(
            candidates=[candidate],
            last_hit_source="",  # API 搜索命中
            title_diff_ratio=0.3,  # 低于阈值 0.6
        )
        _configure_service_mock(ctx.service, bgm, threshold=0.6)

        # 通过 MatchPipeline.run 触发完整流程（含 _record_trace）
        pipeline = MatchPipeline([APISearchStep()])
        with patch("app.services.sync_service.config_manager") as mock_cfg:
            mock_cfg.get.side_effect = lambda s, k, fallback=None: (
                False if (s, k) == ("sync", "enable_real_action") else fallback
            )
            pipeline.run(ctx)

        # 期望：低置信度标记，subject_id 不设置
        assert ctx.trace.final_subject_id is None, (
            f"期望 final_subject_id=None，实际 {ctx.trace.final_subject_id}"
        )
        # 期望：trace 中有 low_confidence 记录
        last_step = ctx.trace.steps[-1] if ctx.trace.steps else None
        assert last_step is not None, "trace.steps 为空"
        assert last_step.status == "low_confidence", (
            f"期望最后 step status='low_confidence'，实际 '{last_step.status}'"
        )
        # 期望：候选应沉淀到 trace 供 WebUI 待审
        assert last_step.candidates, "低置信度场景应沉淀候选到 trace.candidates，但为空"
        assert len(last_step.candidates) > 0
        assert last_step.candidates[0].subject_id == "100"


# ======================================================================
# A5-4: 验证跨季查找场景 archive 链不完整
# ======================================================================


class TestVerifyA54CrossSeasonArchiveIncomplete:
    """A5-4: 当前 subject 无目标 sort → archive 续集链不完整（缺中间某季）→
    应降级 API 查找（已修复）

    场景：
    - subject1 (起始) sort 范围 1-50，不含 target_ep=102
    - archive 续集链命中 [subject2]（不完整链，subject2 sort 范围 51-100，不含 102）
    - 真实续集链 [subject2, subject3]（subject3 sort 范围 101-150，含 102）
    - 修复后：archive 链未找到目标时降级到逐 hop API，逐 hop 跳过已检查的
      subject2（visited），继续找到 subject3
    """

    def test_verify_cross_season_archive_incomplete(self) -> None:
        """archive 续集链不完整时应降级到逐跳 API 查找（已修复）

        构造 archive 续集链命中但未找到目标，逐跳 API 能找到目标的场景。
        修复后期望降级到逐跳并返回 (subject3, ep_id)。
        """
        api = BangumiApi()

        # subject1 (起始): sort 1-50
        # subject2 (archive 续集链): sort 51-100
        # subject3 (真实续集链): sort 101-150, 含 target_ep=102
        episodes_data = {
            1: {
                "data": [{"type": 0, "sort": i, "id": 1000 + i} for i in range(1, 51)],
                "total": 50,
            },
            2: {
                "data": [
                    {"type": 0, "sort": i, "id": 2000 + i} for i in range(51, 101)
                ],
                "total": 50,
            },
            3: {
                "data": [
                    {"type": 0, "sort": i, "id": 3000 + i} for i in range(101, 151)
                ],
                "total": 50,
            },
        }

        def get_episodes_side_effect(sid, *args, **kwargs):
            return episodes_data.get(int(sid), {"data": [], "total": 0})

        def get_related_side_effect(sid):
            # 真实续集链：subject1 → subject2 → subject3
            related_map = {
                1: [{"relation": "续集", "id": 2, "type": 2}],
                2: [{"relation": "续集", "id": 3, "type": 2}],
                3: [],
            }
            return related_map.get(int(sid), [])

        def get_subject_side_effect(sid):
            return {
                "id": sid,
                "type": 2,  # SUBJECT_TYPE_ANIME
                "name": f"S{sid}",
                "name_cn": f"第{sid}季",
                "platform": "TV",
            }

        def find_by_sort_side_effect(subject_id, target_sort, _type=0):
            # subject3 含 target_ep=102
            if int(subject_id) == 3 and target_sort == 102:
                return {"id": 3102, "sort": 102}
            return None

        # mock archive：续集链命中但不完整（只有 subject2）
        api._archive = MagicMock()
        api._archive.enabled = True
        api._archive.try_find_sequel_chain.return_value = ShortcutResult(
            True,
            [2],
            "archive_hit",  # 不完整链：只有 subject2，缺 subject3
        )
        # 其他 archive 短路 miss，强制走 API
        miss = ShortcutResult(False, None, "archive_miss")
        api._archive.try_find_related_id_by_relation.return_value = miss
        api._archive.try_get_subject.return_value = miss
        api._archive.try_get_episodes.return_value = miss
        api._archive.try_get_related_subjects.return_value = miss
        api._archive.try_find_prequel_chain.return_value = miss

        with (
            patch.object(api, "get_episodes", side_effect=get_episodes_side_effect),
            patch.object(
                api, "get_related_subjects", side_effect=get_related_side_effect
            ),
            patch.object(api, "get_subject", side_effect=get_subject_side_effect),
            patch.object(
                api,
                "_find_episode_by_sort",
                side_effect=find_by_sort_side_effect,
            ),
            patch.object(
                api, "_fetch_episodes_page", return_value={"data": [], "total": 0}
            ),
        ):
            result = api.find_episode_across_seasons(subject_id=1, target_ep=102)

        # 期望行为：降级到逐跳 API 查找，找到 subject3
        assert result is not None, (
            "archive 续集链不完整时应降级到逐跳 API 查找，但返回 None"
        )
        assert result[0] == 3, f"期望 subject_id=3，实际 {result[0]}"
        assert result[1] == 3102, f"期望 ep_id=3102，实际 {result[1]}"
