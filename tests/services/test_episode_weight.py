"""集数一致性加权（[matching] episode_count_weight）单元测试

覆盖三类关注点：
1. 开关关闭时**完全不参与**（不查章节数，不改变顺序）；
2. 开启时只做微调：装得下的靠前、明显不够的靠后、无法确定的不加不减；
3. 异常与脏数据不得让匹配失败。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.core.config_schema import SECTIONS
from app.services.matching.episode_weight import (
    BONUS_EPISODE_FIT,
    BONUS_SEASON_MATCH,
    PENALTY_EPISODE_SHORT,
    PENALTY_SEASON_MISMATCH,
    apply_episode_count_weight,
    apply_season_episode_weight,
    apply_season_number_weight,
)


def _cand(sid: int, name: str = "") -> dict:
    return {"id": sid, "name": name or f"subject-{sid}"}


class TestApplyEpisodeCountWeight:
    def test_disabled_returns_input_unchanged(self):
        """target_ep 为空时原样返回（等价于关闭后的行为）"""
        cands = [_cand(1), _cand(2)]
        fetch = MagicMock()
        out, notes = apply_episode_count_weight(cands, None, fetch)
        assert out is cands
        assert notes == []
        fetch.assert_not_called()

    def test_fit_candidate_moves_ahead(self):
        """章节够的候选排到前面"""
        cands = [_cand(1), _cand(2)]
        # 1 号只有 5 集（请求 12 集），2 号有 12 集
        out, notes = apply_episode_count_weight(
            cands, 12, lambda sid: {1: 5, 2: 12}[sid]
        )
        assert [c["id"] for c in out] == [2, 1]
        assert any("集数加 2" in n for n in notes)
        assert any("集数减 1" in n for n in notes)

    def test_unknown_count_is_neutral(self):
        """取不到章节数时既不加也不减，保持原顺序"""
        cands = [_cand(1), _cand(2)]
        out, notes = apply_episode_count_weight(cands, 12, lambda sid: None)
        assert [c["id"] for c in out] == [1, 2]
        assert notes == []

    def test_equal_count_is_fit(self):
        """章节数恰好等于集数视为刚好够（Bangumi 常见正片少一集）"""
        out, notes = apply_episode_count_weight([_cand(1), _cand(2)], 12, lambda s: 12)
        # 两条都是加分，顺序不变
        assert [c["id"] for c in out] == [1, 2]
        assert len(notes) == 2

    def test_fetch_exception_is_survivable(self):
        """查询抛异常不影响匹配，该候选按无法确定处理"""

        def _boom(_sid):
            raise RuntimeError("api down")

        out, notes = apply_episode_count_weight([_cand(1), _cand(2)], 12, _boom)
        assert [c["id"] for c in out] == [1, 2]
        assert notes == []

    def test_zero_and_negative_count_are_neutral(self):
        """0 / 负数章节数是脏数据，不做判断"""
        for bad in (0, -3):
            out, notes = apply_episode_count_weight(
                [_cand(1), _cand(2)], 12, lambda s, b=bad: b
            )
            assert [c["id"] for c in out] == [1, 2]
            assert notes == []

    def test_missing_id_is_neutral(self):
        """候选没有 id 时无法查询，按中性处理（不加分，故排在加分候选之后）"""
        cands = [{"name": "no-id"}, _cand(2)]
        out, _ = apply_episode_count_weight(cands, 12, lambda s: 12)
        assert [c["name"] for c in out] == ["subject-2", "no-id"]

    def test_limit_only_probes_first_n(self):
        """limit 只限制查询数量，返回列表仍是完整的"""
        cands = [_cand(i) for i in range(1, 8)]
        seen: list[int] = []

        def _count(sid):
            seen.append(sid)
            return 20

        out, _ = apply_episode_count_weight(cands, 12, _count, limit=5)
        assert len(seen) == 5
        assert len(out) == 7

    def test_stable_order_for_equal_weight(self):
        """同权重的候选保持原有相对顺序（platform 已排过一次）"""
        cands = [_cand(1), _cand(2), _cand(3)]
        out, _ = apply_episode_count_weight(cands, 12, lambda s: 20)
        assert [c["id"] for c in out] == [1, 2, 3]

    def test_scores_are_small_enough_to_only_break_ties(self):
        """加减分幅度必须很小，不能压过标题相似度差异"""
        assert 0 < BONUS_EPISODE_FIT <= 0.05
        assert 0 < PENALTY_EPISODE_SHORT <= 0.05


class TestEpisodeWeightConfig:
    def test_config_schema_registers_key(self):
        """配置项必须登记在 config_schema（否则配置页无入口）"""
        names = {f.name for f in SECTIONS["matching"].fields}
        assert "episode_count_weight" in names

    def test_default_is_off(self):
        """默认关闭：未设置视为关"""
        from app.services.matching.steps.api_search_main import APISearchStep

        with patch("app.services.sync_service.config_manager") as cfg:
            cfg.get.return_value = False
            assert APISearchStep._episode_weight_enabled() is False

    def test_enabled_when_config_true(self):
        from app.services.matching.steps.api_search_main import APISearchStep

        with patch("app.services.sync_service.config_manager") as cfg:
            cfg.get.side_effect = lambda section, key, fallback=None: (
                True if key == "episode_count_weight" else fallback
            )
            assert APISearchStep._episode_weight_enabled() is True

    def test_config_read_failure_falls_back_to_off(self):
        """配置读取异常按关闭处理，不让匹配失败"""
        from app.services.matching.steps.api_search_main import APISearchStep

        with patch("app.services.sync_service.config_manager") as cfg:
            cfg.get.side_effect = RuntimeError("boom")
            assert APISearchStep._episode_weight_enabled() is False


class TestApplyEpisodeWeightIntegration:
    """_apply_episode_weight：从 bgm 取章节数并只数本篇章节"""

    def test_counts_only_normal_episodes(self):
        from app.services.matching.steps.api_search_main import APISearchStep

        bgm = MagicMock()
        bgm.get_episodes.side_effect = lambda sid: {
            "data": (
                # 12 集正片 + 2 集 SP（type=1 不该计入）
                [{"id": i, "type": 0} for i in range(1, 13)]
                + [{"id": 100 + i, "type": 1} for i in range(2)]
            )
        }
        item = MagicMock(episode=12)
        out, notes, season_notes = APISearchStep._apply_episode_weight(
            [_cand(1), _cand(2)], item, bgm
        )
        # 两个候选都装得下 12 集 → 都加分，顺序不变
        assert [c["id"] for c in out] == [1, 2]
        assert len(notes) == 2

    def test_short_season_is_penalized(self):
        from app.services.matching.steps.api_search_main import APISearchStep

        bgm = MagicMock()
        bgm.get_episodes.side_effect = lambda sid: {
            "data": [{"id": i, "type": 0} for i in range(1, (13 if sid == 2 else 4))]
        }
        item = MagicMock(episode=12)
        out, notes, season_notes = APISearchStep._apply_episode_weight(
            [_cand(1), _cand(2)], item, bgm
        )
        assert [c["id"] for c in out] == [2, 1]

    def test_no_episode_skips_probe(self):
        from app.services.matching.steps.api_search_main import APISearchStep

        bgm = MagicMock()
        item = MagicMock(episode=None)
        out, notes, season_notes = APISearchStep._apply_episode_weight(
            [_cand(1)], item, bgm
        )
        assert [c["id"] for c in out] == [1]
        assert notes == []
        bgm.get_episodes.assert_not_called()

    def test_no_bgm_skips_probe(self):
        from app.services.matching.steps.api_search_main import APISearchStep

        item = MagicMock(episode=12)
        out, notes, season_notes = APISearchStep._apply_episode_weight(
            [_cand(1)], item, None
        )
        assert [c["id"] for c in out] == [1]
        assert notes == []


def _scand(sid: int, name: str = "", name_cn: str = "") -> dict:
    d = {"id": sid, "name": name or f"subject-{sid}"}
    if name_cn:
        d["name_cn"] = name_cn
    return d


class TestApplySeasonNumberWeight:
    """apply_season_number_weight：候选标题明确声明季号时才加减分"""

    def test_invalid_season_returns_unchanged(self):
        """季号无效（None / 超出合理范围）时原样返回"""
        cands = [_cand(1), _cand(2)]
        out, notes = apply_season_number_weight(cands, None)
        assert out is cands
        assert notes == []
        for bad in (0, -1, 100):
            out, notes = apply_season_number_weight(cands, bad)
            assert out is cands
            assert notes == []

    def test_declared_match_gets_bonus(self):
        """声明第2期的候选在目标季 2 时排到未声明候选前面"""
        cands = [_scand(1, name="作品"), _scand(2, name="作品(第2期)")]
        out, notes = apply_season_number_weight(cands, 2)
        assert [c["id"] for c in out] == [2, 1]
        assert any("季号加 2" in n for n in notes)
        # 未声明的候选不动，不产生说明
        assert len(notes) == 1

    def test_declared_mismatch_gets_penalty(self):
        """明确声明其他季号的候选被减分排后"""
        cands = [_scand(1, name="作品 第二季"), _scand(2, name="作品(第3期)")]
        out, notes = apply_season_number_weight(cands, 2)
        assert [c["id"] for c in out] == [1, 2]
        assert any("季号减 2" in n for n in notes)

    def test_undeclared_is_neutral(self):
        """标题未声明季号：不加不减（不假设「无季号=第一季」）"""
        cands = [_scand(1, name="作品"), _scand(2, name="日常")]
        out, notes = apply_season_number_weight(cands, 2)
        assert [c["id"] for c in out] == [1, 2]
        assert notes == []

    def test_name_cn_declaration_counts(self):
        """name_cn 里的季号声明同样参与"""
        cands = [_scand(1, name="Opposite you and I", name_cn="相反的你和我 第二季")]
        out, notes = apply_season_number_weight(cands, 2)
        assert [c["id"] for c in out] == [1]
        assert any("季号加 1" in n for n in notes)

    def test_trailing_digit_declaration(self):
        """结尾贴号季号（笨蛋，测验，召唤兽2）参与判定"""
        cands = [
            _scand(1, name="バカとテストと召喚獣2"),
            _scand(2, name="バカとテストと召喚獣"),
        ]
        out, notes = apply_season_number_weight(cands, 2)
        assert [c["id"] for c in out] == [1, 2]
        assert any("季号加 1" in n for n in notes)

    def test_dirty_candidates_are_neutral(self):
        """非 dict / 空标题的候选按中性处理，不抛异常"""
        cands = ["not-a-dict", {"id": 2}, {"id": 3, "name": ""}]
        out, notes = apply_season_number_weight(cands, 2)
        assert out == cands
        assert notes == []

    def test_limit(self):
        """limit 只限制参与计算的候选数量，返回完整列表"""
        cands = [_scand(i, name="作品(第2期)") for i in range(1, 8)]
        out, notes = apply_season_number_weight(cands, 2, limit=5)
        assert len(out) == 7
        assert len(notes) == 5

    def test_stable_order_for_equal_weight(self):
        """同权重的候选保持原有相对顺序"""
        cands = [_scand(1, name="A(第2期)"), _scand(2, name="B(第2期)")]
        out, _ = apply_season_number_weight(cands, 2)
        assert [c["id"] for c in out] == [1, 2]

    def test_season_one_target(self):
        """目标季 1：声明第1期的加分、声明第二季的减分、未声明不动"""
        cands = [
            _scand(1, name="作品"),
            _scand(2, name="作品(第1期)"),
            _scand(3, name="作品(第2期)"),
        ]
        out, notes = apply_season_number_weight(cands, 1)
        assert [c["id"] for c in out] == [2, 1, 3]
        assert any("季号加 2" in n for n in notes)
        assert any("季号减 3" in n for n in notes)

    def test_scores_are_small_enough_to_only_break_ties(self):
        assert 0 < BONUS_SEASON_MATCH <= 0.05
        assert 0 < PENALTY_SEASON_MISMATCH <= 0.05


class TestApplySeasonEpisodeWeight:
    """apply_season_episode_weight：季号 + 集数求和后单次稳定排序"""

    def test_both_targets_missing_returns_unchanged(self):
        cands = [_cand(1)]
        out, ep, season = apply_season_episode_weight(cands)
        assert out is cands
        assert ep == [] and season == []

    def test_conflicting_signals_cancel_out(self):
        """集数够(+0.02)但季号声明不符(-0.02)求和为 0，与中性候选平分保序"""
        cands = [_scand(1, name="作品(第3期)"), _scand(2, name="日常")]
        out, ep, season = apply_season_episode_weight(
            cands,
            target_season=2,
            target_ep=12,
            # 1 号章节刚好够（+0.02）、2 号查不到（中性）
            fetch_episode_count=lambda s: 12 if s == 1 else None,
        )
        # 1 号：集数 +0.02、季号 -0.02 → 总分 0；2 号全中性 0 → 平分保持原序
        assert [c["id"] for c in out] == [1, 2]
        assert ep and season

    def test_agreeing_signals_rank_first(self):
        """季号声明一致且章节装得下 → 总分领先未声明候选"""
        cands = [_cand(1), _scand(2, name="作品(第2期)")]
        out, ep, season = apply_season_episode_weight(
            cands,
            target_season=2,
            target_ep=12,
            fetch_episode_count=lambda s: {1: 12, 2: 12}[s],
        )
        assert [c["id"] for c in out] == [2, 1]
        assert len(ep) == 2
        assert len(season) == 1

    def test_season_only_without_episode(self):
        """只有季号没有集数时季号单独起作用，不触发章节数查询"""
        cands = [_cand(1), _scand(2, name="作品(第2期)")]
        fetch = MagicMock()
        out, ep, season = apply_season_episode_weight(
            cands, target_season=2, fetch_episode_count=fetch
        )
        assert [c["id"] for c in out] == [2, 1]
        fetch.assert_not_called()
        assert ep == [] and season

    def test_episode_only_matches_legacy_behavior(self):
        """只给集数时与原 apply_episode_count_weight 的结果完全一致"""
        cands = [_cand(1), _cand(2)]
        fetch = lambda s: {1: 5, 2: 12}[s]  # noqa: E731
        expected, expected_notes = apply_episode_count_weight(cands, 12, fetch)
        out, ep, season = apply_season_episode_weight(
            cands, target_ep=12, fetch_episode_count=fetch
        )
        assert [c["id"] for c in out] == [c["id"] for c in expected] == [2, 1]
        assert ep == expected_notes
        assert season == []


class TestApplyEpisodeWeightSeasonIntegration:
    """_apply_episode_weight：季号与集数一同参与，返回 3 元组"""

    def test_season_only_item_still_weighted(self):
        """episode 为空但季号有效时，季号单独加权且不查章节数"""
        from app.services.matching.steps.api_search_main import APISearchStep

        bgm = MagicMock()
        item = MagicMock(episode=None, season=2)
        out, ep_notes, season_notes = APISearchStep._apply_episode_weight(
            [_cand(1), _scand(2, name="作品(第2期)")], item, bgm
        )
        assert [c["id"] for c in out] == [2, 1]
        assert ep_notes == []
        assert season_notes
        bgm.get_episodes.assert_not_called()

    def test_garbage_season_type_is_ignored(self):
        """季号为非 int / 非纯数字字符串时不参与加权（S50 这类解析垃圾）"""
        from app.services.matching.steps.api_search_main import APISearchStep

        item = MagicMock(episode=None, season="S50")
        out, ep_notes, season_notes = APISearchStep._apply_episode_weight(
            [_cand(1)], item, MagicMock()
        )
        assert out == [_cand(1)]
        assert ep_notes == [] and season_notes == []

    def test_both_signals_together(self):
        """季号与集数同时有效：两项说明分别回传"""
        from app.services.matching.steps.api_search_main import APISearchStep

        bgm = MagicMock()
        bgm.get_episodes.side_effect = lambda sid: {
            "data": [{"id": i, "type": 0} for i in range(1, (13 if sid == 2 else 4))]
        }
        item = MagicMock(episode=12, season=2)
        out, ep_notes, season_notes = APISearchStep._apply_episode_weight(
            [_cand(1), _scand(2, name="作品(第2期)")], item, bgm
        )
        assert [c["id"] for c in out] == [2, 1]
        assert ep_notes and season_notes
