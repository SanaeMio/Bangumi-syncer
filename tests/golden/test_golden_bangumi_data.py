"""L1 黄金用例集：bangumi-data 层的行为快照回归（CI 必跑）

设计要点
--------
1. **完全离线**：数据来自仓库内的 ``bangumi_data_cases.json``（精简后的
   bangumi-data 子集），不联网、不读 707MB 的 archive 归档库，
   因此 CI 一定跑得起来。
2. **断言的是基线快照，不是「应当正确」**：任何行为变化（变好或变坏）
   都会让测试失败，强制人工 review 后再由
   ``scripts/gen_golden_cases.py`` 重新生成基线。这是 P2/P3 改造的闸门。
3. **每条用例另带 oracle**（构造用例时的真实答案），用于输出命中率，
   判断一次改动到底是变好还是变坏。

重新生成基线（确认行为变化是有意为之后）：

    uv run python scripts/gen_golden_cases.py --mode l1
"""

from __future__ import annotations

import pytest

from .helpers import (
    L1_CASES_PATH,
    build_offline_bangumi_data,
    load_golden,
    run_case,
)

pytestmark = pytest.mark.golden

# 允许的最大命中率回退：低于此值说明匹配质量出现明显退化
MIN_ORACLE_HIT_RATE = 0.90


@pytest.fixture(scope="module")
def golden() -> dict:
    return load_golden(L1_CASES_PATH)


@pytest.fixture(scope="module")
def client(golden: dict):
    return build_offline_bangumi_data(golden["items"])


def _answers(case: dict) -> set[str]:
    oracle = case["oracle"]
    answers = {oracle.get("subject_id", "")}
    answers.update(oracle.get("alt_ids") or [])
    answers.discard("")
    return answers


def test_golden_file_is_wellformed(golden: dict) -> None:
    """用例集本身的结构校验（场景覆盖、字段完整）"""
    assert golden["version"] == 1
    cases = golden["cases"]
    assert len(cases) >= 300, f"用例数不足: {len(cases)}"
    assert golden["items"], "fixture 条目为空"

    for c in cases:
        assert c["id"] and c["scenario"], f"缺少标识: {c}"
        assert c["input"]["title"] or c["input"].get("ori_title"), f"空查询: {c['id']}"
        assert "baseline" in c and "oracle" in c, f"缺少基线/oracle: {c['id']}"


def test_fixture_contains_oracle_targets(golden: dict) -> None:
    """正例的目标条目必须在 fixture 中，否则用例测的是空气"""
    ids = {str(i) for i in (_extract_id(it) for it in golden["items"])}
    missing = []
    for c in golden["cases"]:
        target = c["oracle"].get("subject_id")
        if target and target not in ids:
            missing.append(c["id"])
    assert not missing, f"fixture 缺少目标条目: {missing[:5]}（共 {len(missing)}）"


def _extract_id(item: dict) -> str:
    for s in item.get("sites") or []:
        if s.get("site") == "bangumi":
            return str(s["id"])
    return ""


def _diff(current: dict, base: dict) -> str:
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


def _fmt_cands(cands: list[dict]) -> str:
    return ", ".join(f"{c['id']}({c['score']})" for c in cands) or "<空>"


def test_baseline_regression(client, golden: dict) -> None:
    """核心闸门：命中结果**与候选列表**都必须与基线一致

    候选列表参与比对是刻意加严：只比对最终 subject_id 时，打分/排序的
    改动往往仍命中同一条目，闸门会漏掉这类变化。
    """
    diffs: list[str] = []
    for c in golden["cases"]:
        current = run_case(client, c)
        delta = _diff(current, c["baseline"])
        if delta:
            diffs.append(
                f"  {c['id']} [{c['scenario']}]\n"
                f"    查询: {c['input']['title']!r} / {c['input'].get('release_date')!r}\n"
                f"    {delta}"
            )

    assert not diffs, (
        f"{len(diffs)}/{len(golden['cases'])} 条用例的行为与基线不一致。\n"
        "若这是有意为之的改动，请 review 下列差异后重新生成基线：\n"
        "    uv run python scripts/gen_golden_cases.py --mode l1\n\n"
        + "\n".join(diffs[:20])
    )


def test_oracle_hit_rate(client, golden: dict) -> None:
    """命中率护栏：低于阈值说明匹配质量退化（而非单纯的行为变化）"""
    cases = [c for c in golden["cases"] if c["oracle"].get("subject_id")]
    hit = sum(1 for c in cases if run_case(client, c)["subject_id"] in _answers(c))
    rate = hit / len(cases) if cases else 0.0
    assert rate >= MIN_ORACLE_HIT_RATE, (
        f"oracle 命中率 {hit}/{len(cases)} = {rate:.1%} "
        f"< 阈值 {MIN_ORACLE_HIT_RATE:.0%}"
    )


def test_negative_cases_never_match(client, golden: dict) -> None:
    """负例不得被匹配到任何条目（防阈值放宽导致凭空匹配）

    这类回归在正例集上永远看不出来 —— 正例命中率甚至可能不降反升。
    """
    negatives = [c for c in golden["cases"] if not c["oracle"].get("subject_id")]
    assert negatives, "用例集中没有负例"
    leaked = []
    for c in negatives:
        res = run_case(client, c)
        sid, matched = res["subject_id"], res["matched_title"]
        if sid:
            leaked.append(
                f"  {c['id']} 查询 {c['input']['title']!r} → {sid} {matched!r}"
            )
    assert not leaked, f"{len(leaked)}/{len(negatives)} 条负例被误匹配：\n" + "\n".join(
        leaked[:10]
    )
