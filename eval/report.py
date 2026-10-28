"""Eval 报告生成（markdown，可直接贴 PR）。"""

from __future__ import annotations


def render_report(results: list, *, baseline: dict | None = None) -> str:
    lines = ["# LLM Match Eval（live）", ""]
    hits = sum(1 for r in results if r.get("hit"))
    total = len(results)
    acc = (hits / total * 100) if total else 0.0
    tokens = sum((r.get("outcome") or {}).get("total_tokens") or 0 for r in results)
    lines.append(
        f"- cases: **{total}** | top1 命中: **{hits}/{total}（{acc:.1f}%）** | tokens: {tokens}"
    )
    if baseline:
        base_acc = baseline.get("top1_accuracy")
        if isinstance(base_acc, (int, float)):
            delta = acc / 100.0 - float(base_acc)
            lines.append(f"- vs 基线: {delta:+.1%}")
    lines.append("")
    lines.append("| case | 命中 | run_status | 建议条目 | judge | tokens |")
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        outcome = r.get("outcome") or {}
        judge = r.get("judge") or {}
        parsed = judge.get("parsed") or {}
        judge_cell = (
            f"{parsed.get('score')}（{parsed.get('verdict')}）" if parsed else "—"
        )
        lines.append(
            "| {cid} | {hit} | {status} | {cand} | {judge} | {tokens} |".format(
                cid=r.get("case_id"),
                hit="✅" if r.get("hit") else "❌",
                status=outcome.get("run_status"),
                cand=outcome.get("candidate_subject_id") or "无建议",
                judge=judge_cell,
                tokens=outcome.get("total_tokens"),
            )
        )
    lines.append("")
    issues = []
    for r in results:
        judge = r.get("judge") or {}
        parsed = judge.get("parsed") or {}
        if parsed.get("issues"):
            issues.append(f"- {r.get('case_id')}: {parsed.get('issues')}")
    if issues:
        lines.append("## judge 发现的问题")
        lines.extend(issues)
        lines.append("")
    lines.append(
        "> judge 模型与生成模型异源（生成 DeepSeek / judge Qwen）；"
        "建议结合人工抽检校准 judge 一致性。"
    )
    return "\n".join(lines)
