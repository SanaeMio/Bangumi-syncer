# Phase 2.1: 工具调用协议支持

> 所属计划：Bangumi-Syncer Agent 化三步增量计划
> 前置依赖：Phase 1（ContentBlock 骨架 + 双 provider 结构）+ Phase 2（记忆，纯文本 chat 已就绪）
> 交付物：内部模型支持 ToolUse/ToolResult block，Anthropic 与 OpenAI 双协议可无损表达工具调用
> 执行时机：Phase 2 之后、Phase 2.2 之前（一个 phase 只做一件事）

## 目标

让内部中立模型具备**工具调用的表达力**，且两种协议都能无损承载：

- `Message.content` 支持 `ToolUseBlock` / `ToolResultBlock`
- Anthropic 侧：content blocks 1:1 映射（协议原生结构）
- OpenAI 侧：`tool_calls` / `tool` role 与内部模型的拆并转换
- 为 Phase 3 Agent 循环提供协议层基础（本 phase 不实现 Agent 循环）

## 范围

### 做什么

- `models.py` union 扩展：`ToolUseBlock`、`ToolResultBlock`
- `AnthropicProvider`：tool_use/tool_result 的 wire 转换（1:1）
- `OpenAICompatProvider`：tool 拆并转换 + `content` 为 list 时的防御
- `ChatResponse.blocks` 完整承载 tool block

### 不做什么

- 不实现 Agent 循环（Phase 3）
- 不实现工具注册表/工具执行（Phase 3）
- 不做 `thinking_level` → `reasoning_effort` 映射（Phase 2.2）
- 不重构 `OpenAICompatProvider.chat()` 结构（Phase 2.2）

## 模型扩展

```python
# app/services/llm/models.py（Phase 1 union 基础上追加两个类型）

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

ContentBlock = TextBlock | ThinkingBlock | RedactedThinkingBlock | ToolUseBlock | ToolResultBlock
```

Pydantic union 追加类型向后兼容：Phase 1 定义的类型不受影响，`Message` / `ChatResponse` 无需改动。

## 双协议转换

### Anthropic：1:1 映射（wire format 原生结构）

| 内部模型 | Anthropic wire |
|---|---|
| `ToolUseBlock(id, name, input)` | assistant content 里的 `{type: "tool_use", id, name, input}` |
| `ToolResultBlock(tool_use_id, content, is_error)` | user content 里的 `{type: "tool_result", tool_use_id, content, is_error}` |

- 一条内部 assistant 消息可含多个 ToolUseBlock → wire 中多个 tool_use block（并行工具调用）
- 一条内部 user 消息可含多个 ToolResultBlock → wire 中多个 tool_result block（放在同一 user 消息内）
- `_parse_response` 中 `tool_use` block → `ToolUseBlock`，`stop_reason = "tool_use"`

### OpenAI：拆并转换

**内部 → wire（`_build_request`）：**

1. **assistant 消息带 ToolUseBlock** → 单条 `{role: "assistant", content: null, tool_calls: [...]}`：
   - `tool_calls[].id` = `ToolUseBlock.id`
   - `tool_calls[].function.name` = `ToolUseBlock.name`
   - `tool_calls[].function.arguments` = `json.dumps(ToolUseBlock.input)`（JSON 字符串）
   - 消息中的 TextBlock 文本 → `content` 字段（OpenAI 允许 content 与 tool_calls 共存）
2. **user 消息带 ToolResultBlock** → 拆成多条 `{role: "tool", tool_call_id, content}`：
   - 一条 user 消息里的 N 个 ToolResultBlock → N 条 tool 消息
   - `tool_call_id` = `ToolResultBlock.tool_use_id`
   - `content` = `ToolResultBlock.content`（error 时 content 前缀 `[ERROR] `）
3. **`content` 为 list 时的防御**：若 user 消息 content 是 list 且含 text block 与 tool_result block 混排，text 部分先输出为独立 user 消息，tool_result 部分再拆为 tool 消息（OpenAI 要求 tool 消息紧跟在 assistant tool_calls 消息之后）

**wire → 内部（`_parse_response` + 历史消息合并）：**

