---
title: 🤖 Agent 运行时与场景协议
order: 6
---

# 🤖 Agent 运行时与场景协议

LLM 匹配增强（以及未来的其它 Agent 场景）基于一层**通用运行时**：
`app/services/agent/` 承载与业务无关的 run 编排、恢复续跑状态机与观测设施；
具体业务（工具、种子、落库、通知）以「场景」形式接入。本文档给出分层、
`ScenarioHooks` 协议、扩展新场景的步骤与关键约束。

## 分层总览

| 层 | 模块 | 职责 |
| --- | --- | --- |
| 契约 | `agent/protocols.py`、`agent/chat_span.py` | 跨层能力契约（StreamFn / 执行器 / 记录器）与 chat span 契约 + 两实现（纯聚合 / 落库） |
| 循环 | `agent/loop.py` | 通用 LLM 循环 `run_loop`（工具调用、终止工具、预算注入、兜底收尾） |
| 预算 | `agent/budget.py` | 轮次预算策略（`task_type` + `thinking_level` → `max_iterations`） |
| 工具执行 | `agent/execution/tools.py`、`agent/execution/streaming.py` | 工具注册与执行（分区并行 / 停点提前执行） |
| 观测 | `agent/observe/trace.py`、`agent/observe/recorder.py` | span 读写与 `TraceRecorder`（chat/tool span、seed 行、budget 钩子、全轮 tokens 累计） |
| 运行编排 | `agent/run/runtime.py` | `run` 编排与 `continue_run` 恢复状态机（原子抢占 / 异常分流 / replay 分派 / 补执行 / 终局分派） |
| 场景协议 | `agent/run/scenario.py` | `ScenarioHooks`：场景差异点契约 |
| 注册表 | `agent/run/registry.py` | `get_scenario(task_type)`：调度器等通用组件按 task_type 取得场景运行入口（纯机制，由装配层登记） |
| 装配层 | `services/scenarios.py` | Composition Root：静态 import 场景工厂并 `wire_scenarios()` 登记进注册表 |
| 场景实现 | `matching/llm_assist.py` | 匹配场景：工具注册 / seed 构建 / 条目校验 / 候选落库 / 通知 / 业务键 |

## 代码地图（按执行顺序读）

1. `agent/run/registry.py` —— 调度器如何取到场景入口（`ScenarioRuntime` 门面 + 注册表）；
2. `agent/run/runtime.py` —— `run()`：原子抢占 → 场景 ctx/seed → 调 `run_loop` → 终局分派；
3. `agent/loop.py` —— `run_loop()`：每轮 `_consume_stream` → 终局归一化 → 工具执行 / 预算注入 → 兜底收尾；
4. `agent/chat_span.py` —— 每轮的聚合与落库契约（`async with` 单 finalize，`resp` 与落库同源）；
5. `agent/observe/recorder.py` + `agent/observe/trace.py` —— span 落库与 `replay()` 重建；
6. `agent/execution/tools.py` + `agent/execution/streaming.py` —— 工具注册与执行（分区并行 / 停点提前执行）；
7. `agent/run/scenario.py` —— 场景契约（hooks / ctx 泛型 / terminal handler）。

`agent/__init__.py` 的 docstring 里也放了同一份包地图。

## ScenarioHooks 协议（`agent/run/scenario.py`）

| 字段 | 说明 |
| --- | --- |
| `task_type` | 任务类型（观测/日志前缀用，如 `"match"`） |
| `register_tools(registry, ctx)` | 注册场景工具，返回 `ToolDefinition` 列表 |
| `build_seed(ctx)` | 构建种子消息（system + user） |
| `build_stream_fn(thinking_level)` | 构建默认流式 LLM 函数（场景决定 job 归属与思考强度透传） |
| `resolve_thinking_level()` | 从场景集中配置读取思考强度（恢复路径使用） |
| `resolve_max_iterations(thinking_level)` | 解析轮次预算（含显式覆盖；单一来源由场景保证） |
| `handle_terminal(dbm, run_id, result, ctx, *, total_tokens, notification_service)` | 终局处理：校验 / 落库 / 通知，返回终态 status |
| `on_run_failed(failure)` | 终态失败回调（失败判定点由 runtime 触发，不经 `handle_terminal`）：场景在此处理通知等职责；`None` = 不处理 |

