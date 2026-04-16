"""
StartupInfo tests - Simplified version
"""

from unittest.mock import patch


class TestStartupInfoSimple:
    """Test StartupInfo class with simplified tests"""

    def test_startup_info_init(self):
        """Test initialization"""
        with (
            patch("app.core.startup_info.platform.system", return_value="Linux"),
            patch("app.core.startup_info.sys.stdout.isatty", return_value=True),
        ):
            from app.core.startup_info import StartupInfo

            info = StartupInfo()
            assert hasattr(info, "supports_color")
            assert hasattr(info, "COLORS")
            assert "reset" in info.COLORS
            assert "red" in info.COLORS

    def test_colorize_with_color(self):
        """Test colorize with color support"""
        with (
            patch("app.core.startup_info.platform.system", return_value="Linux"),
            patch("app.core.startup_info.sys.stdout.isatty", return_value=True),
        ):
            from app.core.startup_info import StartupInfo

            info = StartupInfo()
            info.supports_color = True

            result = info.colorize("test", "red")
            assert "\033[31m" in result
            assert "test" in result

    def test_colorize_without_color(self):
        """Test colorize without color support"""
        from app.core.startup_info import StartupInfo

        info = StartupInfo()
        info.supports_color = False

        result = info.colorize("test", "red")
        assert result == "test"

    def test_print_success(self, capsys):
        """Test success message"""
        from app.core.startup_info import StartupInfo

        info = StartupInfo()
        info.supports_color = False

        info.print_success("Operation successful")

        captured = capsys.readouterr()
        assert "Operation successful" in captured.out
        assert "✅" in captured.out

    def test_print_warning(self, capsys):
        """Test warning message"""
        from app.core.startup_info import StartupInfo

        info = StartupInfo()
        info.supports_color = False

        info.print_warning("Warning message")

        captured = capsys.readouterr()
        assert "Warning message" in captured.out

    def test_print_error(self, capsys):
        """Test error message"""
        from app.core.startup_info import StartupInfo

        info = StartupInfo()
        info.supports_color = False

        info.print_error("Error message")

        captured = capsys.readouterr()
        assert "Error message" in captured.out

    def test_print_info(self, capsys):
        """Test info message"""
        from app.core.startup_info import StartupInfo

        info = StartupInfo()
        info.supports_color = False

        info.print_info("Info message")

        captured = capsys.readouterr()
        assert "Info message" in captured.out

    def test_print_separator(self, capsys):
        """Test separator printing"""
        from app.core.startup_info import StartupInfo

        info = StartupInfo()
        info.supports_color = False

        info.print_separator()

        captured = capsys.readouterr()
        assert "─" in captured.out

    def test_print_system_info(self, capsys, temp_dir):
        """Test system info printing"""
        from app.core.startup_info import StartupInfo

        info = StartupInfo()
        info.supports_color = False

        config_path = temp_dir / "config.ini"
        info.print_system_info(config_path)

        captured = capsys.readouterr()
        assert "系统信息" in captured.out

    def test_print_startup_complete_non_docker(self, capsys):
        """Test startup complete for non-Docker"""
        from app.core.startup_info import StartupInfo

        info = StartupInfo()
        info.supports_color = False

        with (
            patch("app.core.startup_info.os.environ.get", return_value=None),
            patch("app.core.startup_info.os.path.exists", return_value=False),
        ):
            info.print_startup_complete(host="0.0.0.0", port=8000)

            captured = capsys.readouterr()
            assert "启动完成" in captured.out

    def test_check_color_support_no_color_env(self):
        """Test color support with NO_COLOR"""
        with patch("app.core.startup_info.os.environ.get") as mock_get:
            mock_get.return_value = "1"

            from app.core.startup_info import StartupInfo

            info = StartupInfo()
            result = info._check_color_support()
            assert result is False

    def test_check_color_support_term_dumb(self):
        """Test color support with TERM=dumb"""
        with patch("app.core.startup_info.os.environ.get") as mock_get:
            mock_get.side_effect = lambda k, d=None: {"TERM": "dumb"}.get(k, d)

            from app.core.startup_info import StartupInfo

            info = StartupInfo()
            result = info._check_color_support()
            assert result is False

    def test_colors_dict(self):
        """Test colors dictionary"""
        from app.core.startup_info import StartupInfo

        info = StartupInfo()
        expected_colors = ["reset", "bold", "red", "green", "yellow", "blue"]
        for color in expected_colors:
            assert color in info.COLORS

    def test_print_startup_progress(self, capsys):
        from app.core.startup_info import StartupInfo

        info = StartupInfo()
        info.supports_color = False
        info.print_startup_progress(2, 4, "loading")
        out = capsys.readouterr().out
        assert "50%" in out or "loading" in out

    def test_print_banner_defaults(self, capsys):
        from app.core.startup_info import StartupInfo

        info = StartupInfo()
        info.supports_color = False
        info.print_banner()
        out = capsys.readouterr().out
        assert "Bangumi" in out or "v" in out

    def test_print_startup_complete_docker_branch(self, capsys, monkeypatch):
        from app.core.startup_info import StartupInfo

        monkeypatch.setenv("DOCKER_CONTAINER", "1")
        info = StartupInfo()
        info.supports_color = False
        info.print_startup_complete(host="0.0.0.0", port=9000)
        out = capsys.readouterr().out
        assert "localhost:9000" in out
        assert "容器内" in out

    def test_check_color_windows_powershell(self, monkeypatch):
        from unittest.mock import MagicMock

        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        with patch("app.core.startup_info.platform.system", return_value="Windows"):
            with patch("app.core.startup_info.subprocess.run") as run:
                run.return_value = MagicMock(stdout="Windows PowerShell", stderr="")
                from app.core.startup_info import StartupInfo

                assert StartupInfo().supports_color is True

    def test_check_color_windows_subprocess_fails(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        with patch("app.core.startup_info.platform.system", return_value="Windows"):
            with patch(
                "app.core.startup_info.subprocess.run",
                side_effect=TimeoutError(),
            ):
                from app.core.startup_info import StartupInfo

                assert StartupInfo().supports_color is False
