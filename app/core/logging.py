"""
日志管理模块
"""

import contextvars
import datetime
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .config import ConfigManager

# 与 config.ini / Web 日志 API 对齐的默认相对路径（相对项目根）
DEFAULT_DEV_LOG_FILE = "./log.txt"

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# 同步日志关联 ID（线程内通过 ContextVar 传播）
sync_run_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "sync_run_id", default=None
)

RUN_ID_FIELD_WIDTH = 28  # 供 UI 等宽展示参考；写入文件时不填充空格


def get_sync_run_id() -> Optional[str]:
    """当前同步 run_id；无上下文时为 None。"""
    return sync_run_id.get()


@contextmanager
def sync_log_context(run_id: str) -> Iterator[str]:
    """在作用域内为日志行附加 [run:...] 标记。"""
    token = sync_run_id.set(run_id)
    try:
        yield run_id
    finally:
        sync_run_id.reset(token)


def new_inline_sync_run_id(counter: int) -> str:
    """直调 sync_custom_item 时生成 run_id。"""
    return f"sync_inline_{counter}_{int(time.time())}"


def new_retry_sync_run_id(record_id: int) -> str:
    """手动重试同步记录时生成 run_id。"""
    return f"retry_{record_id}_{int(time.time())}"


def resolve_dev_log_file_path(raw: str) -> Path:
    """将配置中的 log_file 转为绝对 Path（仅将以 ./ 开头的视为相对项目根）。"""
    if raw.startswith("./"):
        return _REPO_ROOT / raw.split("./", 1)[1]
    return Path(raw)


def effective_dev_log_file_raw(config_manager: "ConfigManager") -> Optional[str]:
    """
    返回将用于打开日志文件的原始配置字符串；None 表示显式留空、禁用文件日志。
    缺键时使用 DEFAULT_DEV_LOG_FILE。
    """
    cfg = config_manager.get_config_parser()
    if not cfg.has_section("dev"):
        return DEFAULT_DEV_LOG_FILE
    if not cfg.has_option("dev", "log_file"):
        return DEFAULT_DEV_LOG_FILE
    raw = str(cfg.get("dev", "log_file", fallback="")).strip()
    if raw == "":
        return None
    return raw


def resolved_dev_log_file_path(
    config_manager: "ConfigManager",
) -> Optional[Path]:
    """当前配置下解析后的日志文件绝对路径；禁用时为 None。"""
    raw = effective_dev_log_file_raw(config_manager)
    if raw is None:
        return None
    return resolve_dev_log_file_path(raw).resolve()


