"""E2E 测试专用 fixture

提供登录态浏览器 page，避免每个测试重复登录流程。
test_server fixture 由 tests/conftest.py 在 HAS_PYTEST_PLAYWRIGHT=True 时定义。
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def base_url(test_server: str) -> str:
    """覆盖 pytest-playwright 的 base_url，指向 test_server

    设为 session 作用域，与 pytest-playwright 的 base_url 作用域一致。
    设置后 page.goto("/login") 会自动拼接为 test_server + "/login"。
    """
    return test_server


@pytest.fixture
def authed_page(page, base_url: str):
    """已登录的浏览器 page

    自动用 admin/admin 登录，后续测试直接操作已认证页面。
    登出由 page 的 fixture 生命周期自动清理。
    """
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")

    page.fill("#username", "admin")
    page.fill("#password", "admin")
    page.click("#loginBtn")

    page.wait_for_url("**/dashboard", timeout=10000)
    page.wait_for_load_state("networkidle")

    yield page