`ctx` 为场景上下文（runtime **原样透传、不感知其结构**）；匹配场景使用
`_MatchContext(sync_record, bgm)`。

## 运行流程

### run（正常路径）

1. 原子抢占 run（`atomic_claim`，失败返回 `"skipped"`）；
2. per-run `ToolRegistry` + 场景工具注册（并发隔离）；
3. `build_seed` → 写 seed span；`resolve_max_iterations` 解析预算；
4. `loop.py` 编排（每轮经 `recorder.chat_span()` 单点聚合与落库；
   tool span / 预算消息由 recorder 记录）；
5. LLM 异常分流：`LLMCallError(retryable=False)` → `failed(llm_error)`；
   可重试 → `increment_attempts`（≥3 由仓储单点转 failed）；
6. 终局交 `handle_terminal`。

#### 轮次预算与收尾

- **预设映射**（`MatchIterationStrategy.PRESET`）：`off=1 / low=2 / medium=5 / high=10`；
  未知 `thinking_level` 回落 `medium`（5）。未注册 `task_type` 仍走保守默认兜底
  （`off=1 / low=2 / medium=3 / high=5`）。
- **末轮强化提示**：`remaining == 1` 时预算消息不再是朴素 `[剩余轮次：1]`，而是
  `FINAL_ROUND_BUDGET_MESSAGE`——明确「最后一轮 / 必须调用 `submit_suggestion` 给出结论
  （推荐或明确放弃）/ 不得再调用检索工具」。用于对冲供应商把强制 `tool_choice=terminal`
  降级为 `auto` 导致思考模型不提交的情况。
- **兜底收尾调用**：`for` 循环自然结束（即将返回 `exhausted`）时，追加**恰好一次**
  收尾 LLM 调用（仅 live 路径）：
  - 追加 `FINAL_RECOVERY_MESSAGE` 并经 `recorder.record_budget(...)` 记录（重放一致性）；
  - `tools` 过滤为**仅** terminal schema，`tool_choice=terminal`；
  - 含 terminal tool_call → `submit_suggestion`；否则 `exhausted`（`last_response` 为收尾响应）；
  - 收尾 LLM 调用抛异常 → best-effort 降级为 `exhausted`（保留循环内最后一次响应），不重试。

### continue_run（恢复路径，唯一公开续跑入口）

1. 场景解析 thinking_level / max_iterations；
2. `trace.replay` 从 `agent_steps` 重建消息与终局响应；
3. **终局优先**（先于预算耗尽判定）按 `last_response` 分派：
   - `end_turn` → `mark_succeeded`（stop_reason=`end_turn`）；
   - 含终止工具调用（注册表 `access="terminal"` 派生）→ 场景 `handle_terminal`（tokens 取 replay 累计）；
   - `None`（轮次已完整记录）→ 预算耗尽则 `succeeded(stop_reason=exhausted)`，否则续跑 loop；
   - 含 tool_use → 补执行缺失只读工具（写 tool span 自包含）→ 预算够则续跑 loop；
4. 非终局且 `remaining <= 0` → `succeeded(stop_reason=exhausted)`（避免 run 永久滞留 `processing`）；
5. 异常在函数内部消化（不向调度器抛出）。

> 顺序说明：末轮可能已产出 `submit` 但 run 中断，若先判预算耗尽会误判 `exhausted` 丢失提交，
> 故终局语义必须先消费。
>
> 边界：**兜底收尾调用仅存在于 live 路径**（`loop.run_loop`）。恢复路径预算耗尽时不再发起
> 收尾 LLM 调用，而是直接按终局语义落终态（`handle_terminal` / `mark_succeeded`）。
>
> 中断轮次的观测：replay 在该轮 break 时，其 `llm_chat` span 可能仍是 `running`
> （进程被杀/重启）。续跑前会调用 `trace.reopen_interrupted_chat_span` 复用同一行
> （重置为初始态，后续 `end_span` 闭合同一行），避免 UI 出现同轮「未闭合 + 重跑」两条
> 记录；已完整记录的轮次不受影响。若恢复失败或 run 已终态仍有残留，
> `mark_residual_running_spans` 在启动与调度每轮兜底标记为 error。

## 观测：chat span 单聚合契约

每轮 LLM 调用的响应**只聚合一次、只落一次 span**。`loop` 每轮经
`recorder.chat_span()` 进入本轮 `async with` 上下文（`recorder=None` 时为
`PlainChatSpan` 纯聚合，控制流不变）：

