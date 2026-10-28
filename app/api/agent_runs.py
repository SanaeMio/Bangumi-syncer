"""
Agent 追踪查询 API

提供观测用途的追踪查询端点（仅元数据，不暴露内部重放全文）：
- ``GET /api/agent/runs/{run_id}``      → 单次会话元数据
- ``GET /api/agent/runs/{run_id}/steps`` → span 列表（按 (iteration, sequence) 排序）

鉴权语义：
- 未认证 → 401（复用 ``get_current_user_flexible`` 既有行为）
- run 不存在或无权访问 → 404（统一文案「追踪记录不存在」，不暴露 run_id 存在性；
  经 ``sync_record_id`` 关联 ``sync_records.user_name`` 校验归属，拒绝场景记审计 warning）

展示契约：
- 响应永不包含 ``replay_delta`` 原文（仅用于内部重放）。
- ``payload_json`` 列已删除，不再出现在响应中。
- steps 端点新增 ``display_json``：读取时解密 replay_delta → 现场生成展示摘要
  （直接返回嵌套对象，超限时截断字符串字段并追加 ``...[shrinked]`` 标记）。
- 时间字段由 epoch 秒整数转为 ISO 8601 字符串（统一 UTC，形如 ``...+00:00``）；0/None → None。
"""

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..api.deps import get_current_user_flexible
from ..core.database import database_manager
from ..core.logging import logger
from ..core.security import security_manager
from ..services.agent.observe.delta import (
    BudgetMessageDelta,
    ResponseDelta,
    SeedDelta,
    ToolResultDelta,
    decode_replay_delta,
)
from ..utils.truncate import MAX_PAYLOAD_JSON_BYTES, truncate_text_with_marker

router = APIRouter(prefix="/api/agent", tags=["agent"])

# 列表端点的分页与窗口常量
_LIST_DEFAULT_PAGE_SIZE = 20
_LIST_MAX_PAGE_SIZE = 100
_LIST_DEFAULT_DAYS = 30

# 单 span 详情接口的展示上限（仅在用户按需展开时传输；预览仍为 2KB）
MAX_STEP_DETAIL_BYTES = 32 * 1024
# /steps 的 thinking 摘要上限（独立字段，不挤占 display_json 的 2KB 预算）
THINKING_PREVIEW_BYTES = 300

# run 端点对外暴露的字段（不含 payload_json / replay_delta 等内部重放字段）
_RUN_PUBLIC_FIELDS = (
    "run_id",
    "status",
    "stop_reason",
    "task_type",
    "sync_record_id",
    "business_key",
    "attempts",
    "total_attempts",
    "total_tokens",
    "started_at",
    "ended_at",
    "created_at",
    "last_error",
)

# steps 端点对外暴露的字段（不含 payload_json / replay_delta；display_json 现场生成）
_STEP_PUBLIC_FIELDS = (
    "span_id",
    "name",
    "status",
    "model",
    "tokens",
    "latency_ms",
    "tool_name",
    "input_summary",
    "error",
    "iteration",
    "sequence",
    "started_at",
    "ended_at",
)

# 时间字段集合（run / steps 共享），用于 ISO 转换
_TIME_FIELDS = ("started_at", "ended_at", "created_at")


def _is_admin_user(current_user: dict) -> bool:
    """当前用户是否为管理员。

    管理员判定（与系统单管理员模型一致）：
    - 认证关闭（auth_disabled）视为管理员
    - 会话带 is_admin 标志
    - 用户名为配置中 auth.username 管理员账号
    """
    if current_user.get("auth_disabled"):
        return True
    if current_user.get("is_admin"):
        return True
    try:
        admin_name = security_manager.get_auth_config().get("username")
    except Exception:  # pragma: no cover - 配置异常时保守处理
        admin_name = None
    return bool(admin_name) and current_user.get("username") == admin_name


def _resolve_owner_user_name(run: dict) -> str | None:
    """经 sync_record_id 解析会话归属用户（无则 None）。"""
    sync_record_id = run.get("sync_record_id")
    if not sync_record_id:
        return None
    try:
        record = database_manager.sync_records.get_sync_record_by_id(sync_record_id)
    except Exception as e:  # pragma: no cover - 查询异常时按“无归属”处理
        logger.error(f"查询 sync_record 归属失败: {e}")
        return None
    if not record:
        return None
    return record.get("user_name")


def _log_access_denied(
    endpoint: str, run_id: str, current_user: dict, reason: str, owner: str | None
) -> None:
    """拒绝访问追踪记录时记录审计日志（不静默）。"""
    logger.warning(
        f"⚠️ 追踪记录访问拒绝 endpoint={endpoint} run_id={run_id} "
        f"username={current_user.get('username')} reason={reason} owner={owner}"
    )


def _trace_not_found() -> HTTPException:
    """统一的 404：不存在与无权访问共用，避免泄露 run_id 存在性。"""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="追踪记录不存在")


