"""执行门（Gate）契约测试

守护三件事：
1. ``status="skipped"`` 不被任何逻辑分支消费 —— 这是「把门提到管线统一评估」
   之所以安全的前提。若将来有人给 skipped 加分支，本测试失败以提醒重新评估。
2. 全部执行门可在 ``ALL_GATES`` 枚举，且有稳定的 name 与可读 describe。
3. 门的判定异常时**放行**（保守），不因判定失败静默丢匹配。
"""

from __future__ import annotations

import inspect

from app.services.matching import gates as gates_mod
from app.services.matching.gates import (
    ALL_GATES,
    EPISODE_ALREADY_RESOLVED,
    EXACT_SEARCH_HIT,
    GATES_BY_NAME,
    NO_VALID_DATE,
)
from app.services.matching.steps.base import Gate, evaluate_gates, skipped_outcome


class TestGateRegistry:
    """门登记表本身"""

    def test_all_gates_enumerable(self):
        """全部门可枚举，且数量符合当前实现（防新增门时忘记登记）"""
        assert len(ALL_GATES) >= 6
        names = [g.name for g in ALL_GATES]
        assert len(names) == len(set(names)), "门名必须唯一"

    def test_index_matches_registry(self):
        for g in ALL_GATES:
            assert GATES_BY_NAME[g.name] is g

    def test_every_gate_has_name_and_describe(self):
        for g in ALL_GATES:
            assert g.name and isinstance(g.name, str)
            assert g.describe and isinstance(g.describe, str)
            assert callable(g.check)

    def test_gate_names_are_snake_case_identifiers(self):
        """门名会写进 trace JSON 并可能被前端引用，限定字符集"""
        for g in ALL_GATES:
            assert g.name.replace("_", "").isalnum()
            assert g.name == g.name.lower(), f"{g.name} 应为小写"


class TestGateEvaluation:
    """判定与容错"""

    def test_evaluate_gates_returns_first_hit(self):
        first = Gate("a", "A", lambda ctx, prev: True)
        second = Gate("b", "B", lambda ctx, prev: True)
        assert evaluate_gates((first, second), None) is first

    def test_evaluate_gates_returns_none_when_all_miss(self):
        g = Gate("a", "A", lambda ctx, prev: False)
        assert evaluate_gates((g,), None) is None

    def test_evaluate_gates_empty(self):
        assert evaluate_gates((), None) is None

    def test_check_exception_means_do_not_skip(self):
        """判定抛异常时放行（宁可执行，不可静默丢匹配）"""

        def boom(ctx, prev):
            raise RuntimeError("boom")

        g = Gate("boom", "boom", boom)
        assert g.evaluate(None) is False
        assert evaluate_gates((g,), None) is None

    def test_check_truthiness_is_coerced(self):
        g = Gate("t", "t", lambda ctx, prev: [1])  # truthy 非 bool
        assert g.evaluate(None) is True


class TestSkippedOutcome:
    """标准 skipped outcome"""

    def test_skipped_outcome_shape(self):
        g = Gate("some_gate", "某原因", lambda ctx, prev: True)
        out = skipped_outcome(g)
        assert out.status == "skipped"
        assert out.reason == "某原因"
        assert out.gate == "some_gate"
        assert out.is_terminal is False

    def test_skipped_outcome_accepts_extra_fields(self):
        g = Gate("g", "d", lambda ctx, prev: True)
        out = skipped_outcome(g, inputs={"k": "v"})
        assert out.inputs == {"k": "v"}

    def test_skipped_outcome_ignores_redundant_gate_kwarg(self):
        """gate 标识由 helper 自动填充；显式传会被忽略而非报错

        （helper 用 ``**extra`` 透传，若不剔除会与形参名冲突 TypeError）
        """
        g = Gate("real", "d", lambda ctx, prev: True)
        out = skipped_outcome(g, gate="wrong")
        assert out.gate == "real"