```python
async with recorder.chat_span() as span:
    async for chunk in stream_fn(messages, tools=..., tool_choice=...):
        span.feed(chunk)                  # 唯一聚合点
        if executor is not None:
            executor.feed(chunk)          # 工具事件并行喂执行器
resp = span.resp                          # __aexit__ 已完成唯一一次 finalize
```

| 契约点（`protocols.ChatSpan`） | 语义 |
| --- | --- |
| `__aenter__` | 落本轮 span 起点：iteration 进位、`_current_iteration` 就位（流进行中提前执行的 tool span 靠它同轮 + 同 parent） |
| `feed(chunk)` | **唯一**聚合点（调用方不再另建 `StreamAggregator`） |
| `__aexit__` | 正常退出：做**唯一**一次 `StreamAggregator.finalize()` 并写 span 终态；异常退出：写 error span、**不** finalize、原异常透传（落库自身失败只记 warning，不遮蔽主流程） |
| `resp` | 在 `async with` **块外**读取；loop 控制流与 `replay_delta` 落库内容同源 |

两条硬约束（放宽会破坏恢复语义）：

- **CM 粒度是「每轮」而非「每 run」**：loop 必须在流结束、下一轮开始前消费 resp
  做控制流决策；且 chat span 的 INSERT（轮首）/ UPDATE（轮末）是 `trace.replay`
  的可重放锚点，攒到 run 末统一入库会让恢复看不到已发生轮次（全量重跑，工具副作用
  重复执行）。
- **轮次编号由 recorder 自持**：`chat_span()` 不接收调用方下标——续跑从
  `executed_iterations` 起算、补执行轮经 `begin_replayed_round` 再偏移，loop 本地
  轮次与持久化历史编号不同源；撞号会让 replay 按 `iteration` 分组时静默丢响应。

守卫：`tests/services/agent/test_chat_span.py`（每轮 finalize 恰好一次、控制流与落库
同源、流异常 error span、收尾轮 iteration 顺延、无 recorder 纯聚合）、
`tests/services/agent/test_protocols.py`（协议面 + 实现显式继承）、
`tests/core/test_dead_code_removed.py`（`wrap_stream_fn` 不回归）。

### 展示层：预览与按需展开

- `GET /api/agent/runs/{run_id}/steps` 是 **2KB 预览**：`display_json` 超限截断并带
  `...[shrinked]`；每步另带 `thinking` 摘要（300B）与 `thinking_truncated` 标记。
- `GET /api/agent/runs/{run_id}/steps/{span_id}` 是**单 span 详情**（前端「展开」按需加载）：
  完整 `display_json` 与 `thinking`（各 32KB 上限，超限截断），鉴权复用 `_load_authorized_run`。
- `thinking` 来源：`replay_delta.response.blocks` 中 `type='thinking'` 的文本按序拼接；
  `redacted_thinking` 以 `[已脱敏]` 占位；旧数据无 `blocks` 时为空串（兼容）。

## 新增一个 Agent 场景

1. 实现 `ScenarioHooks` 各钩子（工具注册 / 默认 `stream_fn` / 预算 / 终局，可选
   `veto_terminal` 软护栏）与 `ScenarioRuntime.new_ctx`（run 输入 → 场景 ctx）；
2. 提供工厂 `get_scenario_runtime() -> ScenarioRuntime`（`agent/run/registry.py`
   中的类型）；
3. 在**应用装配层** `app/services/scenarios.py::wire_scenarios()` 中**静态
   import** 该工厂并 `register_scenario(task_type, factory())`；
4. 调度/触发入口按 `get_scenario(task_type).run / .continue_run` 调用，
   不 import 场景模块内部。

装配触发点：生产由 `scheduler_bootstrap.register_all()` 调用一次；测试由
`tests/conftest.py` 调用一次（保证测试中 `get_scenario("match")` 可用）。

> 采用**静态装配（Composition Root）**而非注册表内字符串路径表惰性导入，动机：
> 场景工厂调用对静态分析 / LSP 可见；注册表保持纯机制、不感知具体场景；装配层
> 位于应用层，通用层（`agent/`）不反向依赖场景层。有 AST 守卫测试防回归
> （禁止 `importlib` / 字符串提供者表），并有子进程导入顺序测试保证无循环导入。

## 约束与约定

