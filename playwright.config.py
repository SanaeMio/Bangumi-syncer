"""
Playwright 配置文件
用于端到端测试

使用 pytest-playwright 时，配置通过 pytest.ini_options 或 pyproject.toml 设置
此文件提供额外的 Playwright 配置
"""

import os
from pathlib import Path

# 项目根目录
ROOT_DIR = Path(__file__).parent


def get_app_url():
    """获取应用 URL"""
    return os.environ.get("TEST_APP_URL", "http://localhost:8000")
