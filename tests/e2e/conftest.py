"""E2E 测试专用 fixture

提供共享的已登录浏览器上下文，避免每个测试重复登录流程。
test_server fixture 由 tests/conftest.py 在 HAS_PYTEST_PLAYWRIGHT=True 时定义。

提速策略：
- ``authed_storage_state`` (session)：整轮测试只登录一次，把 cookie 存成 dict
- ``authed_context`` (function)：每个测试新建独立 context，复用 storage_state，互相隔离
- ``authed_page`` (function)：从 authed_context 取 page，已登录
- ``_do_login`` (helper)：真正执行登录动作的内部函数，仅 session 级调一次
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(scope="session")
def base_url(test_server: str) -> str:
    """覆盖 pytest-playwright 的 base_url，指向 test_server

    设为 session 作用域，与 pytest-playwright 的 base_url 作用域一致。
    设置后 page.goto("/login") 会自动拼接为 test_server + "/login"。
    """
    return test_server


def _do_login(page, base_url: str) -> None:
    """在给定 page 上执行 admin/admin 登录，等到跳转到 dashboard"""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")
    page.fill("#username", "admin")
    page.fill("#password", "admin")
    page.click("#loginBtn")
    page.wait_for_url("**/dashboard", timeout=10000)
    page.wait_for_load_state("networkidle")


@pytest.fixture(scope="session")
def authed_storage_state(browser, base_url: str) -> dict[str, Any]:
    """整轮 E2E 只登录一次，返回 storage_state（含 cookie 等）

    用一个临时 context 登录后导出 storage_state，供后续所有测试复用。
    """
    context = browser.new_context()
    page = context.new_page()
    _do_login(page, base_url)
    state = context.storage_state()
    context.close()
    return state


@pytest.fixture
def authed_context(browser, authed_storage_state):
    """已登录的浏览器 context（每个测试独立，复用登录态 cookie）

    每个 function 级测试拿到一个全新 context，避免测试间状态串扰，
    但 cookie 来自 session 级登录，无需重复登录。
    """
    context = browser.new_context(storage_state=authed_storage_state)
    yield context
    context.close()


@pytest.fixture
def authed_page(authed_context):
    """已登录的浏览器 page

    登录态通过 storage_state 复用，整轮测试只登录一次。
    不主动导航，由测试自行决定起点（避免与测试内 goto 重复加载）。
    """
    page = authed_context.new_page()
    yield page