- **恢复续跑收敛为单一入口 `continue_run`**：调度器只调用公开入口，不触碰场景
  私有符号（有源码级守卫测试）。
- **超时闭环**：`llm_match` 调度器为单 run 施加**动态超时**（`compute_match_run_timeout`：
  `max_iterations × ([llm] timeout + 工具 30s) + 缓冲 120s`），并为整轮加 tick 兜底
  （`run_timeout + 60s`）。超时由 `asyncio.wait_for` 取消协程 → 恢复路径的 `finally`
  释放进程内执行权 → run 保持 `processing` → 下一 tick 恢复扫描断点续跑；APScheduler/cron
  结构不变（超时值动态推算，不新增配置项）。
- **调度器不依赖场景模块**：经 `agent.registry.get_scenario(task_type)` 获取
  `ScenarioRuntime`（组合模式）；新增任务类型时调度骨架可复用。
- **场景钩子实现内部以场景模块全局名引用依赖**（而非闭包硬绑定），保持可
  mock / patch / 替换（既有测试依赖该约定）。
- **失败语义**（run / continue 一致）：确定性 LLM 失败 → 立即 `failed`；
  可重试失败 → 累计 attempts；其它异常 → 日志 + attempts。
- **终止工具单一来源**：由注册表 `access="terminal"` 派生（**至多一个**，多则
  runtime 入口 fail-fast；`ScenarioHooks` 不承载工具名，`loop` 从 `ToolSchema` 自行
  派生）。末轮强制 `tool_choice`、终止判定、收尾轮 tools 过滤均只依赖它；
  `RunResult.stop_reason` 为**实际终止工具名**（骨架不硬编码场景名）。
- 运行时日志前缀由 `hooks.task_type` 派生（如 `[match]`）。
- 事务 / 落库 / 通知属场景职责；runtime 只透传 `ctx` 与 `notification_service`。
- **导入风格（agent 生态）**：禁止 `as` 别名（`from X import Y as Z` / `import X as Y`）。
  依据：ty 的 go-to-definition / go-to-declaration 会**跳过 `as` 绑定点**直奔原定义
  （[astral-sh/ty#1592](https://github.com/astral-sh/ty/issues/1592)，maintainer 提交、未修；
  实测 ty 0.0.84：references / rename 对别名处理正常）——多级别名下「这个名字从哪来」
  无法一跳看到，评审与排障都要多跳一层。需要避让时改**本地名字**（参数 / 变量）；
  patch 目标用字符串路径（`patch("a.b.c.fn", ...)`）；需要「留真身」（patch 后仍调原函数）
  时用**本地赋值** `real = module.func`。有 AST 守卫 `tests/services/agent/test_import_style.py`
  （覆盖 `app/`、`eval/`、`tests/` 中所有 agent 相关导入）。

## 相关文件与测试

| 主题 | 位置 |
| --- | --- |
| 运行编排 / 场景协议 / 注册表 | `app/services/agent/run/runtime.py`、`run/scenario.py`、`run/registry.py` |
| 观测（chat span 契约 / tool span / seed / budget） | `app/services/agent/observe/recorder.py`、`observe/trace.py`、`chat_span.py`、`protocols.py`、`tests/services/agent/test_chat_span.py` |
| 匹配场景实现 | `app/services/matching/llm_assist.py` |
| 场景测试 | `tests/services/matching/test_llm_assist.py` |
| 工具执行 | `app/services/agent/execution/tools.py`、`execution/streaming.py` |
| 调度器与守卫 | `app/services/llm_match_scheduler.py`、`tests/services/test_llm_match_scheduler.py` |
| 待办（终局策略拆分等） | `remain/agent_phase3_deferred_design.md`（本地归档，不入库） |

> 历史：`llm_assist.py` 曾混居通用状态机与匹配业务（1583 行）；commit `2cda3bb`
> 将 `TraceRecorder` 迁入 `agent/recorder.py`，`5110e46` 抽取 `agent/runtime.py`
> 与 `ScenarioHooks` 协议，场景层收缩为「匹配业务 + 运行入口工厂」。
> （后续重组：`recorder.py`/`trace.py` 迁入 `agent/observe/`，`runtime.py`/`scenario.py`/
> `registry.py` 迁入 `agent/run/`，`tools.py`/`streaming_tool_executor.py` 迁入 `agent/execution/`。）
