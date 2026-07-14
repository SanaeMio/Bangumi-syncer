"""Repository 基类

为 database 子包中各 repository 提供统一的读写骨架，消除 try/except +
_execute_with_lock 闭包模板。

子类只需实现 `_read(conn)` / `_write(conn)` 闭包并调用
`self._run_read` / `self._run_write`，异常处理、锁、commit、日志、默认值
由基类统一处理。
"""

from __future__ import annotations

from typing import Any, Callable

from ..logging import logger


class BaseRepository:
    """所有 repository 的基类，封装锁保护下的读写骨架。"""

    def __init__(self, conn):
        self._conn = conn

    # ------------------------------------------------------------------
    # 读写骨架
    # ------------------------------------------------------------------

    def _run_write(
        self,
        fn: Callable[[Any], Any],
        *,
        error_msg: str,
        default: Any = None,
        reraise: bool = False,
        ensure_schema: Callable[[Any], None] | None = None,
    ) -> Any:
        """在锁保护下执行写操作，自动 commit。

        Args:
            fn: 接收 conn、返回任意值的闭包；内部执行 SQL 写操作。
            error_msg: 异常时记录的日志前缀（如 "记录同步日志失败"）。
            default: 异常时返回的默认值（reraise=True 时忽略）。
            reraise: 为 True 时异常被记录后重新抛出，而非返回 default。
            ensure_schema: 可选的 schema 迁移回调，接收 cursor。
        """
        try:

            def _write(conn):
                if ensure_schema is not None:
                    ensure_schema(conn.cursor())
                result = fn(conn)
                conn.commit()
                return result

            return self._conn._execute_with_lock(_write)
        except Exception as e:
            logger.error(f"{error_msg}: {e}")
            if reraise:
                raise
            return default

    def _run_read(
        self,
        fn: Callable[[Any], Any],
        *,
        error_msg: str,
        default: Any = None,
        reraise: bool = False,
        ensure_schema: Callable[[Any], None] | None = None,
    ) -> Any:
        """在锁保护下执行读操作（不 commit）。

        Args:
            fn: 接收 conn、返回任意值的闭包；内部执行 SQL 读操作。
            error_msg: 异常时记录的日志前缀。
            default: 异常时返回的默认值（reraise=True 时忽略）。
            reraise: 为 True 时异常被记录后重新抛出，而非返回 default。
            ensure_schema: 可选的 schema 迁移回调，接收 cursor。
        """
        try:
            if ensure_schema is not None:
                # schema 迁移需要在锁内执行
                def _read(conn):
                    ensure_schema(conn.cursor())
                    return fn(conn)

                return self._conn._execute_with_lock(_read)
            else:
                return self._conn._execute_with_lock(fn)
        except Exception as e:
            logger.error(f"{error_msg}: {e}")
            if reraise:
                raise
            return default
