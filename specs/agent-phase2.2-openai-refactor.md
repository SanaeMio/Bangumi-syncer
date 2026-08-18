# Phase 2.2: OpenAI 兼容模式重构

> 所属计划：Bangumi-Syncer Agent 化三步增量计划
> 前置依赖：Phase 1 + Phase 2 + Phase 2.1（工具拆并已就绪）
> 交付物：`OpenAICompatProvider` 适配内部中立模型结构，支持 `thinking_level` → `reasoning_effort`
> 执行时机：Phase 2.1 之后、Phase 3 之前（一个 phase 只做一件事）

## 目标

让 `OpenAICompatProvider` 与 `AnthropicProvider` 结构对齐，并响应统一的 `thinking_level` 配置：

- `chat()` 重构为 `_build_request(messages, **kwargs)` + `_parse_response(data)` 结构（与 Anthropic 侧对称）
- `thinking_level` → `reasoning_effort` 映射（OpenAI o 系列模型）
- `content` 为 list 时的防御逻辑归位（Phase 2.1 已做拆并，此处统一入口）

## 为什么放在最后做

Phase 2.1 已经让 openai 侧具备工具拆并能力（那是工具调用支持的必需部分）。剩余部分——结构重构与 reasoning_effort——是纯粹的代码质量与配置对齐工作，对功能没有增量价值，放最后让前面各 phase 的测试先充分回归。

## 范围

### 做什么

- `chat()` 拆分为 `_build_request` / `_parse_response` 两个私有方法（现状是 chat() 内联拼 body + 解析）
- `thinking_level` → `reasoning_effort` 映射：

| thinking_level | OpenAI wire |
|---|---|
| `off`（缺省） | 不传 `reasoning_effort`（= 现状行为） |
| `low` | `reasoning_effort="low"` |
| `medium` | `reasoning_effort="medium"` |
| `high` | `reasoning_effort="high"` |

- **模型能力降级**：`reasoning_effort` 只对 o 系列模型有效（模型名以 `o` 开头，如 `o3`、`o4-mini`）。非 o 系列模型忽略配置并记录 warning（OpenAI 对未知参数的行为因 API 版本而异，不冒险传给非 o 系列）

### 不做什么

- 不改 `AnthropicProvider`（Phase 1 已定型）
- 不改工具拆并逻辑（Phase 2.1 已定型）
- 不实现 Agent 循环（Phase 3）

## 重构结构

```python
# app/services/llm/providers/openai_compat.py（Phase 2.2 后）

class OpenAICompatProvider(BaseProvider):
    def __init__(self, api_base, api_key, model, max_tokens, temperature, timeout, proxy):
        # 与现状一致；新增 thinking_level 由 _build_request 从 kwargs 读取
        ...

    _REASONING_EFFORTS = {"off": None, "low": "low", "medium": "medium", "high": "high"}

    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        body = self._build_request(messages, **kwargs)
        # 发送 POST {api_base}/chat/completions
        data = ...
        return self._parse_response(data)

    def _build_request(self, messages, **kwargs) -> dict:
        """内部模型 → OpenAI wire 格式"""
        body = {
            "model": kwargs.get("model", self.model),
            "messages": [self._to_wire_message(m) for m in messages],
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
        }
        # thinking_level：每任务 kwargs 覆盖 > 全局默认；非 o 系列降级
        level = kwargs.get("thinking_level", self.thinking_level)
        model = body["model"]
        effort = self._reasoning_effort(level, model)
        if effort:
            body["reasoning_effort"] = effort
        return body

    def _reasoning_effort(self, level: str, model: str) -> str | None:
        """返回 reasoning_effort；非 o 系列模型或 off 时返回 None"""
        effort = self._REASONING_EFFORTS.get(level)
        if effort and not model.startswith("o"):
            logger.warning(f"model {model} 不支持 reasoning_effort，已忽略 thinking_level={level}")
            return None
        return effort

    def _parse_response(self, data: dict) -> ChatResponse:
        """wire → 内部模型（现状解析逻辑 + tool_calls 处理，Phase 2.1 已实现拆并）"""
        ...
```

`_to_wire_message` / 拆并逻辑（Phase 2.1）保持不变，只是移动到 `_build_request` 内部调用。

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `app/services/llm/providers/openai_compat.py` | chat() 拆分为 _build_request/_parse_response；reasoning_effort 映射 + 降级 |

> 总计：修改 1 个文件

## BDD 测试场景

### Scenario O1（原 3.4）reasoning_effort 映射
- **Given** `thinking_level=high`，模型为 o 系列（如 `o4-mini`）
- **When** `_build_request`
- **Then** 请求体含 `reasoning_effort: "high"`
- **Given** `thinking_level=off`
- **When** `_build_request`
- **Then** 请求体不含 `reasoning_effort`（非 o 系列模型不受影响）

### Scenario O2 非 o 系列模型降级
- **Given** `thinking_level=high`，模型为 `gpt-4o-mini`（非 o 系列）
- **When** `_build_request`
- **Then** 请求体不含 `reasoning_effort`，且记录 warning

### Scenario O3 kwargs 覆盖全局默认
- **Given** 全局 `thinking_level=off`
- **When** `chat(messages, thinking_level="high")`，模型 o 系列
- **Then** 本次请求体含 `reasoning_effort: "high"`

### Scenario O4 重构后行为不变（回归）
- **Given** 纯文本消息（Phase 1/2 业务场景）
- **When** `chat()`（重构后）
- **Then** 请求体与重构前完全一致（无 reasoning_effort、无 tool 字段、temperature 走 kwargs 覆盖）

## 验证方式

1. 单元测试：O1-O4 全部场景通过
2. 回归：Phase 1/2/2.1 全部测试通过（`OpenAICompatProvider` 现有测试同步更新到 `_build_request`/`_parse_response` 结构）
3. 实测：openai_compat 指向真实 API，AI 总结任务正常（可选，需 API key）
