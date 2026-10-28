"""Agent 场景协议：通用运行时（runtime）与业务场景之间的契约。

具体的 Agent 场景（如 LLM 匹配增强）通过 :class:`ScenarioHooks` 提供全部
业务差异点；通用运行时（:mod:`app.services.agent.run.runtime`）只负责 run 编排
与恢复续跑状态机，并原样透传 ``ctx``（场景上下文，结构由场景自定义）。

设计约束：

- 运行时**不感知**任何场景概念（sync_record / bgm / 候选表 / 业务键等），
  这些全部封装在场景钩子与 ``ctx`` 内。
- 钩子中的依赖查询约定：场景侧钩子实现内部通过**场景模块全局名**引用依赖
  （而非闭包硬绑定），便于测试 patch 与后续替换。

泛型化（``CtxT``）：

- ``ctx`` 的具体类型是**场景私有**的（如 ``_MatchContext``），通用层不得引用；
  但若一律标注 ``Any``，场景签名错配（如 ``build_seed`` 误收 ``dict``）在类型层面
  完全静默。故以类型变量 :data:`CtxT` 承载：``ScenarioHooks[CtxT]`` /
  ``ScenarioRuntime[CtxT]`` 让**单条调用链内**ctx 类型自洽，而通用层源码仍只写
  ``CtxT``，解耦不被破坏。
- ``TerminalHandler`` 用 ``__call__`` Protocol 逐字受检终局签名（含仅关键字的
  ``total_tokens`` / ``notification_service``）。此前为表达「场景私有 ctx + 仅关键字
  参数」而退化为 ``Callable[..., Awaitable[str]]``（零检查）；引入 ``CtxT`` 后
  该阻碍消失——ctx 是类型变量而非场景具体类型。
- **局限**：泛型运行时被擦除，``get_scenario(task_type)`` 动态查表仍返回
  ``Any``；此处只能守住单链路自洽，跨场景分发的正确性不在类型系统覆盖内。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from app.services.agent.execution.tools import ToolDefinition, ToolRegistry
from app.services.agent.loop import RunResult
from app.services.agent.protocols import StreamFn
from app.services.llm.models import Message
from app.services.notification_service import NotificationService


class RunInputs(Protocol):
    """场景上下文的**通用能力**：必须携带本次run 的种子消息。

    这是通用层对 ``ctx`` 的**唯一**要求，且只要求通用概念（LLM 消息）；ctx 的其余
    内容（``sync_record`` / ``bgm`` / 候选表 / 业务键等）通用层一概不感知。

    seed 之所以是 ctx 的必备属性而非 ``ScenarioHooks`` 钩子：它是**数据**（本次 run
    的派生输入快照），不是**行为**（怎么做）。由场景在 ``new_ctx``（run 初始化）时
    一次性备好，通用层只读取，避免执行期再回调场景派生。
    """

    @property
    def seed(self) -> Sequence[Message]: ...


#: 场景上下文类型（**场景私有**，如 ``_MatchContext``）。通用层只以本类型变量出现，
#: 不引用其具体类型，从而在收紧类型的同时保持解耦。
#: 声明为**不变**：``ScenarioHooks`` / ``ScenarioRuntime`` 的字段与 run 参数双向使用它；
#: 受 :class:`RunInputs` 约束，故通用层读 ``ctx.seed`` 有类型保障。
CtxT = TypeVar("CtxT", bound=RunInputs)

#: ``TerminalHandler`` 专用：ctx 只出现在参数位置（逆变），故须声明为逆变类型变量。
_CtxT_contra = TypeVar("_CtxT_contra", contravariant=True)


class TerminalHandler(Protocol[_CtxT_contra]):
    """终局处理：``(run_id, result, ctx, *, total_tokens, notification_service) -> status``。

    以 ``__call__`` Protocol 表达，使实现签名（含仅关键字参数）**逐字受检**。

    运行时**不注入 dbm**：数据库管理器是全局单例（``get_database_manager``），由场景
    实现内部自取；作为参数跨层传递既无测试收益（测试注入走
    ``set_database_manager``），又使通用层持有场景存储依赖。
    """

    async def __call__(
        self,
        run_id: str,
        result: RunResult,
        ctx: _CtxT_contra,
        *,
        total_tokens: int,
        notification_service: NotificationService | None,
    ) -> str: ...


@dataclass(frozen=True)
class RunFailure:
    """run 终态失败事件（通用层 → 场景的唯一失败出口）。

    失败判定点在 runtime 内部（不经 ``handle_terminal``）；通知等副作用
    属场景职责（见 docs/development/agent-runtime.md），runtime 只发射事件。
    """

    run_id: str
    task_type: str
    stop_reason: str
    last_error: str
    attempts: int = 0


@dataclass(frozen=True)
class ScenarioHooks(Generic[CtxT]):
    """场景钩子集合：run / continue_run 的全部业务差异点。"""

    #: 任务类型（观测/日志用，如 "match"）
    task_type: str
    #: 注册场景工具（``registry`` + ``ctx`` → 工具定义列表）
    register_tools: Callable[[ToolRegistry, CtxT], list[ToolDefinition]]
    #: 构建默认**流式** LLM 函数（``thinking_level`` → ``StreamFn``；场景决定 job 归属
    #: 与思考强度透传）。返回 async iterator（``StreamChunk``）；每轮的聚合与落库由
    #: loop 经 recorder 的 chat span（``recorder.chat_span()``）单点完成。
    build_stream_fn: Callable[[str], StreamFn]
    #: 解析思考强度（从场景集中配置读取；恢复续跑路径使用）
    resolve_thinking_level: Callable[[], str]
    #: 解析轮次预算（``thinking_level`` → ``max_iterations``；含场景配置覆盖）
    resolve_max_iterations: Callable[[str], int]
    #: 终局处理（校验/落库/通知），返回终态 status 字符串
    handle_terminal: TerminalHandler[CtxT]
    #: 终止提交软护栏（veto）：入参为 terminal 工具调用 input；返回 None=放行，
    #: 返回字符串=暂缓提示文案（loop 注入配对 tool_result 后继续一轮，仅拦一次）。
    #: None 时行为与现状完全一致（零回归）。
    veto_terminal: Callable[[dict], str | None] | None = None
    #: 终态失败回调（失败判定点由 runtime 触发，不经 handle_terminal）：
    #: 场景在此处理通知等职责（通用层不感知通知）。None = 不处理。
    on_run_failed: Callable[[RunFailure], None] | None = None
