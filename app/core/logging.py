"""
日志管理模块
"""

import datetime
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .config import ConfigManager

# 与 config.ini / Web 日志 API 对齐的默认相对路径（相对项目根）
DEFAULT_DEV_LOG_FILE = "./log.txt"

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


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

    def __init__(self):
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
            if log_path.exists() and log_path.stat().st_size >= 10 * 1024 * 1024:
                mode = "w"
            self.log_file = open(log_path, mode, encoding="utf-8")
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

        # 根据日志级别添加对应的标识符
        level_prefix = f"[{level}]" if level else ""

        timestamp = (
            f"[{datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S.%f')[:-3]}] "
        )
        message = " ".join(str(i) for i in args)
        log_line = timestamp + level_prefix + " " + message

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
            self.log_file.flush()

    def info(self, *args, end: Optional[str] = None, silence: bool = False) -> None:
        """INFO级别日志"""
        if not silence and self.need_mix:
            args = self.mix_args_str(*args)
        self.log(*args, end=end, silence=silence, level=self.INFO)

    def debug(self, *args, end: Optional[str] = None, silence: bool = False) -> None:
        """DEBUG级别日志"""
        # DEBUG级别只在调试模式下输出
        if not self.debug_mode:
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
