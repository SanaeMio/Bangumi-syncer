# Phase 1 BDD 测试计划 — Anthropic 协议支持

> 所属计划：Bangumi-Syncer Agent 化三步增量计划
> 范围：Phase 1 全部功能（Anthropic 协议 + thinking_level + 前端配置）
> 方式：BDD（Given/When/Then），按现有测试惯例组织（unittest.mock + _make_mock_client 模式）

## 测试文件布局

| 测试文件 | 覆盖对象 | 新增/修改 |
|---|---|---|
| `tests/services/llm/test_models.py` | ContentBlock 模型扩展 | 修改 |
| `tests/services/llm/test_provider_anthropic.py` | AnthropicProvider（新增） | 新增 |
| `tests/services/llm/test_provider_openai_compat.py` | tool_use/tool_result 拆并、reasoning_effort | 修改 |
| `tests/services/llm/test_client.py` | anthropic_compat 工厂分支、thinking_level kwargs | 修改 |
| `tests/core/test_config_llm.py` | get_llm_config 默认值（provider/thinking_level） | 修改 |
| `tests/api/test_summary.py` | /api/llm/conf 存取新字段 | 修改 |
| `tests/e2e/test_config_llm.py` | 前端表单（Playwright） | 新增 |

---

## Feature 1: Anthropic 协议请求构建

### Scenario 1.1 纯文本请求符合 Messages API 格式
- **Given** 配置 `provider=anthropic_compat`、`api_base=https://api.anthropic.com/v1`
- **And** 调用 `chat([Message(role="user", content="Hello")])`
- **When** 发送 POST `/v1/messages`
- **Then** 请求体包含 `model`、`max_tokens`、`messages[0] = {role: "user", content: [{type: "text", text: "Hello"}]}`
- **And** 请求体不含 `temperature` 覆盖逻辑错误（保留配置值）

### Scenario 1.2 system prompt 提升为顶层参数
- **Given** 消息列表包含 `Message(role="system", content="你是追番助手")` 和 user 消息
- **When** 构建请求体
- **Then** 请求体顶层 `system = "你是追番助手"`
- **And** `messages` 数组中不包含 role=system 的消息

### Scenario 1.3 多条 system 消息合并
- **Given** 两条 system 消息：`"规则A"` 和 `"规则B"`
- **When** 构建请求体
- **Then** 顶层 `system = "规则A\n\n规则B"`

### Scenario 1.4 thinking_level 映射为 budget_tokens
- **Given** `thinking_level=medium`（或 kwargs 覆盖 `thinking_level="high"`）
- **When** 构建请求体
- **Then** 请求体包含 `thinking = {type: "enabled", budget_tokens: 4096}`（high 时为 8192）
- **And** `temperature` 被强制为 `1`

### Scenario 1.5 thinking_level=off 不传 thinking（默认最弱）
- **Given** 未配置 `thinking_level`（缺省 off）
- **When** 构建请求体
- **Then** 请求体不含 `thinking` 字段
- **And** `temperature` 保持配置值不变

### Scenario 1.6 per-call 覆盖全局默认
- **Given** 全局 `thinking_level=off`
- **When** 调用 `chat(messages, thinking_level="high")`
- **Then** 本次请求体含 `thinking.budget_tokens=8192`
- **And** 未传 kwargs 的调用仍走全局 off

### Scenario 1.7 tool_use/tool_result 1:1 映射（Anthropic）
- **Given** 内部 assistant 消息含 `ToolUseBlock(id="u1", name="search", input={...})`
- **And** 内部 user 消息含 `ToolResultBlock(tool_use_id="u1", content="...")`
- **When** 构建请求体
- **Then** wire 格式 content blocks 与内部模型一一对应，无拆并

---

## Feature 2: Anthropic 协议响应解析

