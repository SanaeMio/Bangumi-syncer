"""``replay_delta`` 判别联合：四种形态的模型与单点编解码边界。

``agent_steps.replay_delta`` 是一列（加密的）JSON 文本，承载四种形态；本模块
把它们建模为**带 ``kind`` 判别式的 pydantic 联合**，并提供写/读两个唯一出口：

- :func:`encode_replay_delta`（写侧）：模型 → 落库 dict——字段名/结构在检查期
  就被约束，拼写错误不会静默进库；
- :func:`decode_replay_delta`（读侧）：原始值 → 联合模型——**运行时校验**；
  未知 ``kind`` / 未知 ``delta_version`` / 字段类型错 / 坏 JSON → warning 并
  返回 ``None``（显式降级，调用方跳过该行；不猜测、不静默）。

``delta_version`` 是格式演进的抓手：将来新增字段/新形态时按版本分派兼容，
``decode`` 对未知版本显式降级。当前仅支持 :data:`DELTA_VERSION`。

（budget 消息为**独立形态 + 独立行**：``name="budget"``，一行一 delta，
不再"并入同轮最后一条 tool_execute"，消除归属时序假设。）
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app.core.logging import logger

__all__ = [
    "DELTA_VERSION",
    "BudgetMessageDelta",
    "ReplayDelta",
    "ResponseDelta",
    "SeedDelta",
    "ToolResultDelta",
    "decode_replay_delta",
    "encode_replay_delta",
]

#: 当前支持的 ``replay_delta`` 格式版本（演进抓手，读侧对未知版本显式降级）
DELTA_VERSION = 1


class SeedDelta(BaseModel):
    """seed 行形态：``replay_delta.seed_messages``。"""

    kind: Literal["seed"] = "seed"
    delta_version: int = DELTA_VERSION
    seed_messages: list[dict]


class ResponseDelta(BaseModel):
    """llm_chat 行形态：``replay_delta.response``（payload 保持透传）。"""

    kind: Literal["response"] = "response"
    delta_version: int = DELTA_VERSION
    response: dict


class ToolResultDelta(BaseModel):
    """tool_execute 行形态：``replay_delta.tool_result``。"""

    kind: Literal["tool_result"] = "tool_result"
    delta_version: int = DELTA_VERSION
    tool_result: dict


class BudgetMessageDelta(BaseModel):
    """budget 行形态：``replay_delta.budget_message``（独立行，一行一形态）。"""

    kind: Literal["budget_message"] = "budget_message"
    delta_version: int = DELTA_VERSION
    budget_message: str


#: 判别联合（``kind`` 为判别式）；写/读边界统一经 encode/decode
ReplayDelta = Annotated[
    SeedDelta | ResponseDelta | ToolResultDelta | BudgetMessageDelta,
    Field(discriminator="kind"),
]

_REPLAY_DELTA_ADAPTER: TypeAdapter[ReplayDelta] = TypeAdapter(ReplayDelta)


def encode_replay_delta(delta: ReplayDelta) -> dict:
    """判别联合 → 落库 dict（写侧唯一出口；加密/JSON 化由仓储层负责）。"""
    return delta.model_dump()


def decode_replay_delta(raw: Any) -> ReplayDelta | None:
    """落库原始值 → 判别联合（读侧唯一入口）。

    - ``raw`` 可为 dict 或 JSON 字符串；``None`` / ``""`` / ``{}`` 为合法空值，
      静默返回 ``None``；
    - 非空非法（坏 JSON / 缺 ``kind`` / 未知 ``kind`` / 字段类型错 /
      未知 ``delta_version``）→ 记 warning 并返回 ``None``（显式降级）。
    """
    if raw is None or raw == "" or raw == {}:
        return None
    try:
        delta = (
            _REPLAY_DELTA_ADAPTER.validate_json(raw)
            if isinstance(raw, str)
            else _REPLAY_DELTA_ADAPTER.validate_python(raw)
        )
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ())) or "?"
        logger.warning(
            f"[delta] replay_delta 形态校验失败，已降级跳过"
            f"（loc={loc}: {first.get('msg', type(e).__name__)}）"
        )
        return None
    if delta.delta_version != DELTA_VERSION:
        logger.warning(
            f"[delta] replay_delta 未知 delta_version={delta.delta_version}"
            f"（当前支持 {DELTA_VERSION}），已降级跳过"
        )
        return None
    return delta