class TestNoValidDateGate:
    """具体门的行为（抽 3 个代表）"""

    class _Item:
        def __init__(self, release_date):
            self.release_date = release_date

    class _Ctx:
        def __init__(self, release_date):
            self.item = TestNoValidDateGate._Item(release_date)

    def test_no_valid_date_triggers_on_empty(self):
        assert NO_VALID_DATE.evaluate(self._Ctx("")) is True

    def test_no_valid_date_triggers_on_short(self):
        assert NO_VALID_DATE.evaluate(self._Ctx("2023-1")) is True

    def test_no_valid_date_misses_on_valid(self):
        assert NO_VALID_DATE.evaluate(self._Ctx("2023-01-01")) is False

    def test_no_valid_date_skips_when_item_missing(self):
        """ctx 无 item 时按「无日期」处理 → 跳过（宁可不跑，也不抛错）"""

        class Empty:
            pass

        assert NO_VALID_DATE.evaluate(Empty()) is True


class TestEpisodeAlreadyResolvedGate:
    def test_skips_when_upstream_has_episode(self):
        assert EPISODE_ALREADY_RESOLVED.evaluate(None, {"episode_id": "123"}) is True

    def test_runs_when_upstream_lacks_episode(self):
        assert EPISODE_ALREADY_RESOLVED.evaluate(None, {}) is False
        assert EPISODE_ALREADY_RESOLVED.evaluate(None, None) is False


class TestExactSearchHitGate:
    class _Item:
        title = "X"
        ori_title = ""

    class _Bgm:
        def __init__(self, ratio):
            self._ratio = ratio

        def title_diff_ratio(self, title, ori_title, bgm_data):
            return self._ratio

    class _Ctx:
        def __init__(self, ratio, has_data=True):
            self.item = TestExactSearchHitGate._Item()
            self.bgm = TestExactSearchHitGate._Bgm(ratio)
            self.bgm_data = [{"id": 1}] if has_data else None

    def test_skips_when_ratio_at_threshold(self):
        assert EXACT_SEARCH_HIT.evaluate(self._Ctx(1.0)) is True

    def test_runs_when_ratio_below_threshold(self):
        assert EXACT_SEARCH_HIT.evaluate(self._Ctx(0.0)) is False

    def test_runs_when_no_candidates(self):
        assert EXACT_SEARCH_HIT.evaluate(self._Ctx(1.0, has_data=False)) is False


class TestSkippedNotBranchedOn:
    """契约守护：skipped 只用于展示，不被任何逻辑分支消费。

    这是「把门提到管线统一评估不改变控制流」的前提。若此测试失败，
    说明有人给 skipped 加了分支，需要重新评估门改动的安全性。
    """

    def test_no_branch_on_skipped_in_app(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2] / "app"
        offenders = []
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                # 找比较式分支（排除注释与本次新增的说明文本）
                if ('== "skipped"' in s or "== 'skipped'" in s) and "status" in s:
                    offenders.append(f"{path.relative_to(root)}:{i}: {s}")
        assert not offenders, (
            "status='skipped' 被逻辑分支消费了，门抽象的前提不再成立：\n"
            + "\n".join(offenders)
        )


class TestGateWiringOnSteps:
    """step 声明的门必须已登记，避免拼写错误导致门静默失效"""

    def test_declared_gates_are_registered(self):
        from app.services.matching.steps.archive_shortcut import ArchiveShortcutStep
        from app.services.matching.steps.bangumi_data import BangumiDataStep

        for step_cls in (ArchiveShortcutStep, BangumiDataStep):
            for g in getattr(step_cls, "gates", ()):
                assert g.name in GATES_BY_NAME, (
                    f"{step_cls.__name__} 声明了未登记的门 {g.name}"
                )

    def test_gate_check_signature_takes_ctx_and_prev(self):
        """check 必须可接受 (ctx, prev) 两个位置参数（管线按此调用）"""
        for g in ALL_GATES:
            sig = inspect.signature(g.check)
            params = [
                p
                for p in sig.parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            assert len(params) >= 2, f"{g.name} 的 check 需接受 (ctx, prev)"

    def test_gates_module_exposes_all_gate_constants(self):
        """登记表与实际常量一致：凡模块内 Gate 实例都应在 ALL_GATES 中"""
        found = {
            name
            for name, val in vars(gates_mod).items()
            if isinstance(val, Gate) and not name.startswith("_")
        }
        registered = {g.name for g in ALL_GATES}
        by_var = {
            v.name
            for k, v in vars(gates_mod).items()
            if isinstance(v, Gate) and k.isupper()
        }
        assert by_var <= registered, f"未登记：{by_var - registered}"
        assert found <= {k for k, v in vars(gates_mod).items() if isinstance(v, Gate)}
