# Phase 3: Agent 化 — AI 能力对外溢出

> 所属计划：Bangumi-Syncer Agent 化三步增量计划
> 前置依赖：Phase 1（ContentBlock 类型）+ Phase 2（记忆模块）
> 交付物：Agent 循环 + 工具调用 + 日志分析 + 知识沉淀（Hermes 模式）

## 目标

构建 Agent 循环、工具注册、知识沉淀三大能力。首个落地场景：**一键日志分析**。

用户在 Web UI 点击"分析日志"→ Agent 自主查询近期错误日志、调用 Bangumi API 检查服务状态、交叉比对 sync_records → 产出诊断报告 → 将解决方案沉淀到知识库 → 下次遇到类似问题时直接引用。

这就是 Hermes 模式：分析 → 诊断 → 沉淀 → 复用。

## Agent 循环

```
┌────────────────────────────────────────────────┐
│  Agent.run(task)                                │
│                                                 │
│  messages = [system_prompt, user_task]          │
│  while budget.can_continue():                   │
│      response = await llm.chat(messages)        │
│      if response.stop_reason == "end_turn":     │
│          return AgentResult(status="success")    │
│      if response.has_tool_calls():              │
│          for tc in response.tool_calls:         │
│              result = await tools.execute(tc)   │
│              messages.append(tool_result(tc, r))│
│      budget.consume(response.usage)             │
│      trace.record_step(response, results)       │
│  return AgentResult(status="exhausted")          │
└────────────────────────────────────────────────┘
```

## 模块结构

```
app/services/agent/
├── __init__.py           # Agent 主类
├── loop.py               # Agent 循环
├── budget.py             # 预算控制 (max_iterations, max_tokens, max_time)
├── trace.py              # 执行追踪 (内存中构建，完成后持久化)
├── tool_registry.py      # 工具注册表
├── tools/                # 内置工具定义
│   ├── __init__.py
│   ├── bangumi.py        # search_subject, get_episodes, set_watched
│   ├── sync.py           # query_history, retry_failed, get_candidates
│   ├── system.py         # check_health, get_errors, get_scheduler_status
│   └── memory.py         # search_knowledge_base (查询已沉淀的知识)
├── memory.py             # 记忆注入/提取 (Phase 2 已实现，Phase 3 扩展)
└── models.py             # AgentRun, ToolCall, ToolResult, AgentTrace
```

## 首个落地场景：日志分析 Agent

用户看到错误日志，点击 "AI 分析" → 触发以下流程：

```
Agent 收到任务: "分析最近 24 小时的错误日志，找出根因"

Step 1: Think → 需要先获取错误日志
        Act → tool: get_recent_errors(hours=24)
        Observe → 返回 15 条错误，主要是 "Bangumi API 503" 和 "标题匹配失败"

Step 2: Think → Bangumi API 503 可能是服务端问题，验证一下
        Act → tool: check_bangumi_api_health()
        Observe → API 当前正常 (200)，说明是间歇性故障

Step 3: Think → 标题匹配失败需要深入分析
        Act → tool: get_pending_candidates(status="pending")
        Observe → 3 个待处理候选，都是同一类标题格式

Step 4: Think → 已掌握全貌，产出一份诊断报告
        Act → 返回文本报告（不调用工具）

报告内容:
  - 根因 1: Bangumi API 间歇性 503，非我方问题，建议开启 retry queue
  - 根因 2: 特定标题格式匹配率低，建议添加映射规则
  - 建议: 下次遇到 503 可自动重试，标题问题可沉淀映射

Step 5: 用户点击 "采纳并记录" → 知识沉淀
        → 写入 knowledge_base 表：{error_pattern, diagnosis, solution, created_at}
        → 关联到 agent_working_memory（温层）
```

## 知识沉淀 (Hermes 模式)

```sql
CREATE TABLE knowledge_base (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    error_pattern TEXT NOT NULL,       -- 错误特征（用于匹配）
    diagnosis TEXT NOT NULL,           -- 诊断结果
    solution TEXT NOT NULL,            -- 解决方案
    category TEXT NOT NULL,            -- 'api_error', 'match_failure', 'config', ...
    severity TEXT DEFAULT 'medium',    -- 'low', 'medium', 'high', 'critical'
    run_id TEXT,                       -- 关联的 agent run
    resolved_count INTEGER DEFAULT 0,  -- 此方案成功解决的次数
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE knowledge_base_fts USING fts5(
    error_pattern, diagnosis, solution, category,
    content='knowledge_base',
    content_rowid='id'
);
```

工具定义：

```python
# Agent 可调用的知识库工具
tools.register(ToolDefinition(
    name="search_knowledge_base",
    description="搜索知识库，查找与当前错误模式匹配的历史诊断和解决方案",
    parameters={...},
    handler=knowledge_base_repo.search_fts,
))

tools.register(ToolDefinition(
    name="record_solution",
    description="将本次诊断的解决方案沉淀到知识库，供未来复用",
    parameters={...},
    handler=knowledge_base_repo.insert,
))
```

