"""裁决层 A/B 评估（P3）

在真实 Archive 数据上，用同一批 L2 用例分别跑「裁决关闭」与「裁决开启」，
对比自动采用率 / 精确率 / 召回率，量化裁决层的实际收益。

用法：

    uv run python scripts/eval_arbiter.py [每场景用例数]

两次运行都走子进程 + 临时配置（与 L2 黄金集相同的环境），
因此差异只来自 ``[matching] arbiter_enabled`` 这一个开关。

注意：L2 依赖约 300MB 的 Archive 归档库（不在仓库中），需本地已导入数据。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "scripts" / "gen_golden_cases.py"


def run(arbiter: str, out: Path, n: int) -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            str(GEN),
            "--mode",
            "l2",
            "--n",
            str(n),
            "--arbiter",
            arbiter,
            "--out",
            str(out),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"生成失败（arbiter={arbiter}）：\n"
            f"stdout: {proc.stdout[-1500:]}\nstderr: {proc.stderr[-1500:]}"
        )
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def stats(data: dict) -> dict:
    """baseline.subject_id 非空 = 自动采用；为空 = 沉淀待审 / 未命中"""
    cases = data["cases"]
    adopted = [c for c in cases if c["baseline"]["subject_id"]]
    correct = [
        c for c in adopted if c["baseline"]["subject_id"] == c["oracle"]["subject_id"]
    ]
    wrong = [c for c in adopted if c not in correct]
    total = len(cases) or 1
    return {
        "total": len(cases),
        "adopted": len(adopted),
        "correct": len(correct),
        "wrong": len(wrong),
        "review": len(cases) - len(adopted),
        "coverage": len(adopted) / total,
        "precision": len(correct) / len(adopted) if adopted else 0.0,
        "recall": len(correct) / total,
    }


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    # 结果落到临时目录，避免污染工作区；结束时打印路径便于复核
    tmp = Path(tempfile.mkdtemp(prefix="arbiter_ab_"))

    print(f"用例规模：每场景 {n} 条（两次运行共用同一批）")
    print("跑「裁决关闭」…", flush=True)
    off = run("off", tmp / "off.json", n)
    print("跑「裁决开启」…", flush=True)
    on = run("on", tmp / "on.json", n)

    a, b = stats(off), stats(on)

    print()
    print(f"{'指标':<14}{'关闭(现状)':>14}{'开启(裁决)':>14}{'变化':>14}")
    print("-" * 56)

    def row(label: str, key: str, pct: bool = True) -> None:
        va, vb = a[key], b[key]
        if pct:
            print(f"{label:<14}{va:>13.1%}{vb:>14.1%}{(vb - va):>+14.1%}")
        else:
            print(f"{label:<14}{va:>14}{vb:>14}{(vb - va):>+14}")

    row("自动采用数", "adopted", pct=False)
    row("覆盖(采用率)", "coverage")
    row("精确率", "precision")
    row("召回率", "recall")
    row("错命中数", "wrong", pct=False)
    row("沉淀待审数", "review", pct=False)

    # 逐条差异：只看「采用了哪个条目」，忽略 score 精度
    # （关闭时 final_score 是完整浮点，开启时裁决层 round 到 4 位，
    #   直接比对整个 baseline 会把几十条纯精度差异算成行为变化）
    base = {c["id"]: c["baseline"] for c in off["cases"]}
    new = {c["id"]: c["baseline"] for c in on["cases"]}
    changed = [
        k for k in base if base[k]["subject_id"] != new.get(k, {}).get("subject_id")
    ]
    score_only = [k for k in base if base[k] != new.get(k) and k not in changed]
    print(f"\n（另有 {len(score_only)} 条仅分数精度不同，不计入行为变化）")

    gained = [k for k in changed if not base[k]["subject_id"] and new[k]["subject_id"]]
    lost = [k for k in changed if base[k]["subject_id"] and not new[k]["subject_id"]]
    swapped = [
        k
        for k in changed
        if base[k]["subject_id"]
        and new[k]["subject_id"]
        and base[k]["subject_id"] != new[k]["subject_id"]
    ]

    print()
    print(f"行为变化的用例：{len(changed)}/{len(base)}")
    print(f"  现状沉淀 → 裁决采用：{len(gained)}")
    print(f"  现状采用 → 裁决沉淀：{len(lost)}")
    print(f"  采用条目被改判：    {len(swapped)}")

    # 净收益：从「错命中」变「沉淀」，或「沉淀」变「正确命中」
    def is_correct(bl: dict, oracle: dict) -> bool:
        return bool(bl["subject_id"]) and bl["subject_id"] == oracle["subject_id"]

    oracles = {c["id"]: c["oracle"] for c in off["cases"]}
    better = [
        k
        for k in changed
        if not is_correct(base[k], oracles[k])
        and (is_correct(new[k], oracles[k]) or not new[k]["subject_id"])
    ]
    worse = [
        k
        for k in changed
        if is_correct(base[k], oracles[k]) and not is_correct(new[k], oracles[k])
    ]
    print(f"  其中方向变好（少标错/多命中）：{len(better)}")
    print(f"  其中方向变差（少命中/多标错）：{len(worse)}")

    if changed:
        off_by_id = {c["id"]: c for c in off["cases"]}
        print("\n变化明细（前 8 条）：")
        for k in changed[:8]:
            print(
                f"  {k}\n"
                f"    查询 {off_by_id[k]['input']['title']!r}\n"
                f"    关闭 → {base[k]['subject_id'] or '(沉淀)':<12} "
                f"score={base[k]['score']}\n"
                f"    开启 → {new[k]['subject_id'] or '(沉淀)':<12} "
                f"score={new[k]['score']}  {new[k]['failure'][:60]}"
            )

    print(f"\n原始数据：{tmp / 'off.json'} / {tmp / 'on.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
