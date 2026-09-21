"""L2 黄金用例集：完整匹配管线的回归基线

与 L1 的区别
------------
L1 只覆盖 bangumi-data 一层；L2 跑的是**完整管线**
（Normalize → CustomMapping → BangumiData → ArchiveShortcut → APISearch），
记录每条用例最终命中哪个 subject、由哪个策略命中、分数与歧义标记。

这才是 P3 裁决层的真正闸门 —— 裁决会改变「哪个策略赢」，只有跑完整管线
才看得出来。

为什么默认不跑
--------------
L2 依赖 Bangumi Archive 归档库（约 300MB，不在仓库中），且要构建 FTS5
索引。CI 没有这些数据，因此默认 skip；需要时显式启用：

    GOLDEN_L2=1 uv run pytest tests/golden -m golden -k l2

环境一致性
----------
管线行为受 config.ini 影响（archive 开关、阈值等）。为避免「生成时用临时
配置、测试时用用户配置」导致的基线误报，测试通过**子进程复用生成脚本本身**，
保证两次跑在完全相同的环境下。

重新生成基线（确认行为变化是有意为之后）：

    uv run python scripts/gen_golden_cases.py --mode l2
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from .helpers import GOLDEN_DIR, L2_CASES_PATH

pytestmark = pytest.mark.golden

L2_ENABLED = os.getenv("GOLDEN_L2") == "1"
ROOT = GOLDEN_DIR.parents[1]
GEN_SCRIPT = ROOT / "scripts" / "gen_golden_cases.py"

# 参与比对的基线维度（score 用 4 位小数，避免浮点噪声）
COMPARE_FIELDS = ("subject_id", "match_method", "ambiguous")


def test_l2_baseline_is_wellformed() -> None:
    """L2 基线的结构校验（不需要 archive 数据，CI 也会跑）"""
    if not L2_CASES_PATH.exists():
        pytest.skip("L2 基线尚未生成（需要本地 archive 数据）")

    with open(L2_CASES_PATH, encoding="utf-8") as f:
        payload = json.load(f)

    assert payload["version"] == 1
    cases = payload["cases"]
    assert len(cases) >= 200, f"用例数不足: {len(cases)}"
    for c in cases:
        assert c["id"].startswith("L2-"), c["id"]
        assert c["oracle"]["subject_id"], f"缺 oracle: {c['id']}"
        assert "baseline" in c, f"缺基线: {c['id']}"


@pytest.mark.skipif(
    not L2_ENABLED, reason="L2 需要本地 archive 数据，设置 GOLDEN_L2=1 启用"
)
def test_l2_pipeline_regression(tmp_path: Path) -> None:
    """完整管线回归：重跑一遍并与仓库中的基线逐条比对"""
    if not L2_CASES_PATH.exists():
        pytest.skip(
            "L2 基线尚未生成，先跑：python scripts/gen_golden_cases.py --mode l2"
        )

    with open(L2_CASES_PATH, encoding="utf-8") as f:
        expected = json.load(f)

    fresh_path = tmp_path / "matching_cases.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(GEN_SCRIPT),
            "--mode",
            "l2",
            "--n",
            str(expected.get("n", 30)),
            "--out",
            str(fresh_path),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert proc.returncode == 0, (
        "重跑 L2 用例失败：\n"
        f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-2000:]}"
    )

    with open(fresh_path, encoding="utf-8") as f:
        actual = json.load(f)

    old = {c["id"]: c for c in expected["cases"]}
    new = {c["id"]: c for c in actual["cases"]}
    assert set(old) == set(new), (
        f"用例集发生变化：新增 {sorted(set(new) - set(old))[:5]}，"
        f"缺失 {sorted(set(old) - set(new))[:5]}"
    )

    diffs: list[str] = []
    for cid, old_case in old.items():
        new_case = new[cid]
        for field in COMPARE_FIELDS:
            if old_case["baseline"].get(field) != new_case["baseline"].get(field):
                diffs.append(
                    f"  {cid} [{old_case['scenario']}] {field}: "
                    f"{old_case['baseline'].get(field)!r} → "
                    f"{new_case['baseline'].get(field)!r}"
                )
        old_score = old_case["baseline"].get("score")
        new_score = new_case["baseline"].get("score")
        if old_score is not None and new_score is not None:
            if round(float(old_score), 4) != round(float(new_score), 4):
                diffs.append(f"  {cid} score: {old_score} → {new_score}")

    def _rate(cases: dict[str, dict]) -> float:
        ok = sum(
            1
            for c in cases.values()
            if c["baseline"]["subject_id"] == c["oracle"]["subject_id"]
        )
        return ok / len(cases)

    assert not diffs, (
        f"{len(diffs)} 处管线行为与基线不一致"
        f"（命中率 {_rate(old):.1%} → {_rate(new):.1%}）。\n"
        "若这是有意为之的改动，请 review 后重新生成基线：\n"
        "    uv run python scripts/gen_golden_cases.py --mode l2\n\n"
        + "\n".join(diffs[:30])
    )
