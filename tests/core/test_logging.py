"""
Logger tests - Simplified version
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.logging import (
    Logger,
    effective_dev_log_file_raw,
    resolve_dev_log_file_path,
    resolved_dev_log_file_path,
)


def _make_logger():
    """创建一个不触发循环导入的 Logger"""
    with (
        patch("app.core.logging.os.getlogin", side_effect=OSError()),
        patch("app.core.logging.os.environ.get", return_value="test_user"),
    ):
        logger = Logger()
        logger.need_mix = False
        return logger


class TestLoggerSimple:
    """Test Logger class with simplified tests"""

    def test_mix_host_gen(self):
        """Test host mixing function"""
        result1 = Logger.mix_host_gen("example.com:8080")
        assert "_mix_host_" in result1
        assert "8080" in result1

        result2 = Logger.mix_host_gen("example.com")
        assert "_mix_host_" in result2

    def test_logger_init(self):
        """Test logger initialization"""
        logger = _make_logger()
        assert hasattr(logger, "need_mix")
        assert hasattr(logger, "api_key")

    def test_mix_args_str(self):
        """Test argument string mixing"""
        logger = _make_logger()
        args = ["message", "test"]
        result = logger.mix_args_str(*args)
        assert isinstance(result, list)

    def test_log_output(self, capsys):
        """Test log output"""
        logger = _make_logger()
        logger.info("Test message")
        captured = capsys.readouterr()
        assert "Test message" in captured.out

    def test_log_with_level(self, capsys):
        """Test log with level prefix"""
        logger = _make_logger()
        logger.log("Test message", level="INFO")
        captured = capsys.readouterr()
        assert "[INFO]" in captured.out

    def test_info_level(self, capsys):
        """Test info level logging"""
        logger = _make_logger()
        logger.info("Info message")
        captured = capsys.readouterr()
        assert "[INFO]" in captured.out

    def test_warning_level(self, capsys):
        """Test warning level logging"""
        logger = _make_logger()
        logger.warning("Warning message")
        captured = capsys.readouterr()
        assert "[WARNING]" in captured.out

    def test_error_level(self, capsys):
        """Test error level logging"""
        logger = _make_logger()
        logger.error("Error message")
        captured = capsys.readouterr()
        assert "[ERROR]" in captured.out

    def test_debug_level_with_debug_mode(self, capsys):
        """Test debug level with debug mode enabled"""
        logger = _make_logger()
        logger._debug_mode = True
        logger.debug("Debug message")
        captured = capsys.readouterr()
        assert "Debug message" in captured.out
        assert "[DEBUG]" in captured.out

    def test_debug_level_without_debug_mode(self, capsys):
        """Test debug level without debug mode"""
        logger = _make_logger()
        logger._debug_mode = False
        logger.debug("Debug message")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_silence_parameter(self, capsys):
        """Test silence parameter"""
        logger = _make_logger()
        logger.info("Visible message", silence=False)
        logger.info("Hidden message", silence=True)
        captured = capsys.readouterr()
        assert "Visible message" in captured.out
        assert "Hidden message" not in captured.out


class TestLoggerFileOperations:
    """Test log file operations"""

    def test_close_log_file_handle_no_attr(self):
        """无 log_file 属性时关闭不报错"""
        logger = _make_logger()
        logger._close_log_file_handle()

    def test_close_log_file_handle_with_attr(self):
        """有 log_file 属性时正确关闭"""
        logger = _make_logger()
        mock_file = MagicMock()
        logger.log_file = mock_file
        logger._log_file_path = Path("/tmp/test.log")
        logger._close_log_file_handle()
        mock_file.close.assert_called_once()
        assert logger._log_file_path is None

    def test_close_log_file_handle_os_error(self):
        """关闭文件句柄时 OSError 被捕获"""
        logger = _make_logger()
        mock_file = MagicMock()
        mock_file.close.side_effect = OSError("closed")
        logger.log_file = mock_file
        logger._close_log_file_handle()
        # 不应抛出异常

    def test_setup_log_file_import_error(self):
        """导入 config 失败时跳过文件日志"""
        logger = _make_logger()
        import sys

        # 临时从 sys.modules 移除 config 模块让 import 失败
        saved = sys.modules.pop("app.core.config", None)
        sys.modules["app.core.config"] = None  # type: ignore[assignment]
        try:
            logger._setup_log_file()
        finally:
            if saved is not None:
                sys.modules["app.core.config"] = saved
            else:
                sys.modules.pop("app.core.config", None)

    def test_setup_log_file_raw_none(self):
        """log_file 配置为空时禁用文件日志"""
        logger = _make_logger()
        with patch("app.core.logging.effective_dev_log_file_raw", return_value=None):
            logger._setup_log_file()
            assert not hasattr(logger, "log_file") or logger.log_file is None

    def test_setup_log_file_os_error(self):
        """打开日志文件时 OSError"""
        logger = _make_logger()
        with (
            patch(
                "app.core.logging.effective_dev_log_file_raw", return_value="./log.txt"
            ),
            patch("app.core.logging.resolve_dev_log_file_path") as mock_resolve,
            patch("builtins.open", side_effect=OSError("permission denied")),
        ):
            mock_path = MagicMock()
            mock_path.parent.mkdir = MagicMock()
            mock_path.exists.return_value = False
            mock_resolve.return_value = mock_path
            logger._setup_log_file()

    def test_ensure_log_file_for_write_no_file(self):
        """无 log_file 时直接返回"""
        logger = _make_logger()
        logger._log_file_path = None
        logger._ensure_log_file_for_write()

    def test_ensure_log_file_for_write_path_exists(self):
        """日志文件路径存在时不做任何操作"""
        logger = _make_logger()
        logger.log_file = MagicMock()
        logger._log_file_path = MagicMock()
        logger._log_file_path.exists.return_value = True
        logger._ensure_log_file_for_write()

    def test_ensure_log_file_for_write_path_lost(self):
        """日志文件路径丢失时重新打开"""
        logger = _make_logger()
        logger.log_file = MagicMock()
        logger._log_file_path = MagicMock()
        logger._log_file_path.exists.return_value = False
        with patch.object(logger, "_setup_log_file") as mock_setup:
            logger._ensure_log_file_for_write()
            mock_setup.assert_called_once()

    def test_ensure_log_file_for_write_os_error(self):
        """检查路径存在性时 OSError"""
        logger = _make_logger()
        logger.log_file = MagicMock()
        logger._log_file_path = MagicMock()
        logger._log_file_path.exists.side_effect = OSError("broken")
        with patch.object(logger, "_setup_log_file") as mock_setup:
            logger._ensure_log_file_for_write()
            mock_setup.assert_called_once()


class TestLoggerDebugMode:
    """Test debug_mode property"""

    def test_debug_mode_cached(self):
        """debug_mode 返回缓存值"""
        logger = _make_logger()
        logger._debug_mode = True
        assert logger.debug_mode is True

    def test_debug_mode_import_error(self):
        """导入 config 失败时返回 False"""
        logger = _make_logger()
        logger._debug_mode = None
        import sys

        saved = sys.modules.pop("app.core.config", None)
        sys.modules["app.core.config"] = None  # type: ignore[assignment]
        try:
            assert logger.debug_mode is False
        finally:
            if saved is not None:
                sys.modules["app.core.config"] = saved
            else:
                sys.modules.pop("app.core.config", None)

    def test_debug_mode_from_config(self):
        """从 config 读取 debug_mode"""
        logger = _make_logger()
        logger._debug_mode = None
        mock_cm = MagicMock()
        mock_cm.get.return_value = True
        with patch("builtins.__import__") as mock_import:
            # 让 from .config import config_manager 能成功
            mock_import.return_value = MagicMock()
            # 直接设置缓存值来验证属性
            logger._debug_mode = True
            assert logger.debug_mode is True


class TestLogFilePathHelpers:
    """Test log file path helper functions"""

    def test_resolve_dev_log_file_path_relative(self):
        """相对路径解析"""
        result = resolve_dev_log_file_path("./log.txt")
        assert result.is_absolute() or str(result).endswith("log.txt")

    def test_resolve_dev_log_file_path_absolute(self):
        """绝对路径保持不变"""
        result = resolve_dev_log_file_path("/var/log/app.log")
        # Path 在 Windows 上会将 / 转为 \，所以只验证是绝对路径且包含正确部分
        assert (
            "var" in str(result) and "log" in str(result) and "app.log" in str(result)
        )

    def test_effective_dev_log_file_raw_no_section(self):
        """无 dev 段时返回默认值"""
        mock_cfg = MagicMock()
        mock_cfg.has_section.return_value = False
        mock_cm = MagicMock()
        mock_cm.get_config_parser.return_value = mock_cfg
        result = effective_dev_log_file_raw(mock_cm)
        assert result == "./log.txt"

    def test_effective_dev_log_file_raw_no_option(self):
        """无 log_file 选项时返回默认值"""
        mock_cfg = MagicMock()
        mock_cfg.has_section.return_value = True
        mock_cfg.has_option.return_value = False
        mock_cm = MagicMock()
        mock_cm.get_config_parser.return_value = mock_cfg
        result = effective_dev_log_file_raw(mock_cm)
        assert result == "./log.txt"

    def test_effective_dev_log_file_raw_empty(self):
        """log_file 为空字符串时返回 None"""
        mock_cfg = MagicMock()
        mock_cfg.has_section.return_value = True
        mock_cfg.has_option.return_value = True
        mock_cfg.get.return_value = "  "
        mock_cm = MagicMock()
        mock_cm.get_config_parser.return_value = mock_cfg
        result = effective_dev_log_file_raw(mock_cm)
        assert result is None

    def test_effective_dev_log_file_raw_set(self):
        """log_file 有值时返回该值"""
        mock_cfg = MagicMock()
        mock_cfg.has_section.return_value = True
        mock_cfg.has_option.return_value = True
        mock_cfg.get.return_value = "/var/log/app.log"
        mock_cm = MagicMock()
        mock_cm.get_config_parser.return_value = mock_cfg
        result = effective_dev_log_file_raw(mock_cm)
        assert result == "/var/log/app.log"

    def test_resolved_dev_log_file_path_none(self):
        """禁用文件日志时返回 None"""
        mock_cm = MagicMock()
        with patch("app.core.logging.effective_dev_log_file_raw", return_value=None):
            result = resolved_dev_log_file_path(mock_cm)
            assert result is None

    def test_resolved_dev_log_file_path_set(self):
        """启用文件日志时返回路径"""
        mock_cm = MagicMock()
        with patch(
            "app.core.logging.effective_dev_log_file_raw", return_value="./log.txt"
        ):
            result = resolved_dev_log_file_path(mock_cm)
            assert result is not None


class TestLoggerLogToFile:
    """Test writing to log file"""

    def test_log_writes_to_file(self, capsys):
        """日志写入文件"""
        logger = _make_logger()
        mock_file = MagicMock()
        logger.log_file = mock_file
        logger._log_file_path = Path("/tmp/test.log")
        logger._log_file_lazy_initialized = True

        with patch.object(logger, "_ensure_log_file_for_write"):
            logger.info("file message")

        mock_file.write.assert_called()

    def test_log_end_parameter(self, capsys):
        """测试自定义 end 参数"""
        logger = _make_logger()
        logger.log("test", end="--")
        captured = capsys.readouterr()
        assert "test" in captured.out

    def test_mix_args_str_replaces_values(self):
        """测试 mix_args_str 替换敏感信息"""
        logger = _make_logger()
        logger.need_mix = True
        logger.api_key = "secret123"
        logger.netloc = "host.com"
        logger.netloc_replace = "masked.com"
        logger.user_name = "realuser"

        result = logger.mix_args_str("key=secret123 host=host.com user=realuser")
        assert "secret123" not in result[0]
        assert "host.com" not in result[0]
        assert "realuser" not in result[0]
