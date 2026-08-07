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
- Content block 类型：`text`、`thinking`、`redacted_thinking`（工具相关 block 见 Phase 2.1）
- System prompt 作为顶层参数（而非 message role）
- `stop_reason` 解析（`end_turn`、`tool_use`、`max_tokens`、`stop_sequence`）
- `thinking_level` 配置项（off/low/medium/high）
- thinking 模型能力降级（claude-haiku 等不支持时忽略并 warning）

### 不做什么

- 不实现 Agent 循环
- 不实现工具调用（ToolUseBlock/ToolResultBlock 类型与转换见 Phase 2.1）
- 不改 `OpenAICompatProvider` 的任何逻辑（openai 适配重构见 Phase 2.2）
- 不添加 Function Calling 的适配层
- 不解析 `tool_use` block——遇到未知 block 类型时**跳过并记录 warning，不崩溃**（正式解析在 Phase 2.1）

## 消息模型扩展

当前 `Message(role, content: str)` → 扩展为 `Message(role, content: str | list[ContentBlock])`。

```python
# app/services/llm/models.py

from typing import Literal
from pydantic import BaseModel, Field

class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str

class ThinkingBlock(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: str | None = None  # Anthropic 的 thinking signature

class RedactedThinkingBlock(BaseModel):
    type: Literal["redacted_thinking"] = "redacted_thinking"
    data: str

ContentBlock = TextBlock | ThinkingBlock | RedactedThinkingBlock
# Phase 2.1 追加 ToolUseBlock / ToolResultBlock（Pydantic union 扩展向后兼容）

class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str | list[ContentBlock]  # 兼容旧用法 + 新 content blocks
```

`ChatResponse` 同步扩展：

```python
class ChatResponse(BaseModel):
    content: str                        # 纯文本（无 tool call 时），老代码照常用
    blocks: list[ContentBlock] = Field(default_factory=list)  # 完整 blocks，供 agent 循环用
    stop_reason: str = ""               # end_turn | tool_use | max_tokens
    model: str = ""
    usage: Usage | None = None
    latency: int = 0
```

> 注：不用常量类（`ContentBlockType`），`type` 字段由 `Literal` 做强约束（见"ContentBlock 设计含义"）。

**str 与 list[ContentBlock] 的语义分层**：
- 内部形态由开发方决定（与 API 提供方无关）：`str` = 纯文本便捷形态（等价 `list[TextBlock]`）；`list[ContentBlock]` = 富内容（工具调用等）。API 提供方只约束 wire 格式（OpenAI content 为 string/parts array、Anthropic content 恒为 blocks 数组），转换由 provider 适配器消化——同一份内部 str，发 OpenAI 是 `content: "文本"`，发 Anthropic 是 `content: [{type: "text", text: "文本"}]`
- `str`：Phase 1 全部调用方使用（summary service、/api/llm/test、Phase 2 记忆注入）
- `list[ContentBlock]`：Phase 1 无业务代码产生；Phase 2.1（tool_use/tool_result）与 Phase 3（agent loop）才产生（仍是开发方在 agent loop 实现中构造）。响应侧出现什么 block 由模型输出决定，但解析后的内部结构由我们定义
- 两 provider 的处理现状：anthropic.py 的 `_to_wire_message` 已正确处理两种形态；openai_compat.py 仅正确处理 str（list 为未定义行为，纯 text block 恰能被 OpenAI content parts 接受，tool_use 需走 tool_calls）——刻意推迟到 Phase 2.2 处理
- **不删除 str**：str 与 list 是并列形态、长期共存（类似 SDK 的 json=/data= 参数）。删除 str 需改模型+全部调用方+provider+测试，收益为零（每 provider 省一个 isinstance 分支）；纯文本场景（summary/llm test/记忆注入）用 str 最自然。Phase 2.2 重构方向是"补齐 list 处理"而非"移除 str"，重构后两 provider 均支持两种形态

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

两种协议对 system 的承载方式不同，差异收敛在各 provider 的 `_build_request` 内部：

