"""后台任务注册中心

提供 fire-and-forget asyncio.create_task 的统一注册与生命周期管理：
- 任务加入全局集合，避免被 GC 提前回收
- 应用 shutdown 时可通过 cancel_all / wait_all 优雅取消并等待
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

_background_tasks: set[asyncio.Task] = set()


def register_background_task(coro: Awaitable) -> asyncio.Task:
    """创建并注册一个后台任务

    用法：
        register_background_task(_run())

    等价于 asyncio.create_task(coro)，但额外加入全局集合，
    使应用 shutdown 时能够 cancel/await，避免半途丢失。
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def cancel_all() -> None:
    """取消所有已注册的后台任务（仅限当前事件循环）"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    for task in _background_tasks:
        if loop is None or task.get_loop() is loop:
            task.cancel()


async def wait_all(timeout: float | None = None) -> None:
    """等待所有后台任务结束（应在 cancel_all 之后调用）

    Args:
        timeout: 最大等待秒数；None 表示无超时
    """
    if not _background_tasks:
        return

    # 只处理属于当前事件循环且未完成的任务，跳过其它循环的残留（测试隔离）
    loop = asyncio.get_running_loop()
    pending = [t for t in _background_tasks if not t.done() and t.get_loop() is loop]
    # 清除已完成或跨循环的残留
    _background_tasks.clear()
    for t in pending:
        _background_tasks.add(t)

    if not pending:
        return

    if timeout is None:
        await asyncio.gather(*pending, return_exceptions=True)
    else:
        done, still_pending = await asyncio.wait(pending, timeout=timeout)
        for task in still_pending:
            task.cancel()
        if still_pending:
            await asyncio.gather(*still_pending, return_exceptions=True)
    _background_tasks.clear()
