"""Eval CLI：record / replay / live。

用法（在仓库根目录）：

    # 录制（本地，需要 EVAL_LLM_API_KEY；CI 环境被硬拒绝）
    EVAL_LLM_API_KEY=... uv run python eval/run_eval.py --mode record --all

    # 回放（离线、零成本；CI 用）
    uv run python eval/run_eval.py --mode replay --all

    # 真实模型 eval + judge（本地/手动）
    EVAL_LLM_API_KEY=... uv run python eval/run_eval.py --mode live --all --judge

环境变量：

- ``EVAL_LLM_API_KEY`` / ``EVAL_LLM_BASE`` / ``EVAL_LLM_MODEL`` / ``EVAL_LLM_PROVIDER``
  （默认 DeepSeek 的 Anthropic 兼容端点）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.lib import (  # noqa: E402
    FingerprintMismatch,
    llm_cfg_from_env,
    load_golden,
    run_case,
)


def _select_cases(cases: list, args) -> list:
    if args.case:
        selected = [c for c in cases if c["id"] == args.case]
        if not selected:
            raise SystemExit(f"未找到 case: {args.case}")
        return selected
    if args.all:
        return cases
    raise SystemExit("请指定 --case <id> 或 --all")


def main() -> int:
    # eval 场景直连（避免继承本机 SOCKS 代理导致 httpx 需要 socksio）
    for _k in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(_k, None)

    ap = argparse.ArgumentParser(description="LLM Match Eval（record/replay/live）")
    ap.add_argument("--mode", choices=["record", "replay", "live"], required=True)
    ap.add_argument("--case", help="单个 case id")
    ap.add_argument("--all", action="store_true", help="全部 case")
    ap.add_argument("--golden", default=str(ROOT / "eval/golden/public_v1.jsonl"))
    ap.add_argument("--fixtures", default=str(ROOT / "eval/fixtures"))
    ap.add_argument("--out", default=str(ROOT / "eval-out"))
    ap.add_argument("--thinking", default="medium", help="轮次预算策略（默认 medium）")
    ap.add_argument("--judge", action="store_true", help="live 模式附加 LLM-as-judge")
    ap.add_argument("--judge-model", default="opencode-go/qwen3.7-plus")
    args = ap.parse_args()

    # 机制守卫：CI 环境禁止录制（照抄 opencode：missing cassette 必须 fail，
    # 而不是偷偷调真实 API）
    if args.mode == "record" and os.environ.get("CI"):
        raise SystemExit(
            "CI 环境禁止录制（cassette 缺失必须失败，而不是产生真实 API 调用）"
        )

    cases = _select_cases(load_golden(args.golden), args)
    llm_cfg = llm_cfg_from_env() if args.mode in ("record", "live") else None

    judge_fn = None
    if args.mode == "live" and args.judge:
        from eval.judge import make_judge_fn

        judge_fn = make_judge_fn(model=args.judge_model)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    failures = []
    for case in cases:
        print(f"[{args.mode}] case={case['id']} ...", flush=True)
        try:
            result = asyncio.run(
                run_case(
                    case,
                    mode=args.mode,
                    fixtures_dir=args.fixtures,
                    llm_cfg=llm_cfg,
                    thinking_level=args.thinking,
                    judge_fn=judge_fn,
                )
            )
            results.append(result)
            outcome = result["outcome"]
            line = (
                f"  status={outcome['run_status']} "
                f"candidate={outcome['candidate_subject_id']} "
                f"notify={outcome['notifications']} tokens={outcome['total_tokens']} "
                f"hit={result['hit']}"
            )
            if result.get("judge"):
                j = result["judge"].get("parsed") or {}
                line += f" judge={j.get('score')}"
            print(line, flush=True)
        except (FingerprintMismatch, AssertionError) as e:
            failures.append({"case_id": case["id"], "error": str(e)})
            print(f"  FAIL: {e}", flush=True)

    report = {
        "mode": args.mode,
        "thinking": args.thinking,
        "cases": len(cases),
        "passed": len(results),
        "failed": len(failures),
        "failures": failures,
        "results": results,
    }
    result_path = out_dir / f"result_{args.mode}.json"
    result_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n结果已写入: {result_path}", flush=True)

    if args.mode == "live":
        from eval.report import render_report

        md = render_report(results)
        md_path = out_dir / "report.md"
        md_path.write_text(md, encoding="utf-8")
        print(f"报告已写入: {md_path}\n", flush=True)
        print(md)

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
