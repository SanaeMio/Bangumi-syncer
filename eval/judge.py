"""LLM-as-judge：经本地 opencode CLI 调用异源模型（默认 qwen3.7-plus）。

关键约定（面试常问「怎么保证 judge 可信」）：

- **异源判分**：judge 模型与生成模型不同源（生成用 DeepSeek，judge 用 Qwen）；
- **结构化输出** + JSON 解析失败计 error（不计 pass/fail）；
- **可校准**：结合人工抽检（见报告模板中的校准列）计算一致率。
"""

from __future__ import annotations

import json
import subprocess
import tempfile

JUDGE_PROMPT_TEMPLATE = """你是严格的评测裁判，评估一次「番剧匹配建议」的质量。
请只依据下面给出的信息判断，不要使用任何工具，也不要请求更多信息。

## 媒体库请求
{request}

## 人工期望（golden）
{expect}

## Agent 输出
- 运行结论: {status}
- 建议条目: {candidate}
- 建议理由: {reason}

## 评分标准（1-5）
- 5 = 结论与期望一致，且理由有据（或正确给出「不建议」）
- 4 = 结论可接受，理由基本合理
- 3 = 结论勉强可接受，但理由薄弱/含糊
- 2 = 结论可疑或理由与证据存在出入
- 1 = 结论错误，或理由明显幻觉

只输出一行 JSON（不要代码块、不要多余文字）：
{{"verdict":"good|mixed|bad","score":1-5,"issues":["..."]}}"""


def call_opencode(prompt: str, model: str, timeout: int = 240) -> str:
    """调用 opencode CLI 一次（在临时目录运行，避免项目上下文污染）。"""
    workdir = tempfile.mkdtemp(prefix="eval_judge_")
    proc = subprocess.run(
        ["opencode", "run", "-m", model, "--format", "json", prompt],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"opencode judge 调用失败（exit={proc.returncode}）: {proc.stderr[:300]}"
        )
    texts = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("type") == "text":
            texts.append((event.get("part") or {}).get("text") or "")
    return "\n".join(texts).strip()


def parse_judge_json(text: str):
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


def judge_case(case: dict, outcome: dict, *, model: str) -> dict:
    """对单条 case 判分；解析失败重试一次。"""
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        request=json.dumps(case.get("input") or {}, ensure_ascii=False),
        expect=json.dumps(case.get("expect") or {}, ensure_ascii=False),
        status=outcome.get("run_status"),
        candidate=outcome.get("candidate_subject_id") or "无建议",
        reason=outcome.get("candidate_reason") or "（无）",
    )
    last_raw = ""
    for attempt in range(2):
        last_raw = call_opencode(prompt, model)
        parsed = parse_judge_json(last_raw)
        if parsed is not None:
            return {
                "model": model,
                "attempt": attempt + 1,
                "parsed": parsed,
                "raw": last_raw[:800],
            }
    return {
        "model": model,
        "attempt": 2,
        "parsed": None,
        "error": "JSON 解析失败",
        "raw": last_raw[:800],
    }


def make_judge_fn(*, model: str):
    def _judge(case: dict, outcome: dict) -> dict:
        return judge_case(case, outcome, model=model)

    return _judge
