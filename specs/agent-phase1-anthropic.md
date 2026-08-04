# Phase 1: Anthropic 协议支持

> 所属计划：Bangumi-Syncer Agent 化三步增量计划
> 前置依赖：无
> 交付物：支持 Anthropic Messages API（及任何兼容该协议的 API）

## 目标

让项目能使用 **任何遵循 Anthropic Messages API 协议** 的 LLM 服务（官方 Anthropic API、或兼容该协议的第三方代理/网关）。不引入 Agent 循环，不改业务逻辑，单纯多一个 provider 选项。

## 为什么第一步做这个

- 后续两步（记忆、Agent 化）都需要 tool_use / tool_result / thinking 这些 Anthropic 协议原生的 content block 类型
- 现有 `Message(role, content: str)` 只支持纯文本，是后续所有扩展的瓶颈
- 这一步只动 LLM 层，零业务风险

## 范围

### 做什么

- Messages API 格式的请求/响应处理
- Content block 类型：`text`、`tool_use`、`tool_result`、`thinking`、`redacted_thinking`
- System prompt 作为顶层参数（而非 message role）
- `stop_reason` 解析（`end_turn`、`tool_use`、`max_tokens`、`stop_sequence`）
- Thinking budget 配置项

### 不做什么

- 不实现 Agent 循环
- 不实现工具调用执行（只做协议层的 content block 解析）
- 不改 `OpenAICompatProvider` 的任何逻辑
- 不添加 Function Calling 的适配层

## 消息模型扩展

当前 `Message(role, content: str)` → 扩展为 `Message(role, content: str | list[ContentBlock])`。

```python
# app/services/llm/models.py

class ContentBlockType:
    TEXT = "text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    REDACTED_THINKING = "redacted_thinking"

class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str

class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict = {}

class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False

class ThinkingBlock(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: str | None = None  # Anthropic 的 thinking signature

class RedactedThinkingBlock(BaseModel):
    type: Literal["redacted_thinking"] = "redacted_thinking"
    data: str

ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock | ThinkingBlock | RedactedThinkingBlock

class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str | list[ContentBlock]  # 兼容旧用法 + 新 content blocks
```

`ChatResponse` 同步扩展：

```python
class ChatResponse(BaseModel):
    content: str                   # 纯文本（无 tool call 时），老代码照常用
    blocks: list[ContentBlock]     # 完整 blocks（含 tool_use/thinking），供 agent 循环用
    stop_reason: str = ""          # end_turn | tool_use | max_tokens
    model: str = ""
    usage: Usage | None = None
    latency: int = 0
```

## 双协议兼容：内部中立模型 + Wire 适配器

一套代码兼容两种协议的关键：**内部模型中立化，协议差异收敛在每个 provider 内部**。

```
业务层 (summary service / 未来的 agent loop)     ← 只认识内部模型
        │
        ▼
   Message / ContentBlock / ChatResponse        ← 内部中立模型 (protocol-agnostic)
        │
        ├──────────────┬──────────────┐
        ▼              ▼              ▼
 OpenAICompat      Anthropic      (未来: 其他)
 _build_request   _build_request
 _parse_response  _parse_response
        │              │
        ▼              ▼
 /v1/chat/completions    /v1/messages
```

每个 provider 只实现两个私有方法：

- `_build_request(messages, **kwargs) -> dict` — 内部模型 → wire 格式（请求体）
- `_parse_response(data) -> ChatResponse` — wire 格式 → 内部模型（响应）

### 协议差异对照表

| 能力 | OpenAI wire | Anthropic wire | 内部模型 |
|---|---|---|---|
| system | `role=system` 消息 | 顶层 `system` 参数 | `role=system` 消息 |
| 文本 | `content: str` | `content: [{type:text}]` | `TextBlock` |
| 工具调用 | `assistant.tool_calls[{id, function:{name, arguments}}]` | assistant content 里的 `tool_use` block | `ToolUseBlock` |
| 工具结果 | `role=tool` 消息，一条一个 | user 消息里的 `tool_result` block，可多个 | `ToolResultBlock` |
| 思考 | 无（DeepSeek 有 `reasoning_content`） | `thinking` / `redacted_thinking` block | `ThinkingBlock` |
| 结束原因 | `finish_reason: stop/tool_calls/length` | `stop_reason: end_turn/tool_use/max_tokens` | 统一 `stop_reason` |

