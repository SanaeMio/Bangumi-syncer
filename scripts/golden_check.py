"""匹配管线基线检查（手动执行，非 CI 测试）

把原先的两个 pytest 黄金用例测试改成手动脚本：改动匹配管线前后各跑一次，
逐条比对仓库中的基线快照，输出差异与 oracle 命中率。

用法：

    uv run python scripts/golden_check.py            # L1 离线集（无需 archive 数据）
    uv run python scripts/golden_check.py --l2       # 追加 L2 全量管线集（需 archive 库）
    uv run python scripts/golden_check.py --quiet    # 只输出结论与命中率

退出码：0 = 与基线一致；1 = 存在差异或命中率低于护栏（便于接入本地流程）。

基线的语义：**基线的期望值是「生成时的实际行为快照」，不是「应当正确」的断言。**
因此报出差异只说明"行为变了"，不说明"变坏了"——需结合 oracle 命中率判断方向。
确认改动是有意的之后，用 gen_golden_cases.py 重新生成基线：

    uv run python scripts/gen_golden_cases.py --mode l1
    uv run python scripts/gen_golden_cases.py --mode l2
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Windows 控制台默认 GBK，app 启动横幅含 emoji 会抛 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 允许的最大命中率回退：低于此值说明匹配质量出现明显退化
MIN_ORACLE_HIT_RATE = 0.90
GEN_SCRIPT = ROOT / "scripts" / "gen_golden_cases.py"

# L2 比对维度（score 用 4 位小数，避免浮点噪声）
COMPARE_FIELDS = ("subject_id", "match_method", "ambiguous")


def _prepare_config() -> Path:
    """把 CONFIG_FILE 指向基于 config.example.ini 的临时配置

    必须在 import 任何 app 模块**之前**调用：``app.utils.bangumi_data`` 等
    单例在 import 期就读取 CONFIG_FILE 完成构造，之后再改环境变量无效。

    用 example 而非用户的 config.ini，保证检查结果可复现、不受本地开关
    （archive 启用、token、proxy 等）影响；同时避免污染用户配置与 log.txt。
    """
    import configparser
    import shutil
    import tempfile

    tmpdir = Path(tempfile.mkdtemp(prefix="bs_golden_check_"))
    cfg = tmpdir / "config.ini"
    src = ROOT / "config.example.ini"
    shutil.copy2(src if src.exists() else ROOT / "config.ini", cfg)

    cp = configparser.ConfigParser()
    cp.read(cfg, encoding="utf-8")
    for section, key, value in (
        ("bangumi-data", "cache_ttl_days", "36500"),
        ("bangumi-archive", "enabled", "false"),
        ("bangumi-replay", "enabled", "false"),
        ("dev", "log_file", str(tmpdir / "golden_check.log")),
        ("dev", "log_level", "WARNING"),
        ("dev", "debug", "false"),
    ):
        if not cp.has_section(section):
            cp.add_section(section)
        cp.set(section, key, value)
    with open(cfg, "w", encoding="utf-8") as f:
        cp.write(f)

    os.environ["CONFIG_FILE"] = str(cfg)
    return tmpdir


def _answers(case: dict) -> set[str]:
    oracle = case["oracle"]
    answers = {oracle.get("subject_id", "")}
    answers.update(oracle.get("alt_ids") or [])
    answers.discard("")
    return answers


def _fmt_cands(cands: list[dict]) -> str:
    return ", ".join(f"{c['id']}({c['score']})" for c in cands) or "<空>"


def _diff_l1(current: dict, base: dict) -> str:
    """只报有变化的维度，避免输出淹没在无关字段里"""
    parts = []
    if current["subject_id"] != base["subject_id"]:
        parts.append(f"subject_id: {base['subject_id']!r} → {current['subject_id']!r}")
    if current["matched_title"] != base["matched_title"]:
        parts.append(
            f"matched_title: {base['matched_title']!r} → {current['matched_title']!r}"
        )
    if current["date_matched"] != base["date_matched"]:
        parts.append(
            f"date_matched: {base['date_matched']} → {current['date_matched']}"
        )
    if current["candidates"] != base.get("candidates", []):
        parts.append(
            "candidates:\n"
            f"        基线: {_fmt_cands(base.get('candidates', []))}\n"
            f"        实际: {_fmt_cands(current['candidates'])}"
        )
    return "; ".join(parts)


def check_l1(quiet: bool) -> tuple[int, bool]:
    """L1：bangumi-data 单层基线（离线，自带 fixture）"""
    from golden_helpers import (
        L1_CASES_PATH,
        build_offline_bangumi_data,
        load_golden,
        run_case,
    )

    if not L1_CASES_PATH.exists():
        print(f"[L1] 基线不存在：{L1_CASES_PATH}")
        print("     先跑：uv run python scripts/gen_golden_cases.py --mode l1")
        return 0, False

    golden = load_golden(L1_CASES_PATH)
    cases = golden["cases"]
    print(f"[L1] 用例 {len(cases)} 条，fixture 条目 {len(golden['items'])} 条")

    # 结构自检：场景覆盖、字段完整、目标条目在 fixture 内
    ids = set()
    for it in golden["items"]:
        for s in it.get("sites") or []:
            if s.get("site") == "bangumi":
                ids.add(str(s["id"]))
    problems = []
    for c in cases:
        if not c.get("id") or not c.get("scenario"):
            problems.append(f"缺少标识: {c}")
        if not (c["input"].get("title") or c["input"].get("ori_title")):
            problems.append(f"空查询: {c.get('id')}")
        if "baseline" not in c or "oracle" not in c:
            problems.append(f"缺少基线/oracle: {c.get('id')}")
        target = c["oracle"].get("subject_id")
        if target and target not in ids:
            problems.append(f"fixture 缺少目标条目: {c.get('id')}")
    if problems:
        print(f"[L1] 结构校验失败（{len(problems)} 项）：")
        for p in problems[:10]:
            print(f"       {p}")
        return len(problems), True

    client = build_offline_bangumi_data(golden["items"])

    diffs = []
    for c in cases:
        delta = _diff_l1(run_case(client, c), c["baseline"])
        if delta:
            diffs.append(
                f"  {c['id']} [{c['scenario']}]\n"
                f"    查询: {c['input']['title']!r} / {c['input'].get('release_date')!r}\n"
                f"    {delta}"
            )

    positives = [c for c in cases if c["oracle"].get("subject_id")]
    hit = sum(1 for c in positives if run_case(client, c)["subject_id"] in _answers(c))
    rate = hit / len(positives) if positives else 0.0
    print(f"[L1] oracle 命中率 {hit}/{len(positives)} = {rate:.1%}")

    negatives = [c for c in cases if not c["oracle"].get("subject_id")]
    leaked = []
    for c in negatives:
        res = run_case(client, c)
        if res["subject_id"]:
            leaked.append(
                f"  {c['id']} 查询 {c['input']['title']!r} → "
                f"{res['subject_id']} {res['matched_title']!r}"
            )

    failed = False
    if diffs:
        failed = True
        print(
            f"\n[L1] {len(diffs)}/{len(cases)} 条用例的行为与基线不一致。\n"
            "     若这是有意为之的改动，review 后重新生成基线：\n"
            "         uv run python scripts/gen_golden_cases.py --mode l1\n"
        )
        if not quiet:
            print("\n".join(diffs[:20]))
            if len(diffs) > 20:
                print(f"  …（其余 {len(diffs) - 20} 条省略）")
    if rate < MIN_ORACLE_HIT_RATE:
        failed = True
        print(
            f"\n[L1] 命中率 {rate:.1%} 低于护栏 {MIN_ORACLE_HIT_RATE:.0%}"
            " —— 匹配质量出现明显退化"
        )
    if leaked:
        failed = True
        print(f"\n[L1] {len(leaked)}/{len(negatives)} 条负例被误匹配：")
        print("\n".join(leaked[:10]))
    if not failed:
        print("[L1] 与基线一致，负例未被误匹配")
    return len(diffs) + len(leaked), failed


def check_l2(quiet: bool) -> tuple[int, bool]:
    """L2：完整管线基线（需本地 archive 归档库）

    通过子进程复用生成脚本本身，保证「生成时」与「校验时」跑在完全相同的
    配置环境下（否则 config.ini 差异会造出一批假差异）。
    """
    from golden_helpers import L2_CASES_PATH

    if not L2_CASES_PATH.exists():
        print(f"[L2] 基线不存在：{L2_CASES_PATH}")
        print("     先跑：uv run python scripts/gen_golden_cases.py --mode l2")
        return 0, False

    with open(L2_CASES_PATH, encoding="utf-8") as f:
        expected = json.load(f)

    tmp = Path(os.environ.get("TEMP", "/tmp")) / "golden_l2_check.json"
    print(f"[L2] 重跑 {len(expected['cases'])} 条用例（archive 库）…")
    proc = subprocess.run(
        [
            sys.executable,
            str(GEN_SCRIPT),
            "--mode",
            "l2",
            "--n",
            str(expected.get("n", 30)),
            "--out",
            str(tmp),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    if proc.returncode != 0:
        print("[L2] 重跑失败：")
        print(f"      stdout: {proc.stdout[-1500:]}")
        print(f"      stderr: {proc.stderr[-1500:]}")
        return 1, True

    with open(tmp, encoding="utf-8") as f:
        actual = json.load(f)

    old = {c["id"]: c for c in expected["cases"]}
    new = {c["id"]: c for c in actual["cases"]}
    if set(old) != set(new):
        print(
            f"[L2] 用例集发生变化：新增 {sorted(set(new) - set(old))[:5]}，"
            f"缺失 {sorted(set(old) - set(new))[:5]}"
        )
        return 1, True

    diffs = []
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

    print(f"[L2] oracle 命中率 {_rate(old):.1%} → {_rate(new):.1%}")

    if diffs:
        print(
            f"\n[L2] {len(diffs)} 处管线行为与基线不一致。\n"
            "     若这是有意为之的改动，review 后重新生成基线：\n"
            "         uv run python scripts/gen_golden_cases.py --mode l2\n"
        )
        if not quiet:
            print("\n".join(diffs[:30]))
            if len(diffs) > 30:
                print(f"  …（其余 {len(diffs) - 30} 处省略）")
        return len(diffs), True

    print("[L2] 与基线一致")
    return 0, False


def main() -> int:
    ap = argparse.ArgumentParser(description="匹配管线基线检查（手动执行）")
    ap.add_argument(
        "--l2", action="store_true", help="追加 L2 全量管线集（需 archive 库）"
    )
    ap.add_argument("--quiet", action="store_true", help="只输出结论与命中率")
    args = ap.parse_args()

    # 必须在 import app 模块前就绪（见 _prepare_config 说明）
    _prepare_config()

    _, l1_failed = check_l1(args.quiet)
    l2_failed = False
    if args.l2:
        print()
        _, l2_failed = check_l2(args.quiet)

    print()
    if l1_failed or l2_failed:
        print("结论：与基线存在差异，请 review 后再决定是否重新生成基线")
        return 1
    print("结论：与基线一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