def _load_authorized_run(run_id: str, current_user: dict, endpoint: str) -> dict:
    """查询 run 并鉴权，返回可访问的 run 数据。

    不存在与无权访问统一抛 404（不暴露存在性）；拒绝场景先记审计日志再抛同一 404。
    鉴权顺序：先取 run 数据（owner 解析所需），再做权限判定。
    """
    run = database_manager.agent_runs.get_run(run_id)
    if not run:
        _log_access_denied(
            endpoint, run_id, current_user, reason="not_found", owner=None
        )
        raise _trace_not_found()
    if _is_admin_user(current_user):
        return run
    owner = _resolve_owner_user_name(run)
    if owner is not None and current_user.get("username") == owner:
        return run
    _log_access_denied(endpoint, run_id, current_user, reason="forbidden", owner=owner)
    raise _trace_not_found()


def _iso_from_epoch(ts) -> str | None:
    """epoch 秒整数 → ISO 8601 字符串（统一 UTC，形如 ``...+00:00``）；0/None/非数字 → None。"""
    if not ts:
        return None
    if not isinstance(ts, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _apply_time_fields(record: dict) -> dict:
    """将记录中的时间列原地转换为 ISO 字符串。"""
    for field in _TIME_FIELDS:
        if field in record:
            record[field] = _iso_from_epoch(record.get(field))
    return record


def _json_byte_size(obj) -> int:
    """对象序列化后的 UTF-8 字节数（展示预算度量）。"""
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _fit_preview_within_limit(
    preview: dict, *, max_bytes: int = MAX_PAYLOAD_JSON_BYTES
) -> dict:
    """保证展示预览序列化后 ≤ max_bytes（默认 2KB 预览预算）。

    超限时逐轮截断当前最长的字符串字段并追加 ``...[shrinked]`` 标记；若已无
    可截断字符串字段、或收敛失败，则返回空对象（记 warning，不静默）。
    """
    if _json_byte_size(preview) <= max_bytes:
        return preview
    result = dict(preview)
    for _ in range(8):
        text_fields = [(k, v) for k, v in result.items() if isinstance(v, str)]
        if not text_fields:
            logger.warning("⚠️ display_json 预览无可截断字符串字段，返回空对象")
            return {}
        key, value = max(text_fields, key=lambda kv: len(kv[1].encode("utf-8")))
        # 该字段可用预算 = 总上限 - 其余字段（含本字段空串占位）序列化开销
        overhead = _json_byte_size({**result, key: ""})
        result[key] = truncate_text_with_marker(value, max(0, max_bytes - overhead))
        if _json_byte_size(result) <= max_bytes:
            return result
    logger.warning("⚠️ display_json 预览截断多轮后仍超限，返回空对象")
    return {}


def _extract_thinking(replay_delta: str) -> str:
    """从 ``response.blocks`` 提取思考文本。

    - ``thinking`` 块按出现顺序拼接（空行分隔）；
    - ``redacted_thinking`` 以 ``[已脱敏]`` 占位（不可解码但需让用户知道存在）；
    - 非 response / 无 blocks / 旧数据 → 空串（兼容）。
    """
    delta = decode_replay_delta(replay_delta)
    if not isinstance(delta, ResponseDelta):
        return ""
    response = delta.response or {}
    blocks = response.get("blocks") or []
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "thinking":
            text = block.get("thinking")
            if isinstance(text, str) and text:
                parts.append(text)
        elif kind == "redacted_thinking":
            parts.append("[已脱敏]")
    return "\n\n".join(parts)


def _build_display_json(
    replay_delta: str, *, max_bytes: int = MAX_PAYLOAD_JSON_BYTES
) -> dict:
    """从 replay_delta 现场生成展示摘要（decode 分派；超限截断）。

    - response → {"stop_reason", "content"}
    - tool_result → {"tool_use_id", "content", "is_error"}
    - seed → {"seed_messages_count": N}
    - budget_message → {"budget_message": ...}
    - 空 delta / 形态非法（decode 降级）→ {}

    ``max_bytes`` 默认 2KB（列表预览）；单 span 详情传 32KB 以展示完整内容。
    """
    delta = decode_replay_delta(replay_delta)
    if delta is None:
        return {}
    if isinstance(delta, ResponseDelta):
        response = delta.response or {}
        preview = {
            "stop_reason": response.get("stop_reason", ""),
            "content": response.get("content", ""),
        }
    elif isinstance(delta, ToolResultDelta):
        tool_result = delta.tool_result or {}
        preview = {
            "tool_use_id": tool_result.get("tool_use_id", ""),
            "content": tool_result.get("content", ""),
            "is_error": tool_result.get("is_error", False),
        }
    elif isinstance(delta, SeedDelta):
        preview = {"seed_messages_count": len(delta.seed_messages)}
    elif isinstance(delta, BudgetMessageDelta):
        preview = {"budget_message": delta.budget_message}
    else:
        return {}

    return _fit_preview_within_limit(preview, max_bytes=max_bytes)


def _build_step_detail(replay_delta: str) -> dict:
    """单 span 详情：完整 display_json + thinking（独立上限，超限截断）。"""
    thinking = _extract_thinking(replay_delta)
    thinking_truncated = False
    if len(thinking.encode("utf-8")) > MAX_STEP_DETAIL_BYTES:
        thinking = truncate_text_with_marker(thinking, MAX_STEP_DETAIL_BYTES)
        thinking_truncated = True
    return {
        "display_json": _build_display_json(
            replay_delta, max_bytes=MAX_STEP_DETAIL_BYTES
        ),
        "thinking": thinking,
        "thinking_truncated": thinking_truncated,
    }


def _project_run(run: dict) -> dict:
    """裁剪为对外元数据（剔除内部重放字段），时间列 ISO 化。"""
    projected = {field: run.get(field) for field in _RUN_PUBLIC_FIELDS}
    _apply_time_fields(projected)
    return projected


def _project_steps(steps: list) -> list:
    """裁剪 span 列表：剔除内部字段、生成 display_json、ISO 时间、按 (iteration, sequence) 排序。"""
    projected = []
    for s in steps:
        record = {f: s.get(f) for f in _STEP_PUBLIC_FIELDS}
        record["display_json"] = _build_display_json(s.get("replay_delta", ""))
        thinking_full = _extract_thinking(s.get("replay_delta", ""))
        record["thinking"] = truncate_text_with_marker(
            thinking_full, THINKING_PREVIEW_BYTES
        )
        record["thinking_truncated"] = (
            len(thinking_full.encode("utf-8")) > THINKING_PREVIEW_BYTES
        )
        _apply_time_fields(record)
        projected.append(record)
    projected.sort(key=lambda r: (r.get("iteration") or 0, r.get("sequence") or 0))
    return projected


@router.get("/runs")
async def list_agent_runs(
    run_status: str = Query("", alias="status"),
    task_type: str = "",
    days: int = _LIST_DEFAULT_DAYS,
    page: int = 1,
    page_size: int = _LIST_DEFAULT_PAGE_SIZE,
    current_user: dict = Depends(get_current_user_flexible),
):
    """列出 agent 会话（观测用；非 admin 仅见自己的 run）。

    - 分页：``page`` / ``page_size``（上限 100）；创建时间倒序（id DESC）；
    - 过滤：``status``（pending/processing/succeeded/failed/cancelled）、
      ``task_type``、``days``（窗口天数，非正数回退 30）；
    - 授权：非 admin 经 ``sync_records.user_name`` 归属过滤；无用户名时返回空
      （不查库、不泄漏全量）；admin 看全部；
    - 字段：与单 run 端点同口径（不含 replay_delta 原文）；时间 ISO 化。
    """
    page = max(1, page)
    page_size = min(max(1, page_size), _LIST_MAX_PAGE_SIZE)
    window_days = days if days > 0 else _LIST_DEFAULT_DAYS
    since = int(time.time()) - window_days * 86400

    owner: str | None
    if _is_admin_user(current_user):
        owner = None
    else:
        owner = current_user.get("username") or ""
        if not owner:
            # 异常态（非 admin 且无用户名）：不查库、不泄漏全量
            return {"items": [], "total": 0, "page": page, "page_size": page_size}

    filters = {
        "status": run_status or None,
        "task_type": task_type or None,
        "since": since,
        "user_name": owner,
    }
    rows = database_manager.agent_runs.list_runs(
        limit=page_size, offset=(page - 1) * page_size, **filters
    )
    total = database_manager.agent_runs.count_runs(**filters)

    items = [_project_run(run) for run in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/runs/{run_id}")
async def get_agent_run(
    run_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict:
    """获取单次 Agent 会话的元数据（观测用途）。"""
    run = _load_authorized_run(run_id, current_user, endpoint="get_agent_run")
    return _project_run(run)


@router.get("/runs/{run_id}/steps")
async def get_agent_run_steps(
    run_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
) -> list:
    """获取单次 Agent 会话的 span 列表（按 (iteration, sequence) 排序）。"""
    _load_authorized_run(run_id, current_user, endpoint="get_agent_run_steps")
    steps = database_manager.agent_runs.get_steps(run_id)
    return _project_steps(steps)


@router.get("/runs/{run_id}/steps/{span_id}")
async def get_agent_run_step_detail(
    run_id: str,
    span_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict:
    """获取单个 span 的完整展示内容（按需展开；鉴权同 /steps）。

    ``/steps`` 为 2KB 预览；本端点返回完整 ``display_json``（32KB 上限）与
    ``thinking`` 全文（32KB 上限），并携带 ``thinking_truncated`` 标记。
    """
    _load_authorized_run(run_id, current_user, endpoint="get_agent_run_step_detail")
    steps = database_manager.agent_runs.get_steps(run_id)
    step = next((s for s in steps if s.get("span_id") == span_id), None)
    if step is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="追踪步骤不存在"
        )
    record = {f: step.get(f) for f in _STEP_PUBLIC_FIELDS}
    detail = _build_step_detail(step.get("replay_delta", ""))
    record["display_json"] = detail["display_json"]
    record["thinking"] = detail["thinking"]
    record["thinking_truncated"] = detail["thinking_truncated"]
    _apply_time_fields(record)
    return record