### OpenAI 转换的两个特殊处理点

1. **arguments 是 JSON 字符串**：OpenAI 的 `tool_calls[].function.arguments` 是 string，内部模型统一为 dict，解析失败兜底为 `{"raw": "..."}`。agent 循环只面对 dict
2. **tool_result 拆/并**：内部一条 user 消息里可有多个 `tool_result` block → 转 OpenAI 时拆成多条 `role=tool` 消息；反向解析时把连续的 tool 消息合并回一条 user 消息的多个 block。`tool_call_id` 与 `tool_use_id` 一一对应

### system prompt 差异由 provider 内部处理

`AnthropicProvider._build_request` 中：从 messages 里抽出 `role=system` 的消息拼成顶层 `system` 参数（多个 system 消息用 `\n\n` 连接）。`OpenAICompatProvider` 照旧当作普通消息。

### 兼容性保证

- `Message.content: str` 旧用法不变，`content: str | list[ContentBlock]` 向后兼容
- `ChatResponse.content` 保留，summary 服务零改动
- 新增字段（`blocks`、`stop_reason`）只被 Phase 3 的 agent 循环消费
- `LLMClient` 的重试、用量记录逻辑完全不用动

## AnthropicProvider

```python
# app/services/llm/providers/anthropic.py

class AnthropicProvider(BaseProvider):
    """Anthropic Messages API provider.

    兼容任何实现 /v1/messages 端点的服务：
    - api.anthropic.com (官方)
    - 第三方代理/网关 (如 openrouter, one-api 等)
    """

    def __init__(self, config: LLMConfig):
        self.base_url = config.base_url.rstrip("/")
        self.api_key = config.api_key
        self.model = config.model
        self.max_tokens = config.max_tokens or 4096
        self.thinking_level = config.get("thinking_level", "off")
        self._http = httpx.AsyncClient(timeout=120)

    # thinking_level → Anthropic budget_tokens 映射
    _THINKING_BUDGETS = {"off": 0, "low": 2048, "medium": 4096, "high": 8192}

    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        # 1. 内部模型 → wire 格式（含 system 提取、content blocks 构建）
        body = self._build_request(messages, **kwargs)
        # 2. 调用 POST /v1/messages
        # 3. wire 格式 → 内部模型（解析 content blocks、usage、stop_reason）
        data = await self._post("/v1/messages", body)
        return self._parse_response(data)

    def _build_request(self, messages, **kwargs) -> dict:
        """内部模型 → Anthropic wire 格式"""
        system_parts = [m.content for m in messages if m.role == "system"]
        body = {
            "model": kwargs.get("model", self.model),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "messages": [self._to_wire_message(m) for m in messages if m.role != "system"],
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        # thinking_level：每任务 kwargs 覆盖 > 全局默认
        level = kwargs.get("thinking_level", self.thinking_level)
        budget = self._THINKING_BUDGETS.get(level, 0)
        if budget > 0:
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            body["temperature"] = 1  # Anthropic 要求 thinking 开启时 temperature 必须为 1
        return body

    def _parse_response(self, data: dict) -> ChatResponse:
        """Anthropic wire 格式 → 内部模型"""
        ...
```

## 配置

```ini
[llm]
provider = openai_compat          # 新增选项: openai_compat | anthropic_compat
api_base = https://api.openai.com/v1
api_key = sk-xxx
model = gpt-4o-mini
max_tokens = 2000
temperature = 0.7
timeout = 60
thinking_level = off              # 新增字段: off | low | medium | high，缺省 off
```

### 与 openai_compat 的配置对比

