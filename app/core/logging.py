"""
日志管理模块
"""
import datetime
import os
import sys
from typing import Optional
from pathlib import Path


class Logger:
    """日志管理器"""
    
    # 日志级别常量
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    
    def __init__(self):
        self.need_mix = True
        self.api_key = '_hide_api_key_'
        self.netloc = '_mix_netloc_'
        self.netloc_replace = '_mix_netloc_'
        self.user_name = self._get_safe_username()
        
        # 延迟获取debug_mode，避免循环依赖
        self._debug_mode = None
        
        # 初始化日志文件
        self._setup_log_file()
    
    def _get_safe_username(self) -> str:
        """安全地获取用户名"""
        try:
            return os.getlogin()
        except (OSError, AttributeError):
            # Docker环境或其他无法获取登录用户的环境
            return os.environ.get('USER', os.environ.get('USERNAME', 'docker_user'))
    
    def _setup_log_file(self) -> None:
        """设置日志文件"""
        # 延迟导入，避免循环依赖
        try:
            from .config import config_manager
        except ImportError:
            # 如果无法导入，跳过日志文件设置
            return
        
        log_file_path = config_manager.get('dev', 'log_file', fallback='')
        if not log_file_path:
            return
        
        # 处理相对路径
        if log_file_path.startswith('./'):
            cwd = Path(__file__).parent.parent.parent
            log_file_path = cwd / log_file_path.split('./', 1)[1]
        
        # 创建日志目录
        log_path = Path(log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 检查文件大小，超过10MB时重置
        mode = 'a'
        if log_path.exists() and log_path.stat().st_size >= 10 * 1024 * 1024:
            mode = 'w'
        
        # 打开日志文件
        self.log_file = open(log_path, mode, encoding='utf-8')
    
    @property
    def debug_mode(self) -> bool:
        """获取调试模式状态"""
        if self._debug_mode is None:
            # 延迟导入，避免循环依赖
            try:
                from .config import config_manager
            except ImportError:
                return False
            self._debug_mode = config_manager.get('dev', 'debug', fallback=False)
        return self._debug_mode
    
    @staticmethod
    def mix_host_gen(netloc: str) -> str:
        """混淆主机名"""
        host, *port = netloc.split(':')
        port_str = ':' + port[0] if port else ''
        new = host[:len(host) // 2] + '_mix_host_' + port_str
        return new
    
    def mix_args_str(self, *args) -> list:
        """混淆敏感信息"""
        return [str(i).replace(self.api_key, '_hide_api_key_')
                .replace(self.netloc, self.netloc_replace)
                .replace(self.user_name, '_hide_user_')
                for i in args]
    
    def log(self, *args, end: Optional[str] = None, silence: bool = False, level: Optional[str] = None) -> None:
        """统一的日志输出方法"""
        if silence:
            return
        
        # 根据日志级别添加对应的标识符
        level_prefix = f"[{level}]" if level else ""
        
        timestamp = f"[{datetime.datetime.now().strftime('%D %H:%M:%S.%f')[:19]}] "
        message = ' '.join(str(i) for i in args)
        log_line = timestamp + level_prefix + " " + message
        
        # 确保有换行符
        if end is None:
            end = '\n'
        
        # 输出到控制台
        print(log_line, end=end)
        
        # 输出到日志文件
        if hasattr(self, 'log_file'):
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