### Scenario 2.1 纯文本响应解析
- **Given** 响应 JSON：`choices 风格反例`（即 Anthropic 的 `content: [{type: "text", text: "你好"}]` + `usage` + `stop_reason: "end_turn"`）
- **When** `_parse_response(data)`
- **Then** `ChatResponse.content = "你好"`
- **And** `blocks[0]` 为 `TextBlock`
- **And** `stop_reason = "end_turn"`、`usage` 正确填充

### Scenario 2.2 thinking block 解析但不进入 content
- **Given** 响应含 `{type: "thinking", thinking: "..."}` 和 `{type: "text", text: "答案"}`
- **When** 解析
- **Then** `content = "答案"`（thinking 不计入）
- **And** `blocks` 包含 `ThinkingBlock`

### Scenario 2.3 redacted_thinking block 容错
- **Given** 响应含 `{type: "redacted_thinking", data: "..."}`
- **When** 解析
- **Then** 不抛异常，解析为 `RedactedThinkingBlock`

### Scenario 2.4 tool_use 响应解析
- **Given** 响应含 `{type: "tool_use", id: "u1", name: "search", input: {...}}`
- **When** 解析
- **Then** `blocks` 包含 `ToolUseBlock`、`stop_reason = "tool_use"`

### Scenario 2.5 API 错误响应抛异常
- **Given** 服务返回 4xx/5xx
- **When** `chat()`
- **Then** 抛出 `httpx.HTTPStatusError`
- **And** `LLMClient` 按既有逻辑重试并记录错误

---

## Feature 3: OpenAI 兼容协议适配转换

### Scenario 3.1 内部 tool_use → assistant.tool_calls
- **Given** 内部 assistant 消息含 `ToolUseBlock`
- **When** `OpenAICompatProvider._build_request`
- **Then** wire 消息为 `{role: "assistant", content: null, tool_calls: [{id, type: "function", function: {name, arguments: <JSON字符串>}}]}`
- **And** `arguments` 是 JSON 字符串（与内部 dict 互转）

### Scenario 3.2 内部多个 tool_result → 多条 role=tool 消息
- **Given** 内部一条 user 消息含 2 个 `ToolResultBlock`
- **When** `_build_request`
- **Then** 拆成 2 条 `{role: "tool", tool_call_id, content}` 消息

### Scenario 3.3 反向：OpenAI tool_calls/tool 消息 → 内部模型
- **Given** OpenAI 响应含 `message.tool_calls` + `finish_reason: "tool_calls"`
- **And** 历史消息含连续 role=tool 消息
- **When** 解析/合并
- **Then** 合并回一条 user 消息的多个 `ToolResultBlock`
- **And** arguments JSON 字符串解析为 dict；解析失败兜底 `{"raw": "..."}`

### Scenario 3.4 reasoning_effort 映射
- **Given** `thinking_level=high`
- **When** 构建请求体（o 系列模型）
- **Then** 请求体含 `reasoning_effort: "high"`
- **Given** `thinking_level=off`
- **When** 构建请求体
- **Then** 请求体不含 `reasoning_effort`（非 o 系列模型不受影响）

---

## Feature 4: 消息模型扩展

### Scenario 4.1 ContentBlock 构造与校验
- **Given** 各类型 ContentBlock（Text/ToolUse/ToolResult/Thinking/RedactedThinking）
- **When** 实例化
- **Then** `type` 字段校验正确，非法 type 抛 ValidationError

### Scenario 4.2 Message 兼容旧用法
- **Given** `Message(role="user", content="纯文本")`（旧用法）
- **When** 访问 `content`
- **Then** 类型为 str，行为与改造前一致
- **Given** `Message(role="assistant", content=[ContentBlock...])`（新用法）
- **When** 访问 `content`
- **Then** 类型为 list[ContentBlock]

### Scenario 4.3 ChatResponse 向后兼容
- **Given** 仅填充 `content`/`model`/`usage`（旧用法）
- **When** 构造 ChatResponse
- **Then** `blocks=[]`、`stop_reason=""` 缺省值可用
- **And** summary 服务现有代码无需改动

---