- **Anthropic**：system 是顶层参数。从 messages 里抽出 `role=system` 的消息拼成顶层 `system`（多个 system 消息用 `\n\n` 连接）
- **OpenAI**：system 就是 messages 数组里的普通 `role=system` 消息，序列化无需任何特殊处理（现状代码 `{"role": m.role, "content": m.content}` 已正确，Phase 1 不改动）

### ContentBlock 设计含义（内部归一化模型）

- **没有统一的官方 protocol**。ContentBlock 是项目内部归一化模型（`app/services/llm/models.py` 中的 Pydantic union），不是外部标准。选择 Anthropic content block 形状的原因：① Anthropic 侧 1:1 映射、零转换成本；② OpenAI 的 wire 格式可无损转换到这个结构（拆并逻辑见 Phase 2.1）
- **Phase 1 只定义文本/思考类 block**：`text`、`thinking`、`redacted_thinking`。工具类 block（`tool_use`/`tool_result`）在 Phase 2.1 扩展 union——Pydantic union 追加类型向后兼容，不影响现有类型
- **未知 block 类型跳过不崩溃**：Phase 1 的 `_parse_response` 是通用解析器，若响应中出现未定义的 block 类型（如 `tool_use`），跳过该 block 并记录 warning，`content` 只取 text block。这是为 Phase 2.1/Phase 3 预置的容错行为，保证 Phase 1 期间 API 响应任何形态都不崩溃
- 当前业务代码（summary service）只用纯文本，`Message.content: str` 旧用法保持不变，不受影响

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

    # 与 OpenAICompatProvider 保持一致的分离参数签名，工厂 _build_provider 统一调用
    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        timeout: int = 60,
        proxy: str | None = None,
        thinking_level: str = "off",
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.proxy = proxy
        self.thinking_level = thinking_level

    # thinking_level → Anthropic budget_tokens 映射
    _THINKING_BUDGETS = {"off": 0, "low": 2048, "medium": 4096, "high": 8192}

    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        body = self._build_request(messages, **kwargs)
        # 复用 create_async_client，与 OpenAICompatProvider 一致支持 proxy/ssl_verify
        async with create_async_client(
            proxy=self.proxy,
            timeout=self.timeout,
            follow_redirects=True,
        ) as client:
            response = await client.post(
                f"{self.api_base}/messages",
                json=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        return self._parse_response(data)

    def _build_request(self, messages, **kwargs) -> dict:
        """内部模型 → Anthropic wire 格式"""
        system_parts = [
            m.content for m in messages if m.role == "system"
        ]
        body = {
            "model": kwargs.get("model", self.model),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "messages": [self._to_wire_message(m) for m in messages if m.role != "system"],
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        # thinking_level：每任务 kwargs 覆盖 > 全局默认；模型不支持时降级
        level = kwargs.get("thinking_level", self.thinking_level)
        budget = self._thinking_enabled(level, kwargs.get("model", self.model))
        if budget > 0:
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            body["temperature"] = 1  # Anthropic 要求 thinking 开启时 temperature 必须为 1
        return body

    def _thinking_enabled(self, level: str, model: str) -> int:
        """返回 budget_tokens；模型不支持或 level=off 时返回 0"""
        if model.startswith("claude-haiku"):
            logger.warning(f"model {model} 不支持 extended thinking，已降级为 off")
            return 0
        return self._THINKING_BUDGETS.get(level, 0)

    def _to_wire_message(self, m: Message) -> dict:
        """内部消息 → Anthropic wire 消息"""
        if isinstance(m.content, str):
            blocks = [{"type": "text", "text": m.content}]
        else:
            blocks = [block.model_dump(exclude_none=True) for block in m.content]
        return {"role": m.role, "content": blocks}

    def _parse_response(self, data: dict) -> ChatResponse:
        """Anthropic wire 格式 → 内部模型"""
        blocks: list[ContentBlock] = []
        text_parts: list[str] = []
        for block in data.get("content", []):
            btype = block.get("type")
            if btype == "text":
                blocks.append(TextBlock(text=block.get("text", "")))
                text_parts.append(block.get("text", ""))
            elif btype == "thinking":
                blocks.append(ThinkingBlock(
                    thinking=block.get("thinking", ""),
                    signature=block.get("signature"),
                ))
            elif btype == "redacted_thinking":
                blocks.append(RedactedThinkingBlock(data=block.get("data", "")))
            else:
                # 未知 block 类型（如 tool_use）：跳过 + warning，不崩溃（Phase 2.1 正式解析）
                logger.warning(f"未知 content block 类型 {btype!r}，已跳过")

        # Anthropic usage 字段映射：input_tokens → prompt_tokens, output_tokens → completion_tokens
        usage = None
        if "usage" in data:
            u = data["usage"]
            prompt = u.get("input_tokens", 0)
            completion = u.get("output_tokens", 0)
            usage = Usage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
            )

        return ChatResponse(
            content="".join(text_parts),
            blocks=blocks,
            stop_reason=data.get("stop_reason", ""),
            model=data.get("model", ""),
            usage=usage,
        )
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

### thinking 开关的两个维度：配置 + 模型能力

`thinking_level` 配置 ≠ 实际发送 thinking 参数。模型能力是第二道闸门：

- Anthropic 的 extended thinking 只被部分模型支持（claude-opus-4.x / claude-sonnet-4.x 等）；**claude-haiku-* 不支持**，传 `thinking` 参数 API 会报错
- Phase 1 实现**最小能力降级**：模型名以 `claude-haiku` 开头时，忽略 thinking 配置并记录 warning（行为等同 `off`）；未知模型按支持处理（让 API 报错自然暴露）

```python
def _thinking_enabled(self, level: str, model: str) -> int:
    """返回 budget_tokens；模型不支持或 level=off 时返回 0"""
    if model.startswith("claude-haiku"):
        logger.warning(f"model {model} 不支持 extended thinking，已降级为 off")
        return 0
    return self._THINKING_BUDGETS.get(level, 0)
```

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
| 修改 | `app/services/llm/models.py` | 扩展 Message 模型，新增 ContentBlock 类型（Text/Thinking/RedactedThinking） |
| 修改 | `app/services/llm/providers/base.py` | BaseProvider.chat() 签名不变，确认兼容 |
| 修改 | `app/services/llm/client.py` | `_PROVIDER_MAP` 新增 `anthropic_compat` 分支；`_build_provider` 为 anthropic 分支传 `thinking_level` |
| 修改 | `app/core/config.py` | `get_llm_config()` 默认值新增 `thinking_level="off"` |
| 修改 | `app/core/config_schema.py` | `[llm]` section 新增 `provider`/`thinking_level` 字段元数据 |
| 修改 | `app/models/summary.py` | `LLMConfigResponse`/`LLMConfigUpdate` 增加 `provider`、`thinking_level` |
| 修改 | `app/api/llm.py` | conf 接口透传新字段 |
| 修改 | `config.example.ini` | `[llm]` 段补 `provider`、`thinking_level` 注释与默认值 |
| 修改 | `templates/config/_llm.html` | Provider 下拉框 + 思考强度下拉框 |
| 修改 | `templates/config.html` | `loadLLMConfig`/`saveLLMAndTest` JS 处理新字段 |

> 总计：新增 1 个文件，修改 10 个文件
> 注意：`OpenAICompatProvider` 在 Phase 1 完全不动（工具拆并见 Phase 2.1，适配重构见 Phase 2.2）
> `_build_provider` 中 `thinking_level` 只传给 anthropic 分支（openai 分支构造函数无此参数，保持不动）

## 验证方式

1. 配置指向 Anthropic API（或兼容代理），调用 `/api/llm/test`，确认返回 "Hello" 响应
2. 切换到 Anthropic provider 后，手动触发一个 AI 总结任务，确认正常生成并发送通知
3. 设置 `thinking_level=medium`，确认响应中包含 thinking block 且不计入最终 content
4. `thinking_level` 缺省 `off` 时行为与现状完全一致（回归验证）
5. 模型名以 `claude-haiku` 开头 + `thinking_level=medium` 时，请求体不含 thinking 参数且记录 warning（降级验证）
6. 响应中出现未知 block 类型（如 `tool_use`）时，跳过并记录 warning，不崩溃（容错验证）
7. 保留 OpenAI 兼容模式，确认现有功能不受影响
8. 详细验收用例见 `agent-phase1-test-plan.md`（BDD，工具相关场景见 Phase 2.1/2.2 文档）
