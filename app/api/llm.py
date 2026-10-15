"""
LLM 配置与连接测试 API。
"""

import time
from dataclasses import asdict

from fastapi import APIRouter, Depends

from ..core.config import LLM_SECTION, config_manager
from ..core.database import database_manager
from ..models.summary import (
    LLMConfigResponse,
    LLMConfigUpdate,
    LLMTestResponse,
    LLMUsageStatsResponse,
)
from ..services.llm import LLMCallError, Message, get_llm_client, reset_llm_client
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.get("/conf", response_model=LLMConfigResponse)
async def get_llm_config(_=Depends(get_current_user_flexible)):
    cfg = config_manager.get_llm_config()
    # 遮掩 api_key 显示
    api_key = cfg.get("api_key", "")
    if api_key:
        api_key = "***" + api_key[-4:] if len(api_key) > 4 else "***"
    return LLMConfigResponse(
        api_base=cfg.get("api_base", ""),
        api_key=api_key,
        model=cfg.get("model", ""),
        max_tokens=cfg.get("max_tokens", 2000),
        temperature=cfg.get("temperature", 0.7),
        timeout=cfg.get("timeout", 60),
        provider=cfg.get("provider", "openai_compat"),
    )


@router.put("/conf")
async def update_llm_config(
    body: LLMConfigUpdate, _=Depends(get_current_user_flexible)
):
    """部分更新 LLM 配置。"""
    updates = body.model_dump(exclude_none=True)
    for key, value in updates.items():
        if key == "api_key" and (
            str(value).startswith("***") or str(value).strip() == ""
        ):
            continue  # 掩码值或空值视为未修改，跳过写入
        config_manager.set_config(LLM_SECTION, key, str(value))
    config_manager.reload_config()
    reset_llm_client()
    return {"status": "success", "message": "LLM 配置已更新"}


@router.post("/test", response_model=LLMTestResponse)
async def test_llm_connection(_=Depends(get_current_user_flexible)):
    """发送简单 ping 验证 LLM 连通性（流式：收到首个内容事件即判定连通）。

    连通性验证语义：不展示回复正文（短回复会被 max_tokens 截停，展示半截句
    反而迷惑）；只返回 成功/模型/延迟。max_tokens=8 让模型在第 8 个 token
    处被 API 截停——服务端不会"生成后丢弃"，只是限制生成长度。

    改走 ``stream_chat()``：收到**首个内容事件**（``text_delta`` 或非空
    ``thinking_delta``——覆盖思考模型 thinking 先行的场景）即视为连通并主动
    ``aclose()``，无需等待全量响应生成完毕，延迟显著低于 ``chat()``（后者需
    消费完整流才能聚合出 ChatResponse）。副作用：提前关闭走 client 的
    "未正常耗尽 → 跳过成功落库" 路径，连接测试 ping 不计入用量统计（合理：
    这是探活而非真实业务调用）。model 取首个事件携带的真实模型名
    （``chunk.model``），事件缺失时回退当前 LLM 配置 model。
    """
    try:
        client = get_llm_client()
        t0 = time.time()
        stream = client.stream_chat(
            [Message(role="user", content="ping")],
            job_name="连接测试",
            max_tokens=8,
        )
        connected = False
        stream_model = ""
        try:
            async for chunk in stream:
                if chunk.model and not stream_model:
                    stream_model = chunk.model
                # 内容事件即判定连通：text_delta 或 thinking_delta（思考模型开启思考后
                # thinking_delta 先行，若只认 text_delta 会在长思考期间超时/误报失败）。
                # 空内容事件（text/thinking 均为空）不计入，保持「无内容 → 失败」语义。
                if (chunk.type == "text_delta" and chunk.text) or (
                    chunk.type == "thinking_delta" and chunk.thinking
                ):
                    connected = True
                    break
        finally:
            # 幂等关闭：提前 break 时触发"未耗尽"路径（不落库）；正常耗尽/异常时为空操作
            await stream.aclose()
        latency = int((time.time() - t0) * 1000)
        # 无任何内容事件（如仅 stop）视为失败，与旧 not content 语义一致
        if not connected:
            return LLMTestResponse(success=False, message="LLM 调用失败（返回空内容）")
        return LLMTestResponse(
            success=True,
            message="连接成功",  # 不含回复正文（短回复截停无展示价值）
            # 真实值优先，事件未携带时回退配置（与流式业务展示口径一致）
            model=stream_model or config_manager.get_llm_config()["model"],
            latency_ms=latency,
        )
    except LLMCallError as e:
        # 重试耗尽或确定性错误（401/403/refusal 等）
        return LLMTestResponse(success=False, message=str(e))
    except Exception as e:
        return LLMTestResponse(success=False, message=str(e))


@router.get("/stats", response_model=LLMUsageStatsResponse)
async def get_llm_stats(
    scope: str = "aggregate", days: int = 30, _=Depends(get_current_user_flexible)
):
    """获取 LLM 用量统计。"""
    stats = database_manager.llm_usage.get_stats(scope=scope, days=days)
    return LLMUsageStatsResponse(**asdict(stats))
