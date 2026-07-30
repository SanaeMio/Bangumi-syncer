"""登录流程 E2E 测试

验证 admin/admin 登录成功、错误密码登录失败、登出后重定向。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def test_login_success(page, base_url: str):
    """admin/admin 登录成功，跳转到 dashboard"""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")

    page.fill("#username", "admin")
    page.fill("#password", "admin")
    page.click("#loginBtn")

    # 等待跳转
    page.wait_for_url("**/dashboard", timeout=10000)
    assert "/dashboard" in page.url


def test_login_wrong_password(page, base_url: str):
    """错误密码登录失败，显示错误提示，不跳转"""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")

    page.fill("#username", "admin")
    page.fill("#password", "wrong_password")
    page.click("#loginBtn")

    # 等待错误提示出现（alert-container 内的提示）
    page.wait_for_timeout(2000)

    # 仍在登录页
    assert "/login" in page.url

    # 检查是否有错误提示（alert-container 或 toast）
    alert_visible = page.evaluate(
        """() => {
            const alert = document.getElementById('alert-container');
            if (alert && alert.textContent.trim()) return true;
            // 也检查 toast 类提示
            const toasts = document.querySelectorAll('.toast-body, .alert-danger');
            return Array.from(toasts).some(t => t.textContent.trim());
        }"""
    )
    assert alert_visible, "登录失败未显示错误提示"


def test_logout_redirects_to_login(page, base_url: str):
    """登录后通过 API 登出，再访问 dashboard 重定向到 login"""
    # 先登录
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")
    page.fill("#username", "admin")
    page.fill("#password", "admin")
    page.click("#loginBtn")
    page.wait_for_url("**/dashboard", timeout=10000)

    # 通过 API 登出（携带 session cookie）
    page.evaluate(
        """async (url) => {
            await fetch(url + '/api/logout', {method: 'POST', credentials: 'include'});
        }""",
        base_url,
    )

    # 再访问 dashboard，应重定向到 login
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")
    assert "/login" in page.url, f"登出后未重定向到登录页，当前 URL: {page.url}"


def test_authed_access_to_dashboard(page, base_url: str):
    """登录后直接访问 dashboard 不会被重定向到 login"""
    # 先登录
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")
    page.fill("#username", "admin")
    page.fill("#password", "admin")
    page.click("#loginBtn")
    page.wait_for_url("**/dashboard", timeout=10000)

    # 直接访问 dashboard
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")
    assert "/dashboard" in page.url
    assert "/login" not in page.url