| 字段 | openai_compat | anthropic_compat | 差异 |
|---|---|---|---|
| `provider` | `openai_compat` | `anthropic_compat` | 无 |
| `api_base` | `https://api.openai.com/v1` | `https://api.anthropic.com/v1` | 仅默认值不同 |
| `api_key` / `model` / `timeout` | ✓ | ✓ | 无 |
| `max_tokens` | ✓ | ✓ | 字段相同；thinking 的 token 计入输出上限，开启时需调大 |
| `temperature` | ✓ | ✓ | 字段相同；thinking 开启时 Anthropic 要求为 1，provider 内部强制 |
| **`thinking_level`** | ✓ | ✓ | **唯一新增字段，协议无关** |

`thinking_level` 是协议无关的思考强度枚举，各 provider 内部映射到自己的 wire 参数：

| thinking_level | anthropic_compat | openai_compat |
|---|---|---|
| `off`（缺省，最弱） | 不传 `thinking` | 不传 `reasoning_effort`（= 现状行为） |
| `low` | `thinking.budget_tokens=2048` | `reasoning_effort=low` |
| `medium` | `thinking.budget_tokens=4096` | `reasoning_effort=medium` |
| `high` | `thinking.budget_tokens=8192` | `reasoning_effort=high` |

**为什么用枚举而非各协议的原始参数**：`budget_tokens`（token 数）与 `reasoning_effort`（枚举）单位不同，暴露给用户需要两套控件。统一为 `thinking_level` 后，前端一个下拉框搞定。缺省 `off` = 与现状行为完全一致，零回归风险。

### 全局默认 + 每任务覆盖（不需要每任务独立配置段）

沿用现有 `LLMClient.chat(messages, **kwargs)` 的透传机制 —— `max_tokens` / `temperature` 现在就是这样被任务覆盖的：

```python
await llm_client.chat(messages, thinking_level="high")   # 本任务覆盖为 high
await llm_client.chat(messages)                          # 用全局默认
```

- `[llm] thinking_level` = 全局默认值
- 每个任务通过 kwargs 覆盖 `thinking_level`，无需为 anthropic 单独建配置段
- summary job 如需按任务配置，Phase 2 再在 `[summary-{name}]` 加可选字段
- Phase 3 的预算档位（`budget: "quick" | "normal" | "diagnostic"`）本质也是映射到这套 kwargs

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `app/services/llm/providers/anthropic.py` | AnthropicProvider 实现 |
| 修改 | `app/services/llm/models.py` | 扩展 Message 模型，新增 ContentBlock 类型 |
| 修改 | `app/services/llm/providers/base.py` | BaseProvider.chat() 签名不变，确认兼容 |
| 修改 | `app/services/llm/providers/openai_compat.py` | `thinking_level` → `reasoning_effort` 映射；tool_use/tool_result 拆并 |
| 修改 | `app/services/llm/client.py` | 工厂方法新增 `anthropic_compat` 分支 |
| 修改 | `app/core/config.py` | `get_llm_config()` 默认值新增 `thinking_level="off"` |
| 修改 | `app/core/config_schema.py` | `[llm]` section 新增 `provider`/`thinking_level` 字段元数据 |
| 修改 | `app/models/summary.py` | `LLMConfigResponse`/`LLMConfigUpdate` 增加 `provider`、`thinking_level` |
| 修改 | `app/api/llm.py` | conf 接口透传新字段 |
| 修改 | `templates/config/_llm.html` | Provider 下拉框 + 思考强度下拉框 |

> 总计：新增 1 个文件，修改 9 个文件

## 验证方式

1. 配置指向 Anthropic API（或兼容代理），调用 `/api/llm/test`，确认返回 "Hello" 响应
2. 切换到 Anthropic provider 后，手动触发一个 AI 总结任务，确认正常生成并发送通知
3. 设置 `thinking_level=medium`，确认响应中包含 thinking block 且不计入最终 content
4. `thinking_level` 缺省 `off` 时行为与现状完全一致（回归验证）
5. 保留 OpenAI 兼容模式，确认现有功能不受影响
6. 详细验收用例见 `agent-phase1-test-plan.md`（BDD）