## 预算控制

```python
# app/services/agent/budget.py

@dataclass
class ThinkingBudget:
    max_iterations: int = 10
    max_total_tokens: int = 100_000
    max_wall_time_seconds: int = 300

    # 不同任务类型的默认预算
    PRESETS = {
        "sync":       dict(max_iterations=5,  max_total_tokens=50_000),
        "match":      dict(max_iterations=8,  max_total_tokens=80_000),
        "diagnostic": dict(max_iterations=15, max_total_tokens=150_000),
    }
```

## 追踪 (Trace)

```python
# app/services/agent/trace.py

@dataclass
class AgentTrace:
    run_id: str
    agent_name: str
    task: str
    steps: list[TraceStep]
    status: str  # 'running' | 'success' | 'failed' | 'exhausted'
    total_tokens: Usage
    wall_time_ms: float
```

写入 `agent_runs` + `agent_steps` 表，API 可查询完整执行过程。

## API 入口（通用设计）

Agent 执行采用通用 API，不绑定具体场景：

```python
# POST /api/agent/run
# 请求体
{
    "task": "分析最近 24 小时的错误日志，找出根因并给出解决方案",
    "tools": ["system", "sync"],      # 允许的工具分类，不传则全部可用
    "budget": "diagnostic",           # 预算档位: "quick" | "normal" | "diagnostic"
    "auto_commit": false              # 是否自动沉淀到知识库（默认 false，需用户确认）
}

# 响应 (202 Accepted)
{
    "run_id": "uuid",
    "status": "running"
}

# GET /api/agent/runs/{run_id}
# 响应
{
    "run_id": "uuid",
    "status": "success",
    "steps": [
        {"step": 1, "thinking": "...", "tool_calls": [...], "tool_results": [...]},
        ...
    ],
    "output": "诊断报告 Markdown...",
    "knowledge_candidates": [         # 建议沉淀的知识条目
        {"error_pattern": "Bangumi API 503", "solution": "..."}
    ],
    "usage": {"prompt_tokens": 500, "completion_tokens": 300}
}

# POST /api/agent/runs/{run_id}/commit-knowledge
# 用户确认后，将选中的知识条目写入知识库
{
    "knowledge_ids": [0, 2]
}
```

**前端实现搁置。** 先在 API 层把 Agent 跑通，可以通过 curl / Swagger UI 验证。后续再加 Web UI 入口。

## 前端入口（后续实现）

在 dashboard 或 logs 页面增加一个 "AI 分析" 按钮。触发通用 `/api/agent/run`，通过轮询或 SSE 追踪执行过程，展示思考步骤 + 诊断报告 + 知识沉淀确认。

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| **Agent 核心** | | |
| 新增 | `app/services/agent/__init__.py` | Agent 主类 |
| 新增 | `app/services/agent/loop.py` | Agent 循环 |
| 新增 | `app/services/agent/budget.py` | 预算控制 |
| 新增 | `app/services/agent/trace.py` | 执行追踪 |
| 新增 | `app/services/agent/tool_registry.py` | 工具注册表 |
| 新增 | `app/services/agent/models.py` | Agent 相关模型 |
| **内置工具** | | |
| 新增 | `app/services/agent/tools/__init__.py` | |
| 新增 | `app/services/agent/tools/bangumi.py` | Bangumi API 工具 |
| 新增 | `app/services/agent/tools/sync.py` | 同步管理工具 |
| 新增 | `app/services/agent/tools/system.py` | 系统诊断工具 |
| 新增 | `app/services/agent/tools/memory.py` | 知识库搜索工具 |
| **数据层** | | |
| 新增 | `app/core/database/agent_runs.py` | Agent 追踪数据 |
| 新增 | `app/core/database/knowledge_base.py` | 知识库 CRUD |
| 修改 | `app/core/database/connection.py` | `__ensure_agent_runs()` + `__ensure_knowledge_base()` |
| **API + UI** | | |
| 新增 | `app/api/agent.py` | Agent 通用执行/查询/知识沉淀 API |
| 修改 | `app/main.py` | 注册 agent router |
| **前端（Phase 3 暂不实现）** | | |
| — | `templates/` | 后续在 dashboard/logs 页面增加 AI 分析入口 |

> 总计：新增 ~14 个文件，修改 ~3 个文件（前端后续补）

## 验证方式

1. 构造已知故障场景（如故意用错误 API key 触发同步失败）
2. Web UI 点击 "AI 分析最近日志"
3. 确认 Agent 调用了 `get_recent_errors` → 识别出 API key 错误 → 产出诊断报告
4. 点击 "采纳并记录" → 确认写入 `knowledge_base`
5. 再次构造同样故障 → Agent 调用 `search_knowledge_base` → 找到之前的解决方案 → 直接复用
