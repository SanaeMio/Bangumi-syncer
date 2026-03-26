"""
Logger tests - Simplified version
"""

from unittest.mock import patch


class TestLoggerSimple:
    """Test Logger class with simplified tests"""

    def test_mix_host_gen(self):
        """Test host mixing function"""
        from app.core.logging import Logger

        result1 = Logger.mix_host_gen("example.com:8080")
        assert "_mix_host_" in result1
        assert "8080" in result1

        result2 = Logger.mix_host_gen("example.com")
        assert "_mix_host_" in result2

    def test_logger_init(self):
        """Test logger initialization"""
        with (
            patch("app.core.logging.os.getlogin", side_effect=OSError()),
            patch("app.core.logging.os.environ.get", return_value="test_user"),
        ):
            from app.core.logging import Logger

            logger = Logger()
            assert hasattr(logger, "need_mix")
            assert hasattr(logger, "api_key")

    def test_mix_args_str(self):
        """Test argument string mixing"""
        with (
            patch("app.core.logging.os.getlogin", side_effect=OSError()),
            patch("app.core.logging.os.environ.get", return_value="test_user"),
        ):
            from app.core.logging import Logger

            logger = Logger()
            logger.need_mix = False  # Disable mixing for testing

            # Test that function returns list
            args = ["message", "test"]
            result = logger.mix_args_str(*args)
            assert isinstance(result, list)

    def test_log_output(self, capsys):
        """Test log output"""
        with (
            patch("app.core.logging.os.getlogin", side_effect=OSError()),
            patch("app.core.logging.os.environ.get", return_value="test_user"),
        ):
            from app.core.logging import Logger

            logger = Logger()
            logger.need_mix = False

            logger.info("Test message")

            captured = capsys.readouterr()
            assert "Test message" in captured.out

    def test_log_with_level(self, capsys):
        """Test log with level prefix"""
        with (
            patch("app.core.logging.os.getlogin", side_effect=OSError()),
            patch("app.core.logging.os.environ.get", return_value="test_user"),
        ):
            from app.core.logging import Logger

            logger = Logger()
            logger.need_mix = False

            logger.log("Test message", level="INFO")

            captured = capsys.readouterr()
            assert "[INFO]" in captured.out

    def test_info_level(self, capsys):
        """Test info level logging"""
        with (
            patch("app.core.logging.os.getlogin", side_effect=OSError()),
            patch("app.core.logging.os.environ.get", return_value="test_user"),
        ):
            from app.core.logging import Logger

            logger = Logger()
            logger.need_mix = False

            logger.info("Info message")

            captured = capsys.readouterr()
            assert "[INFO]" in captured.out

    def test_warning_level(self, capsys):
        """Test warning level logging"""
        with (
            patch("app.core.logging.os.getlogin", side_effect=OSError()),
            patch("app.core.logging.os.environ.get", return_value="test_user"),
        ):
            from app.core.logging import Logger

            logger = Logger()
            logger.need_mix = False

            logger.warning("Warning message")

            captured = capsys.readouterr()
            assert "[WARNING]" in captured.out

    def test_error_level(self, capsys):
        """Test error level logging"""
        with (
            patch("app.core.logging.os.getlogin", side_effect=OSError()),
            patch("app.core.logging.os.environ.get", return_value="test_user"),
        ):
            from app.core.logging import Logger

            logger = Logger()
            logger.need_mix = False

            logger.error("Error message")

            captured = capsys.readouterr()
            assert "[ERROR]" in captured.out

    def test_debug_level_with_debug_mode(self, capsys):
        """Test debug level with debug mode enabled"""
        with (
            patch("app.core.logging.os.getlogin", side_effect=OSError()),
            patch("app.core.logging.os.environ.get", return_value="test_user"),
        ):
            from app.core.logging import Logger

            logger = Logger()
            logger.need_mix = False
            logger._debug_mode = True

            logger.debug("Debug message")

            captured = capsys.readouterr()
            assert "Debug message" in captured.out
            assert "[DEBUG]" in captured.out

    def test_debug_level_without_debug_mode(self, capsys):
        """Test debug level without debug mode"""
        with (
            patch("app.core.logging.os.getlogin", side_effect=OSError()),
            patch("app.core.logging.os.environ.get", return_value="test_user"),
        ):
            from app.core.logging import Logger

            logger = Logger()
            logger.need_mix = False
            logger._debug_mode = False

            logger.debug("Debug message")

            captured = capsys.readouterr()
            assert captured.out == ""

    def test_silence_parameter(self, capsys):
        """Test silence parameter"""
        with (
            patch("app.core.logging.os.getlogin", side_effect=OSError()),
            patch("app.core.logging.os.environ.get", return_value="test_user"),
        ):
            from app.core.logging import Logger

            logger = Logger()

            logger.info("Visible message", silence=False)
            logger.info("Hidden message", silence=True)

            captured = capsys.readouterr()
            assert "Visible message" in captured.out
            assert "Hidden message" not in captured.out
