"""P0 疑似问题验证测试

验证 3 个 P0 疑似问题是否真实存在：
- P0-1: API 命中时 final_match_method_detail 永远为空
- P0-2: _pick_related_subject 死代码 + type 字段语义不一致
- P0-3: get_target_season_episode_id 返回类型不一致

Confirmed 问题用 @pytest.mark.xfail 标记（断言期望行为但当前失败，不破坏 CI）。
Refuted 问题保留为普通测试（断言当前行为且通过）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.models.sync import CustomItem
from app.services.matching.context import MatchContext
from app.services.matching.steps.api_search_main import APISearchStep
from app.services.sync_service.match_trace import MatchTrace
from app.utils.bangumi_api import BangumiApi


def _build_ctx(
    title: str = "测试番剧",
    ori_title: str | None = None,
    season: int = 1,
    media_type: str = "episode",
) -> MatchContext:
    """构建测试用 MatchContext（参考 test_steps.py 的 _build_ctx）"""
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


def _make_bgm_mock(
    candidate: dict,
    last_hit_source: str = "",
    last_match_method: str = "",
) -> MagicMock:
    """构建 bgm MagicMock（参考 test_steps.py 的 mock 模式）"""
    bgm = MagicMock()
    bgm.bgm_search.return_value = [candidate]
    bgm.last_hit_source = last_hit_source
    bgm.last_match_method = last_match_method
    bgm.title_diff_ratio.return_value = 0.95
    return bgm


def _configure_service_mock(service: MagicMock, bgm: MagicMock) -> None:
    """配置 service MagicMock 的匹配辅助方法"""
    service._get_bangumi_api_for_user.return_value = bgm
    service._sort_candidates_by_platform.side_effect = lambda data, **kw: data
    service._get_match_confidence_threshold.return_value = 0.6
    service._check_season_info_in_title.return_value = False
    service._get_explicit_season_from_title.return_value = 0
    service._pick_mainline_episode_candidate.return_value = bgm.bgm_search.return_value[
        0
    ]


# ===== P0-1: API 命中时 final_match_method_detail 永远为空 =====


class TestVerifyP01ApiSearchMatchMethodDetail:
    """P0-1: API 命中时 match_method_detail 应来自 bgm.last_match_method

    疑似问题：APISearchStep.execute API 命中分支（line 254）读取
    ``ctx.matched_variant_method``，但该字段只在 ``bgm_search`` 包装函数
    创建的内部 ctx 中被 VariantFallbackSearchStep 设置。外部主 ctx 的
    ``matched_variant_method`` 始终为 ``""``。只有 ``bgm.last_match_method``
    （通过共享的 bgm 实例）能跨边界传递。
    """

    _CANDIDATE = {
        "id": 12345,
        "name": "测试番剧",
        "name_cn": "测试番剧",
        "platform": "TV",
        "date": "2024-01-15",
    }

    def test_verify_api_search_hit_match_method_detail(self) -> None:
        """API 命中 + 变体方法：match_method_detail 应为 bgm.last_match_method 的值

        场景：bgm_search 内部 VariantFallbackSearchStep 命中变体 "stripped"，
        通过共享 bgm 实例写入 bgm.last_match_method="stripped"。
        外部主 ctx.matched_variant_method 始终为 "" （从未被设置）。
        修复后：APISearchStep 读 bgm.last_match_method，match_method_detail == "stripped"。
        """
        ctx = _build_ctx(title="测试番剧")
        bgm = _make_bgm_mock(
            self._CANDIDATE,
            last_hit_source="",  # API 命中（非 archive）
            last_match_method="stripped",  # VariantFallbackSearchStep 通过共享 bgm 设置
        )
        _configure_service_mock(ctx.service, bgm)

        outcome = APISearchStep().execute(ctx)

        assert outcome.status == "hit"
        # 期望行为：match_method_detail 应反映 bgm.last_match_method
        assert ctx.match_method_detail == "stripped"

    def test_verify_api_search_exact_hit_empty(self) -> None:
        """API 精确命中（无变体）：match_method_detail 应为空字符串

        精确命中时 bgm.last_match_method="" （无变体方法）。
        期望与实际均为 "" （此场景下 bug 被掩盖，行为恰好正确）。
        """
        ctx = _build_ctx(title="测试番剧")
        bgm = _make_bgm_mock(
            self._CANDIDATE,
            last_hit_source="",  # API 命中
            last_match_method="",  # 精确命中，无变体方法
        )
        _configure_service_mock(ctx.service, bgm)

        outcome = APISearchStep().execute(ctx)

        assert outcome.status == "hit"
        # 精确命中应无变体方法（期望行为与实际行为均为 ""）
        assert ctx.match_method_detail == ""


# ===== P0-2: _pick_related_subject 死代码 + type 字段语义不一致 =====


class TestVerifyP02PickRelatedSubject:
    """P0-2: _pick_related_subject 死代码 + type 字段语义不一致

    疑似问题 1 (D-1 死代码): mainline_match（line 533）初始化为 None 后
        从未被赋值。命中 RELATION_ID_PARENT_STORY 时直接 ``return rel``，
        其他情况只更新 other_match。最终 ``return mainline_match or other_match``
        等价于 ``return other_match``。
    疑似问题 2 (type 字段语义): 用 ``rel.get("type")`` 过滤
        SUBJECT_TYPE_ANIME(2)/SUBJECT_TYPE_REAL(6)。但 API 路径 type 是
        subject type，archive 路径 type 是 relation_type（2=prequel/3=sequel）。
    """

    def test_verify_mainline_match_dead_code(self) -> None:
        """D-1: mainline_match 无赋值，命中 parent_story 直接 return

        构造关联条目列表：第一个 non-parent-story（赋给 other_match），
        第二个 parent_story（直接 return）。验证返回 parent_story 条目，
        证明 mainline_match 变量无用（命中 parent_story 时直接 return）。
        """
        related_list = [
            {
                "type": 2,  # SUBJECT_TYPE_ANIME
                "name": "测试番剧",
                "name_cn": "测试番剧",
                "relation": "续集",  # 非 parent_story → 赋给 other_match
            },
            {
                "type": 2,  # SUBJECT_TYPE_ANIME
                "name": "测试番剧",
                "name_cn": "测试番剧",
                "relation": "主线故事",  # parent_story → 直接 return
            },
        ]
        result = APISearchStep._pick_related_subject(
            related_list,
            request_media_type="episode",
            search_title="测试番剧",
        )
        # 命中 parent_story 直接 return，验证 mainline_match 无用
        assert result is not None
        assert result["relation"] == "主线故事"

    @pytest.mark.xfail(
        reason=(
            "P0-2 Confirmed: archive 路径 type=3 是 relation_type（续集），"
            "但代码当作 subject_type 过滤（3 不在 [2,6]），条目被错误跳过返回 None。"
        )
    )
    def test_verify_archive_relation_type_field_semantic(self) -> None:
        """archive 路径 type=3（relation_type=续集）：应返回该条目，实际被过滤返回 None

        archive 关联条目的 type 字段是 relation_type（2=前传/3=续集），
        但代码用 SUBJECT_TYPE_ANIME(2)/SUBJECT_TYPE_REAL(6) 过滤，
        type=3 不在 [2,6] 被跳过。期望返回该条目，实际返回 None。
        """
        related_list = [
            {
                "type": 3,  # archive 路径：relation_type=续集
                "name": "测试番剧",
                "name_cn": "测试番剧",
                "relation": "续集",
            }
        ]
        result = APISearchStep._pick_related_subject(
            related_list,
            request_media_type="episode",
            search_title="测试番剧",
        )
        # 期望行为：type=3 在 archive 路径表示续集，是有效关联，应返回该条目
        assert result is not None
        assert result["relation"] == "续集"

    def test_verify_api_relation_type_field_semantic(self) -> None:
        """API 路径 type=2（subject_type=动画）：正常工作，返回该条目

        API 关联条目的 type 字段是 subject_type（2=动画/6=三次元），
        type=2 在 [2,6] 通过过滤，正常返回该条目。
        """
        related_list = [
            {
                "type": 2,  # API 路径：subject_type=动画
                "name": "测试番剧",
                "name_cn": "测试番剧",
                "relation": "续集",
            }
        ]
        result = APISearchStep._pick_related_subject(
            related_list,
            request_media_type="episode",
            search_title="测试番剧",
        )
        # type=2 在 API 路径是 subject_type=动画，通过过滤，正常返回
        assert result is not None
        assert result["relation"] == "续集"


# ===== P0-3: get_target_season_episode_id 返回类型不一致 =====


class TestVerifyP03GetTargetSeasonEpisodeId:
    """P0-3: get_target_season_episode_id 返回类型不一致

    疑似问题 1: line 555, 568 ``if not target_ep: return subject_id`` 返回
        单个 int，但调用方用 ``bgm_se_id, bgm_ep_id = ...`` 解包，
        当 target_ep 为 0/None 时抛 TypeError。
    疑似问题 2 (D-2): line 542, 252 ``return None, None if target_ep else None``
        条件表达式无论 target_ep 真假都返回 None，是死逻辑。
    疑似问题 3: ``_episode_lookup_failed`` 注解 ``-> int | None``，但实际返回 tuple。
    """

    _MOCK_SUBJECT = {"id": 123, "type": 2, "name": "test", "name_cn": ""}

    @pytest.mark.xfail(
        reason=(
            "P0-3 Confirmed: target_ep=0 时 line 555 `return subject_id` 返回单 int，"
            "调用方 `bgm_se_id, bgm_ep_id = ...` 解包抛 TypeError。"
            "应返回 tuple (subject_id, None) 才能与调用方解包一致。"
        )
    )
    def test_verify_target_ep_zero_unpack(self) -> None:
        """target_ep=0 + is_season_subject_id：返回单值，解包应不抛错

        触发 line 554-555: ``if not target_ep: return subject_id``。
        调用方 ``bgm_se_id, bgm_ep_id = result`` 解包单 int 抛 TypeError。
        期望行为：返回 tuple，解包不抛错。
        """
        api = BangumiApi()
        with (
            patch.object(api, "_get_episode_sync_limits", return_value=(100, 9999)),
            patch.object(api, "get_subject", return_value=self._MOCK_SUBJECT),
        ):
            # target_ep=0 → if not target_ep: return subject_id（单值）
            result = api.get_target_season_episode_id(
                123, 1, 0, is_season_subject_id=True
            )
            # 期望行为：返回 tuple，解包不抛错
            bgm_se_id, bgm_ep_id = result  # 实际抛 TypeError
            assert bgm_se_id == 123

    def test_verify_none_if_target_ep_else_none_dead_logic(self) -> None:
        """D-2: line 542 ``None, None if target_ep else None`` 死逻辑

        无论 target_ep 真假，条件表达式两分支都返回 None，
        最终返回 (None, None)。验证 target_season > max_season 时
        target_ep=0 和 target_ep=5 返回值相同。
        """
        api = BangumiApi()
        # target_season=101 > max_season=100，target_ep=0（falsy）
        with patch.object(api, "_get_episode_sync_limits", return_value=(100, 9999)):
            result_falsy = api.get_target_season_episode_id(123, 101, 0)
        # target_season=101 > max_season=100，target_ep=5（truthy）
        with patch.object(api, "_get_episode_sync_limits", return_value=(100, 9999)):
            result_truthy = api.get_target_season_episode_id(123, 101, 5)
        # 死逻辑：两个分支都返回 (None, None)
        assert result_falsy == (None, None)
        assert result_truthy == (None, None)

    def test_verify_episode_lookup_failed_return_type(self) -> None:
        """P0-3: _episode_lookup_failed 注解 -> int | None，实际返回 tuple

        mock _resolve_episode_by_airdate_in_subject 返回 tuple，
        _episode_lookup_failed 直接返回该 tuple，与注解 int|None 不一致。
        """
        api = BangumiApi()
        with patch.object(
            api,
            "_resolve_episode_by_airdate_in_subject",
            return_value=(123, 456),
        ):
            result = api._episode_lookup_failed(
                subject_id=123,
                target_ep=5,
                release_date="2024-01-15",
            )
        # 注解声明 -> int | None，实际返回 tuple（与注解不一致）
        assert isinstance(result, tuple)
        assert result == (123, 456)

    def test_verify_episode_lookup_failed_dead_ternary(self) -> None:
        """D-2: line 252 _episode_lookup_failed 末行死逻辑

        release_date=None + target_season=1 → 跳过两个 if 块 → 末行
        ``return None, None if target_ep else None``。
        target_ep=5（truthy）和 target_ep=0（falsy）都返回 (None, None)。
        """
        api = BangumiApi()
        # release_date=None → 跳过第一个 if 块
        # target_season=1（默认）→ 跳过第二个 if 块
        # 到达末行 return None, None if target_ep else None
        result_truthy = api._episode_lookup_failed(
            subject_id=123, target_ep=5, release_date=None
        )
        result_falsy = api._episode_lookup_failed(
            subject_id=123, target_ep=0, release_date=None
        )
        # 死逻辑：两个分支都返回 (None, None)
        assert result_truthy == (None, None)
        assert result_falsy == (None, None)
