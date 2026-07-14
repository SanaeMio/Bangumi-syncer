"""
任务管理 Mixin：异步/同步任务提交、状态跟踪、媒体服务器委托。
不直接引用 config_manager / database_manager 等单例，所有单例依赖通过 self.method() 调用核心同步逻辑。
"""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from ...core.logging import logger
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
            self._register_task(task_id, item.dict(), source)

        # 提交到线程池异步执行
        self._executor.submit(self._sync_custom_item_sync, item, source, task_id)

        # 不等待结果，立即返回任务ID
        logger.info(f"同步任务 {task_id} 已提交到异步队列")
        return task_id

    def _sync_custom_item_sync(
        self, item: CustomItem, source: str, task_id: str
    ) -> SyncResponse:
        """同步执行的内部方法"""
        try:
            self._update_task_status(task_id, "running")
            result = self.sync_custom_item(item, source)
            self._update_task_status(task_id, "completed", result=result.dict())
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

    def cleanup_old_tasks(self, max_age_hours: int = 24):
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

    async def sync_plex_item_async(self, plex_data: dict[str, Any]) -> str:
        """异步同步Plex项目，返回任务ID"""
        self.cleanup_old_tasks()
        with self._tasks_lock:
            self._task_counter += 1
            task_id = f"plex_{self._task_counter}_{int(time.time())}"
            self._register_task(task_id, plex_data, "plex")

        self._executor.submit(self._sync_plex_item_sync, plex_data, task_id)
        logger.info(f"Plex同步任务 {task_id} 已提交到异步队列")
        return task_id

    def _sync_plex_item_sync(
        self, plex_data: dict[str, Any], task_id: str
    ) -> SyncResponse:
        """同步执行Plex同步的内部方法"""
        try:
            self._update_task_status(task_id, "running")
            result = self.sync_plex_item(plex_data)
            self._update_task_status(task_id, "completed", result=result.dict())
            return result
        except Exception as e:
            self._update_task_status(task_id, "failed", error=str(e))
            logger.error(f"异步Plex同步任务 {task_id} 失败: {e}")
            return SyncResponse(status="error", message=f"异步处理失败: {str(e)}")

    def sync_plex_item(self, plex_data: dict[str, Any]) -> SyncResponse:
        """处理Plex同步请求（委托至 plex 子包）"""
        from ..plex.sync_service import plex_sync_service

        return plex_sync_service.sync_item(plex_data, self)

    async def sync_emby_item_async(self, emby_data: dict[str, Any]) -> str:
        """异步同步Emby项目，返回任务ID"""
        self.cleanup_old_tasks()
        with self._tasks_lock:
            self._task_counter += 1
            task_id = f"emby_{self._task_counter}_{int(time.time())}"
            self._register_task(task_id, emby_data, "emby")

        self._executor.submit(self._sync_emby_item_sync, emby_data, task_id)
        logger.info(f"Emby同步任务 {task_id} 已提交到异步队列")
        return task_id

    def _sync_emby_item_sync(
        self, emby_data: dict[str, Any], task_id: str
    ) -> SyncResponse:
        """同步执行Emby同步的内部方法"""
        try:
            self._update_task_status(task_id, "running")
            result = self.sync_emby_item(emby_data)
            self._update_task_status(task_id, "completed", result=result.dict())
            return result
        except Exception as e:
            self._update_task_status(task_id, "failed", error=str(e))
            logger.error(f"异步Emby同步任务 {task_id} 失败: {e}")
            return SyncResponse(status="error", message=f"异步处理失败: {str(e)}")

    def sync_emby_item(self, emby_data: dict[str, Any]) -> SyncResponse:
        """处理Emby同步请求（委托至 emby 子包）"""
        from ..emby.sync_service import emby_sync_service

        return emby_sync_service.sync_item(emby_data, self)

    async def sync_jellyfin_item_async(self, jellyfin_data: dict[str, Any]) -> str:
        """异步同步Jellyfin项目，返回任务ID"""
        self.cleanup_old_tasks()
        with self._tasks_lock:
            self._task_counter += 1
            task_id = f"jellyfin_{self._task_counter}_{int(time.time())}"
            self._register_task(task_id, jellyfin_data, "jellyfin")

        self._executor.submit(self._sync_jellyfin_item_sync, jellyfin_data, task_id)
        logger.info(f"Jellyfin同步任务 {task_id} 已提交到异步队列")
        return task_id

    def _sync_jellyfin_item_sync(
        self, jellyfin_data: dict[str, Any], task_id: str
    ) -> SyncResponse:
        """同步执行Jellyfin同步的内部方法"""
        try:
            self._update_task_status(task_id, "running")
            result = self.sync_jellyfin_item(jellyfin_data)
            self._update_task_status(task_id, "completed", result=result.dict())
            return result
        except Exception as e:
            self._update_task_status(task_id, "failed", error=str(e))
            logger.error(f"异步Jellyfin同步任务 {task_id} 失败: {e}")
            return SyncResponse(status="error", message=f"异步处理失败: {str(e)}")

    def sync_jellyfin_item(self, jellyfin_data: dict[str, Any]) -> SyncResponse:
        """处理Jellyfin同步请求（委托至 jellyfin 子包）"""
        from ..jellyfin.sync_service import jellyfin_sync_service

        return jellyfin_sync_service.sync_item(jellyfin_data, self)