class Logger:
    """日志管理器"""

    # 日志级别常量
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

    def __init__(self) -> None:
        self.need_mix = True
        self.api_key = "_hide_api_key_"
        self.netloc = "_mix_netloc_"
        self.netloc_replace = "_mix_netloc_"
        self.user_name = self._get_safe_username()

        # 延迟获取debug_mode，避免循环依赖
        self._debug_mode = None
        self._log_file_path: Optional[Path] = None
        # 不在 __init__ 中打开日志文件：模块执行 logger = Logger() 时，若此处导入
        # config，而 config 初始化链又 import logger，会触发 partially initialized 循环依赖。
        self._log_file_lazy_initialized = False
        # 日志监听器列表（用于实时捕获日志，如重试 SSE 推送）
        self._listeners: list[Callable[[str, str], None]] = []

    def add_listener(self, callback: Callable[[str, str], None]) -> None:
        """添加日志监听器

        callback 签名: callback(log_line: str, level: str)
        log_line 为完整日志行（含时间戳和级别前缀），level 为级别字符串（DEBUG/INFO/WARNING/ERROR）
        """
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[str, str], None]) -> None:
        """移除日志监听器"""
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def _notify_listeners(self, log_line: str, level: Optional[str]) -> None:
        """通知所有监听器（监听器异常不影响日志输出）"""
        if not self._listeners:
            return
        level_str = level or ""
        for cb in list(self._listeners):
            try:
                cb(log_line, level_str)
            except Exception:
                pass

    def _get_safe_username(self) -> str:
        """安全地获取用户名"""
        try:
            return os.getlogin()
        except (OSError, AttributeError):
            # Docker环境或其他无法获取登录用户的环境
            return os.environ.get("USER", os.environ.get("USERNAME", "docker_user"))

    def _close_log_file_handle(self) -> None:
        if hasattr(self, "log_file"):
            try:
                self.log_file.close()
            except OSError:
                pass
            del self.log_file
        self._log_file_path = None

    def _lazy_init_log_file_once(self) -> None:
        """首次写日志前再绑定文件与 config（避免与 config 模块循环导入）。"""
        if self._log_file_lazy_initialized:
            return
        self._log_file_lazy_initialized = True
        self._setup_log_file()

    def _setup_log_file(self) -> None:
        """设置日志文件（成功/跳过/失败均向 stderr 输出一行，便于排查）"""
        self._close_log_file_handle()

        try:
            from .config import config_manager
        except ImportError as e:
            print(
                f"文件日志未启用: 无法导入 config（{e!r}），仅输出到控制台",
                file=sys.stderr,
            )
            return

        raw = effective_dev_log_file_raw(config_manager)
        if raw is None:
            print(
                "文件日志已禁用: [dev] log_file 为空（留空则禁用）",
                file=sys.stderr,
            )
            return

        log_path = resolve_dev_log_file_path(raw)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a"
            if log_path.exists() and log_path.stat().st_size >= 2 * 1024 * 1024:
                mode = "w"
            self.log_file = open(log_path, mode, encoding="utf-8", buffering=1)
            self._log_file_path = log_path.resolve()
            print(
                f"文件日志已启用: {self._log_file_path}",
                file=sys.stderr,
            )
        except OSError as e:
            print(
                f"文件日志打开失败: {e!r} 路径={log_path}",
                file=sys.stderr,
            )

    def _ensure_log_file_for_write(self) -> None:
        """若磁盘上日志路径已不存在，则关闭句柄并重新打开。"""
        if not hasattr(self, "log_file") or self._log_file_path is None:
            return
        try:
            exists = self._log_file_path.exists()
        except OSError:
            exists = False
        if not exists:
            print(
                f"文件日志路径已丢失，尝试重新打开: {self._log_file_path}",
                file=sys.stderr,
            )
            self._setup_log_file()

    @property
    def debug_mode(self) -> bool:
        """获取调试模式状态"""
        if self._debug_mode is None:
            # 延迟导入，避免循环依赖
            try:
                from .config import config_manager
            except ImportError:
                return False
            self._debug_mode = config_manager.get("dev", "debug", fallback=False)
        return self._debug_mode

    @staticmethod
    def mix_host_gen(netloc: str) -> str:
        """混淆主机名"""
        host, *port = netloc.split(":")
        port_str = ":" + port[0] if port else ""
        new = host[: len(host) // 2] + "_mix_host_" + port_str
        return new

    def mix_args_str(self, *args) -> list:
        """混淆敏感信息"""
        return [
            str(i)
            .replace(self.api_key, "_hide_api_key_")
            .replace(self.netloc, self.netloc_replace)
            .replace(self.user_name, "_hide_user_")
            for i in args
        ]

    def _format_level_field(self, level: Optional[str]) -> str:
        """级别标签，如 [INFO]、[DEBUG]。"""
        if not level:
            return ""
        return f"[{level}]"

    def _format_log_line(self, *args, level: Optional[str]) -> str:
        """格式化日志行（不输出）"""
        timestamp = f"[{datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S.%f')[:-3]}]"
        message = " ".join(str(i) for i in args)
        head = timestamp
        if level:
            head += " " + self._format_level_field(level)
        run_id = get_sync_run_id()
        if run_id:
            head += f" [run:{run_id}]"
        return f"{head} {message}"

    def log(
        self,
        *args,
        end: Optional[str] = None,
        silence: bool = False,
        level: Optional[str] = None,
    ) -> None:
        """统一的日志输出方法"""
        if silence:
            return

        self._lazy_init_log_file_once()

        log_line = self._format_log_line(*args, level=level)

        # 确保有换行符
        if end is None:
            end = "\n"

        # 输出到控制台
        print(log_line, end=end)

        # 输出到日志文件
        if hasattr(self, "log_file"):
            self._ensure_log_file_for_write()
        if hasattr(self, "log_file"):
            self.log_file.write(log_line + end)

        # 通知监听器
        self._notify_listeners(log_line, level)

    def info(self, *args, end: Optional[str] = None, silence: bool = False) -> None:
        """INFO级别日志"""
        if not silence and self.need_mix:
            args = self.mix_args_str(*args)
        self.log(*args, end=end, silence=silence, level=self.INFO)

    def debug(self, *args, end: Optional[str] = None, silence: bool = False) -> None:
        """DEBUG级别日志"""
        # DEBUG级别只在调试模式下输出到控制台/文件
        if not self.debug_mode:
            # 即使 debug_mode 关闭，也通知监听器（用于重试时捕获 debug 日志）
            if self._listeners and not silence:
                mixed_args = self.mix_args_str(*args) if self.need_mix else args
                log_line = self._format_log_line(*mixed_args, level=self.DEBUG)
                self._notify_listeners(log_line, self.DEBUG)
            return
        if not silence and self.need_mix:
            args = self.mix_args_str(*args)
        self.log(*args, end=end, silence=silence, level=self.DEBUG)

    def error(self, *args, end: Optional[str] = None, silence: bool = False) -> None:
        """ERROR级别日志"""
        if not silence and self.need_mix:
            args = self.mix_args_str(*args)
        self.log(*args, end=end, silence=silence, level=self.ERROR)

    def warning(self, *args, end: Optional[str] = None, silence: bool = False) -> None:
        """WARNING级别日志"""
        if not silence and self.need_mix:
            args = self.mix_args_str(*args)
        self.log(*args, end=end, silence=silence, level=self.WARNING)


# 全局日志实例
logger = Logger()
