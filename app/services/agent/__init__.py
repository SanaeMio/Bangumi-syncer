"""通用 Agent 层（与业务无关）：循环骨架 / 观测 / 运行编排 / 工具执行。

包地图（按执行顺序阅读）：

- ``protocols.py``：跨层能力契约（StreamFn / StreamingExecutor / ToolSpanRecorder / BudgetRecorder）
- ``chat_span.py``：chat span 契约 + 两种实现（纯聚合 / 落库）
- ``loop.py``      循环骨架：``run_loop``（每轮经 chat span 单点聚合）
- ``budget.py``    轮次预算策略 + 超时推算
- ``execution/``   工具执行：``tools``（注册/执行）、``streaming``（流式执行器）
- ``observe/``     观测：``trace``（span 读写 + replay）、``recorder``（TraceRecorder）
- ``run/``         运行编排：``runtime``（run/continue_run）、``scenario``（场景契约）、
                   ``registry``（注册表 + ``ScenarioRuntime`` 入口门面）

装配层在 ``app/services/scenarios.py``（Composition Root，静态 import 场景工厂）。
"""