1. 响应中 `message.tool_calls` → assistant 消息的 ToolUseBlock 列表；`finish_reason = "tool_calls"` → `stop_reason = "tool_use"`
2. `arguments` JSON 字符串解析为 dict；**解析失败兜底 `{"raw": "<原字符串>"}`**
3. 历史消息中连续的 `role=tool` 消息 → 合并回一条 user 消息的多个 ToolResultBlock

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `app/services/llm/models.py` | union 追加 ToolUseBlock / ToolResultBlock |
| 修改 | `app/services/llm/providers/anthropic.py` | tool block 1:1 转换 + 响应解析 |
| 修改 | `app/services/llm/providers/openai_compat.py` | 拆并转换 + content=list 防御 |

> 总计：修改 3 个文件（均是对 Phase 1 已新增/既有文件的增量扩展）

## BDD 测试场景

> 这些场景从 Phase 1 测试计划移入本 phase。测试方式：`_build_request` / `_parse_response` 是纯函数，直接构造内部消息喂入，不依赖业务场景触发（Phase 1 业务代码不产生工具消息；Phase 3 agent loop 才会产生）。

### Scenario T1（原 1.7）tool_use/tool_result 1:1 映射（Anthropic）
- **Given** 内部 assistant 消息含 `ToolUseBlock(id="u1", name="search", input={...})`
- **And** 内部 user 消息含 `ToolResultBlock(tool_use_id="u1", content="...")`
- **When** `AnthropicProvider._build_request`
- **Then** wire 格式 content blocks 与内部模型一一对应，无拆并

### Scenario T2（原 2.4）tool_use 响应解析
> **触发场景**：Phase 3 agent loop 传 tools 后才会收到；本 phase 用 mock 响应直接喂 `_parse_response`。

- **Given** mock 响应含 `{type: "tool_use", id: "u1", name: "search", input: {...}}`
- **When** `_parse_response`
- **Then** `blocks` 包含 `ToolUseBlock`、`stop_reason = "tool_use"`

### Scenario T3（原 3.1）内部 tool_use → assistant.tool_calls
- **Given** 内部 assistant 消息含 `ToolUseBlock`
- **When** `OpenAICompatProvider._build_request`
- **Then** wire 消息为 `{role: "assistant", content: null, tool_calls: [{id, type: "function", function: {name, arguments: <JSON字符串>}}]}`
- **And** `arguments` 是 JSON 字符串（与内部 dict 互转）

### Scenario T4（原 3.2）内部多个 tool_result → 多条 role=tool 消息
- **Given** 内部一条 user 消息含 2 个 `ToolResultBlock`
- **When** `_build_request`
- **Then** 拆成 2 条 `{role: "tool", tool_call_id, content}` 消息

### Scenario T5（原 3.3）反向：OpenAI tool_calls/tool 消息 → 内部模型
- **Given** OpenAI 响应含 `message.tool_calls` + `finish_reason: "tool_calls"`
- **And** 历史消息含连续 role=tool 消息
- **When** 解析/合并
- **Then** 合并回一条 user 消息的多个 `ToolResultBlock`
- **And** arguments JSON 字符串解析为 dict；解析失败兜底 `{"raw": "..."}`

### Scenario T6 并行工具调用（Anthropic 多 tool_use block）
- **Given** 内部 assistant 消息含 2 个 `ToolUseBlock`
- **When** `AnthropicProvider._build_request`
- **Then** wire 中 2 个 tool_use block 同消息共存

### Scenario T7 content 混排防御（OpenAI）
- **Given** 内部 user 消息 content 为 `[TextBlock, ToolResultBlock]`
- **When** `OpenAICompatProvider._build_request`
- **Then** text 部分输出为独立 user 消息，tool_result 部分拆为 tool 消息，顺序正确

## 验证方式

1. 单元测试：T1-T7 全部场景通过（纯函数测试，无网络依赖）
2. 回归：Phase 1 全部测试通过（union 扩展向后兼容，现有 Text/Thinking 行为不变）
3. 用 Anthropic API（或兼容代理）实测：传 `tools` 参数手工构造请求，确认模型返回 tool_use 且解析正确（可选，需 API key）
