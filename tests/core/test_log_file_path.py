"""
dev.log_file 解析与 Logger 文件重开相关测试
"""

from configparser import ConfigParser
from pathlib import Path
from unittest.mock import MagicMock, patch


def _mock_cm_with_ini(text: str) -> MagicMock:
    cfg = ConfigParser()
    cfg.read_string(text)
    m = MagicMock()
    m.get_config_parser = lambda: cfg
    return m


def test_effective_dev_log_file_raw_missing_option_uses_default():
    from app.core.logging import DEFAULT_DEV_LOG_FILE, effective_dev_log_file_raw

    cm = _mock_cm_with_ini("[dev]\ndebug = True\n")
    assert effective_dev_log_file_raw(cm) == DEFAULT_DEV_LOG_FILE


def test_effective_dev_log_file_raw_missing_dev_section_uses_default():
    from app.core.logging import DEFAULT_DEV_LOG_FILE, effective_dev_log_file_raw

    cm = _mock_cm_with_ini("[bangumi]\nx = 1\n")
    assert effective_dev_log_file_raw(cm) == DEFAULT_DEV_LOG_FILE


def test_effective_dev_log_file_raw_explicit_empty_disables():
    from app.core.logging import effective_dev_log_file_raw

    cm = _mock_cm_with_ini("[dev]\nlog_file = \n")
    assert effective_dev_log_file_raw(cm) is None

    cm2 = _mock_cm_with_ini("[dev]\nlog_file =    \n")
    assert effective_dev_log_file_raw(cm2) is None


def test_resolve_dev_log_file_path_dot_prefix():
    from app.core.logging import resolve_dev_log_file_path

    p = resolve_dev_log_file_path("./log.txt")
    assert p.name == "log.txt"
    assert p.is_absolute()


def test_resolved_dev_log_file_path_none_when_disabled():
    from app.core.logging import resolved_dev_log_file_path

    cm = _mock_cm_with_ini("[dev]\nlog_file =\n")
    assert resolved_dev_log_file_path(cm) is None


def test_logger_defers_file_open_until_first_log_line(tmp_path):
    """避免 __init__ 导入 config：构造后尚无 log_file，首次 log 再打开。"""
    from app.core.logging import Logger

    log_path = tmp_path / "defer.log"
    with (
        patch("app.core.logging.os.getlogin", side_effect=OSError()),
        patch("app.core.logging.os.environ.get", return_value="test_user"),
        patch(
            "app.core.logging.effective_dev_log_file_raw", return_value=str(log_path)
        ),
    ):
        lg = Logger()
        assert lg._log_file_lazy_initialized is False
        assert not hasattr(lg, "log_file")
        lg.need_mix = False
        lg.info("first")
        assert lg._log_file_lazy_initialized
        assert hasattr(lg, "log_file")
        assert log_path.exists()


def test_logger_reopens_when_path_reported_missing(tmp_path, monkeypatch):
    """模拟路径已不可用（不依赖 unlink 已打开文件），再次写入应走 _setup 重开。"""
    from app.core.logging import Logger

    log_path = tmp_path / "reopen_test.log"
    fixed = str(log_path)
    target = log_path.resolve()

    real_exists = Path.exists
    pretend_missing = {"on": False}

    def exists_wrapper(self):
        if pretend_missing["on"] and self.resolve() == target:
            return False
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", exists_wrapper)

    with (
        patch("app.core.logging.os.getlogin", side_effect=OSError()),
        patch("app.core.logging.os.environ.get", return_value="test_user"),
        patch("app.core.logging.effective_dev_log_file_raw", return_value=fixed),
    ):
        lg = Logger()
        lg.need_mix = False
        lg.info("before reopen")
        assert "before reopen" in log_path.read_text(encoding="utf-8")
        pretend_missing["on"] = True
        lg.info("after reopen")
        pretend_missing["on"] = False
        body = log_path.read_text(encoding="utf-8")
        assert "before reopen" in body
        assert "after reopen" in body
