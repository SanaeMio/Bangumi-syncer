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
    PENALTY_EPISODE_SHORT,
    apply_episode_count_weight,
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
        out, notes = APISearchStep._apply_episode_weight(
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
        out, notes = APISearchStep._apply_episode_weight(
            [_cand(1), _cand(2)], item, bgm
        )
        assert [c["id"] for c in out] == [2, 1]

    def test_no_episode_skips_probe(self):
        from app.services.matching.steps.api_search_main import APISearchStep

        bgm = MagicMock()
        item = MagicMock(episode=None)
        out, notes = APISearchStep._apply_episode_weight([_cand(1)], item, bgm)
        assert [c["id"] for c in out] == [1]
        assert notes == []
        bgm.get_episodes.assert_not_called()

    def test_no_bgm_skips_probe(self):
        from app.services.matching.steps.api_search_main import APISearchStep

        item = MagicMock(episode=12)
        out, notes = APISearchStep._apply_episode_weight([_cand(1)], item, None)
        assert [c["id"] for c in out] == [1]
        assert notes == []
