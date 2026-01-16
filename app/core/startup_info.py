"""
启动信息显示模块
提供统一的跨平台启动信息显示
"""

import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from version import get_version, get_version_name


class StartupInfo:
    """启动信息显示类"""

    # 颜色代码（ANSI转义序列）
    COLORS = {
        "reset": "\033[0m",
        "bold": "\033[1m",
        "dim": "\033[2m",
        "red": "\033[31m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "blue": "\033[34m",
        "magenta": "\033[35m",
        "cyan": "\033[36m",
        "white": "\033[37m",
        "bg_blue": "\033[44m",
        "bg_green": "\033[42m",
        "bg_yellow": "\033[43m",
        "bg_red": "\033[41m",
    }

    def __init__(self):
        self.supports_color = self._check_color_support()

    def _check_color_support(self) -> bool:
        """检查是否支持颜色输出"""
        # 检查环境变量
        if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
            return False

        # Windows 10+ 支持ANSI颜色，但需要特殊处理
        if platform.system() == "Windows":
            # Windows CMD可能不支持ANSI颜色，PowerShell支持
            try:
                # 检查是否在PowerShell中运行
                result = subprocess.run(
                    ["powershell", "-Command", "$Host.Name"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if "PowerShell" in result.stdout:
                    return True
                # 检查是否在Windows Terminal中
                if "WT_SESSION" in os.environ:
                    return True
                # 检查是否在Git Bash中
                if "MSYSTEM" in os.environ:
                    return True
                # 默认CMD不支持，返回False
                return False
            except:
                return False

        # Unix/Linux/macOS 通常支持
        return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    def colorize(self, text: str, color: str) -> str:
        """为文本添加颜色"""
        if not self.supports_color:
            return text
        color_code = self.COLORS.get(color, "")
        return f"{color_code}{text}{self.COLORS['reset']}"

    def print_banner(self, title: str = None, version: str = None):
        """打印应用横幅"""
        if title is None:
            title = get_version_name()
        if version is None:
            version = get_version()
        """打印应用横幅"""
        banner_width = 60
        title_with_version = f"{title} v{version}"

        # 顶部边框
        top_border = "╔" + "═" * (banner_width - 2) + "╗"
        print(self.colorize(top_border, "cyan"))

        # 标题行
        title_padding = (banner_width - 2 - len(title_with_version)) // 2
        title_line = "║" + " " * title_padding + title_with_version
        title_line += " " * (banner_width - 2 - len(title_line) + 1) + "║"
        print(self.colorize(title_line, "bold"))

        # 底部边框
        bottom_border = "╚" + "═" * (banner_width - 2) + "╝"
        print(self.colorize(bottom_border, "cyan"))
        print()

    def print_system_info(self, config_path: Path):
        """打印系统信息"""
        print(self.colorize("🔧 系统信息", "bold"))
        print(self.colorize("─" * 40, "dim"))

        # Python信息
        python_info = f"Python: {sys.executable}"
        print(f"  {python_info}")

        # 版本信息
        version_info = f"版本: {platform.python_version()}"
        print(f"  {version_info}")

        # 操作系统信息
        os_info = f"系统: {platform.platform(True)}"
        print(f"  {os_info}")

        # 配置文件路径
        config_info = f"配置: {config_path}"
        print(f"  {config_info}")

        # 工作目录
        work_dir = f"目录: {os.getcwd()}"
        print(f"  {work_dir}")
        print()

    def print_startup_progress(self, step: int, total: int, message: str):
        """打印启动进度"""
        progress_bar_width = 30
        filled_width = int(progress_bar_width * step / total)
        bar = "█" * filled_width + "░" * (progress_bar_width - filled_width)
        percentage = int(100 * step / total)

        progress_text = f"[{bar}] {percentage:3d}% {message}"
        print(self.colorize(progress_text, "green"))

    def print_success(self, message: str):
        """打印成功信息"""
        print(self.colorize(f"✅ {message}", "green"))

    def print_warning(self, message: str):
        """打印警告信息"""
        print(self.colorize(f"⚠️  {message}", "yellow"))

    def print_error(self, message: str):
        """打印错误信息"""
        print(self.colorize(f"❌ {message}", "red"))

    def print_info(self, message: str):
        """打印信息"""
        print(self.colorize(f"ℹ️ {message}", "blue"))

    def print_separator(self):
        """打印分隔线"""
        print(self.colorize("─" * 50, "dim"))

    def print_startup_complete(self, host: str = "0.0.0.0", port: int = 8000):
        """打印启动完成信息"""
        print()
        print(self.colorize("🎉 启动完成!", "bold"))
        print(self.colorize("─" * 40, "dim"))

        # 根据环境显示不同的访问地址
        if os.environ.get("DOCKER_CONTAINER") or os.path.exists("/.dockerenv"):
            # Docker环境显示localhost
            access_url = f"http://localhost:{port}"
            print(self.colorize(f"🌐 访问地址: {access_url}", "cyan"))
            print(self.colorize(f"🔗 容器内地址: http://{host}:{port}", "dim"))
        else:
            # 本地环境显示实际地址
            access_url = f"http://{host}:{port}"
            print(self.colorize(f"🌐 访问地址: {access_url}", "cyan"))

        print(
            self.colorize(
                f"🕐 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "dim"
            )
        )
        print()


# 全局实例
startup_info = StartupInfo()
