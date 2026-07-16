"""
任务管理 Mixin：异步/同步任务提交、状态跟踪、媒体服务器委托。
不直接引用 config_manager / database_manager 等单例，所有单例依赖通过 self.method() 调用核心同步逻辑。
"""

import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any, Optional

from ...core.logging import logger, sync_log_context
from ...models.sync import CustomItem, SyncResponse


class TaskManagerMixin:
    """任务生命周期管理（线程池提交、状态跟踪、旧任务清理、媒体服务器委托）。"""

    # 实例属性类型声明（实际在 __init__.py 的 SyncService.__init__ 中初始化）
    _executor: ThreadPoolExecutor
    _tasks_lock: Any
    _sync_tasks: dict
    _task_counter: int

    def _register_task(self, task_id: str, item_data: Any, source: str) -> None:
        """注册新任务到状态跟踪（需持有锁）"""
        self._sync_tasks[task_id] = {
            "status": "pending",
            "item": item_data,
            "source": source,
            "created_at": time.time(),
            "result": None,
            "error": None,
        }

    def _update_task_status(
        self, task_id: str, status: str, result: Any = None, error: str = None
    ) -> None:
        """更新任务状态（线程安全）"""
        with self._tasks_lock:
            if task_id in self._sync_tasks:
                self._sync_tasks[task_id]["status"] = status
                if result is not None:
                    self._sync_tasks[task_id]["result"] = result
                if error is not None:
                    self._sync_tasks[task_id]["error"] = error

    async def sync_custom_item_async(
        self, item: CustomItem, source: str = "custom"
    ) -> str:
        """异步同步自定义项目，返回任务ID"""
        self.cleanup_old_tasks()
        with self._tasks_lock:
            self._task_counter += 1
            task_id = f"sync_{self._task_counter}_{int(time.time())}"
            self._register_task(task_id, item.model_dump(), source)

        # 提交到线程池异步执行（contextvars 需 copy_context 传播）
        ctx = copy_context()

        def _run() -> SyncResponse:
            with sync_log_context(task_id):
                return self._sync_custom_item_sync(item, source, task_id)

        self._executor.submit(ctx.run, _run)

        # 不等待结果，立即返回任务ID
        with sync_log_context(task_id):
            logger.info(f"同步任务 {task_id} 已提交到异步队列")
        return task_id

    def _sync_custom_item_sync(
        self, item: CustomItem, source: str, task_id: str
    ) -> SyncResponse:
        """同步执行的内部方法"""
        try:
            self._update_task_status(task_id, "running")
            result = self.sync_custom_item(item, source)
            self._update_task_status(task_id, "completed", result=result.model_dump())
            return result
        except Exception as e:
            self._update_task_status(task_id, "failed", error=str(e))
            logger.error(f"异步同步任务 {task_id} 失败: {e}")
            return SyncResponse(status="error", message=f"异步处理失败: {str(e)}")

    def get_sync_task_status(self, task_id: str) -> Optional[dict]:
        """获取任务状态（线程安全）"""
        with self._tasks_lock:
            return self._sync_tasks.get(task_id)

    def get_all_sync_tasks(self) -> dict:
        """获取所有任务快照（线程安全）"""
        with self._tasks_lock:
            return dict(self._sync_tasks)

    def cleanup_old_tasks(self, max_age_hours: int = 24) -> None:
        """清理旧的任务记录（线程安全）"""
        current_time = time.time()
        cutoff_time = current_time - (max_age_hours * 3600)

        with self._tasks_lock:
            old_tasks = [
                task_id
                for task_id, task_info in self._sync_tasks.items()
                if task_info["created_at"] < cutoff_time
            ]
            for task_id in old_tasks:
                del self._sync_tasks[task_id]

        if old_tasks:
            logger.info(f"清理了 {len(old_tasks)} 个旧的同步任务记录")

    # ------------------------------------------------------------------
    # 媒体服务器同步委托
    # ------------------------------------------------------------------

    def _submit_media_server_task(
        self, data: dict[str, Any], *, source: str, sync_method_name: str
    ) -> str:
        """提交媒体服务器同步任务到线程池，返回任务ID

        Args:
            data: webhook 报文
            source: 源名称（plex/emby/jellyfin），用于任务ID前缀和日志
            sync_method_name: 同步方法名（如 "sync_plex_item"），线程内通过
                              getattr 解析以支持 patch.object 生效
        """
        self.cleanup_old_tasks()
        with self._tasks_lock:
            self._task_counter += 1
            task_id = f"{source}_{self._task_counter}_{int(time.time())}"
            self._register_task(task_id, data, source)

        ctx = copy_context()

        def _run() -> SyncResponse:
            with sync_log_context(task_id):
                return self._run_media_server_task(
                    data, task_id, source, sync_method_name
                )

        self._executor.submit(ctx.run, _run)
        with sync_log_context(task_id):
            logger.info(f"{source.capitalize()}同步任务 {task_id} 已提交到异步队列")
        return task_id

    def _run_media_server_task(
        self,
        data: dict[str, Any],
        task_id: str,
        source: str,
        sync_method_name: str,
    ) -> SyncResponse:
        """媒体服务器同步任务的线程池执行体"""
        try:
            self._update_task_status(task_id, "running")
            with sync_log_context(task_id):
                result = getattr(self, sync_method_name)(data)
            self._update_task_status(task_id, "completed", result=result.model_dump())
            return result
        except Exception as e:
            self._update_task_status(task_id, "failed", error=str(e))
            logger.error(f"异步{source.capitalize()}同步任务 {task_id} 失败: {e}")
            return SyncResponse(status="error", message=f"异步处理失败: {str(e)}")

    async def sync_plex_item_async(self, plex_data: dict[str, Any]) -> str:
        """异步同步Plex项目，返回任务ID"""
        return self._submit_media_server_task(
            plex_data, source="plex", sync_method_name="sync_plex_item"
        )

    def _sync_plex_item_sync(
        self, plex_data: dict[str, Any], task_id: str
    ) -> SyncResponse:
        """同步执行Plex同步的内部方法"""
        return self._run_media_server_task(plex_data, task_id, "plex", "sync_plex_item")

    def sync_plex_item(self, plex_data: dict[str, Any]) -> SyncResponse:
        """处理Plex同步请求（委托至 plex 子包）"""
        from ..plex.sync_service import plex_sync_service

        return plex_sync_service.sync_item(plex_data, self)

    async def sync_emby_item_async(self, emby_data: dict[str, Any]) -> str:
        """异步同步Emby项目，返回任务ID"""
        return self._submit_media_server_task(
            emby_data, source="emby", sync_method_name="sync_emby_item"
        )

    def _sync_emby_item_sync(
        self, emby_data: dict[str, Any], task_id: str
    ) -> SyncResponse:
        """同步执行Emby同步的内部方法"""
        return self._run_media_server_task(emby_data, task_id, "emby", "sync_emby_item")

    def sync_emby_item(self, emby_data: dict[str, Any]) -> SyncResponse:
        """处理Emby同步请求（委托至 emby 子包）"""
        from ..emby.sync_service import emby_sync_service

        return emby_sync_service.sync_item(emby_data, self)

    async def sync_jellyfin_item_async(self, jellyfin_data: dict[str, Any]) -> str:
        """异步同步Jellyfin项目，返回任务ID"""
        return self._submit_media_server_task(
            jellyfin_data, source="jellyfin", sync_method_name="sync_jellyfin_item"
        )

    def _sync_jellyfin_item_sync(
        self, jellyfin_data: dict[str, Any], task_id: str
    ) -> SyncResponse:
        """同步执行Jellyfin同步的内部方法"""
        return self._run_media_server_task(
            jellyfin_data, task_id, "jellyfin", "sync_jellyfin_item"
        )

    def sync_jellyfin_item(self, jellyfin_data: dict[str, Any]) -> SyncResponse:
        """处理Jellyfin同步请求（委托至 jellyfin 子包）"""
        from ..jellyfin.sync_service import jellyfin_sync_service

        return jellyfin_sync_service.sync_item(jellyfin_data, self)