## Feature 5: LLMClient 工厂与重试

### Scenario 5.1 anthropic_compat 分支实例化
- **Given** 配置 `provider=anthropic_compat`
- **When** `get_llm_client()`
- **Then** `_provider` 为 `AnthropicProvider` 实例
- **And** 参数（api_base/api_key/model/max_tokens/timeout/proxy）正确传入

### Scenario 5.2 非法 provider 报错
- **Given** 配置 `provider=unknown_provider`
- **When** 构建 provider
- **Then** 抛 ValueError 且提示支持的 provider 列表

### Scenario 5.3 重试与用量记录
- **Given** AnthropicProvider 首次调用抛 5xx
- **When** `LLMClient.chat()`
- **Then** 重试 2 次（1s/3s 退避）
- **And** 成功时 `llm_usage_logs` 写入 `provider=anthropic_compat`
- **And** 全部失败时写入 error 记录并返回空 ChatResponse

### Scenario 5.4 thinking_level kwargs 透传
- **Given** `chat(messages, thinking_level="high")`
- **When** 调用
- **Then** kwargs 透传到 provider，请求体含 thinking 配置

---

## Feature 6: LLM 配置 API

### Scenario 6.1 保存 provider 与 thinking_level
- **Given** 已登录用户
- **When** `PUT /api/llm/conf` body 含 `{provider: "anthropic_compat", thinking_level: "medium"}`
- **Then** 写入 `[llm]` section
- **And** `get_llm_config()` 返回新值
- **And** `llm_client` 单例被重置（下次调用用新配置）

### Scenario 6.2 读取配置回显
- **Given** 已保存 `thinking_level=high`
- **When** `GET /api/llm/conf`
- **Then** 响应含 `thinking_level: "high"` 与 `provider`
- **And** api_key 仍为掩码值 `***xxxx`

### Scenario 6.3 缺省值
- **Given** 从未配置 `thinking_level`
- **When** `GET /api/llm/conf`
- **Then** 返回 `thinking_level: "off"`、`provider: "openai_compat"`

### Scenario 6.4 连接测试
- **Given** `provider=anthropic_compat` 且指向兼容端点
- **When** `POST /api/llm/test`
- **Then** 返回 `success=True` 且含 model/latency

---

## Feature 7: 前端配置表单（Playwright E2E）

### Scenario 7.1 Provider 下拉框
- **Given** 登录后打开配置页
- **When** 渲染 LLM 配置卡片
- **Then** 显示 Provider 下拉框，选项为 `openai_compat` / `anthropic_compat`，默认 `openai_compat`

### Scenario 7.2 思考强度下拉框
- **Given** 配置页 LLM 卡片
- **When** 渲染
- **Then** 显示思考强度下拉框，选项为 关/低/中/高，默认 关

### Scenario 7.3 保存并测试
- **Given** 选择 `anthropic_compat` + 思考强度 `高`，填写 api_base/api_key/model
- **When** 点击"保存并测试"
- **Then** `PUT /api/llm/conf` 请求携带 `provider` 与 `thinking_level=high`
- **And** `/api/llm/test` 使用新 provider 返回成功
- **And** 页面展示测试结果

### Scenario 7.4 缺省值回显
- **Given** 从未配置 LLM
- **When** 打开配置页
- **Then** Provider 下拉框显示 `openai_compat`、思考强度显示 `关`

---

## 执行方式

```bash
# 单元 + 集成（无网络、无 LLM key）
uv run pytest tests/services/llm/ tests/core/test_config_llm.py tests/api/test_summary.py -q

# E2E（需 Playwright + 本地服务）
uv run pytest tests/e2e/test_config_llm.py -q

# 全量回归（确认零破坏）
uv run pytest tests/ -q
```

## 通过标准

- Feature 1-6 全部场景在 PR 中通过（无 LLM key 依赖）
- Feature 7 E2E 场景在本地验证（Playwright）
- 现有测试全量通过（向后兼容零回归）
