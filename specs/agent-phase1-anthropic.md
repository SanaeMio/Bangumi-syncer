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
        self.thinking_budget = config.max_thinking_tokens or 0  # 0 = 不启用
        self._http = httpx.AsyncClient(timeout=120)

    async def chat(self, messages: list[Message]) -> ChatResponse:
        # 1. 分离 system prompt (Anthropic 要求顶层参数)
        # 2. 构建 content blocks 列表
        # 3. 调用 POST /v1/messages
        # 4. 解析响应中的 content blocks (text/tool_use/thinking)
        # 5. 提取 usage + stop_reason
        # 6. 返回 ChatResponse
        ...
```

## 配置

```ini
[llm]
provider = anthropic              # 新增选项: openai_compat | anthropic
api_base = https://api.anthropic.com/v1
api_key = sk-ant-xxx
model = claude-sonnet-4-6
max_tokens = 4096
max_thinking_tokens = 0           # 新增字段，0=禁用 thinking
```

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `app/services/llm/providers/anthropic.py` | AnthropicProvider 实现 |
| 修改 | `app/services/llm/models.py` | 扩展 Message 模型，新增 ContentBlock 类型 |
| 修改 | `app/services/llm/providers/base.py` | BaseProvider.chat() 签名不变，确认兼容 |
| 修改 | `app/services/llm/client.py` | 工厂方法新增 `anthropic` 分支 |
| 修改 | `app/core/config_schema.py` | `[llm]` section 新增 `max_thinking_tokens` |
| 修改 | `templates/config/_llm.html` | provider 下拉框新增 Anthropic 选项 + thinking 配置 |

> 总计：新增 1 个文件，修改 5 个文件

## 验证方式

1. 配置指向 Anthropic API（或兼容代理），调用 `/api/llm/test`，确认返回 "Hello" 响应
2. 切换到 Anthropic provider 后，手动触发一个 AI 总结任务，确认正常生成并发送通知
3. 设置 `max_thinking_tokens > 0`，确认响应中包含 thinking block 且不计入最终 content
4. 保留 OpenAI 兼容模式，确认现有功能不受影响
