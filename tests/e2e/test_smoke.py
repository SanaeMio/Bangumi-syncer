"""冒烟 E2E 测试：验证服务器启动和基础页面可访问

不需要认证的端点和页面。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def test_health_endpoint(test_server: str):
    """健康检查端点返回 200"""
    import httpx

    resp = httpx.get(f"{test_server}/health", timeout=5)
    assert resp.status_code == 200


def test_login_page_displays(page, base_url: str):
    """登录页正常渲染，包含用户名/密码输入框和提交按钮"""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")

    # 标题存在
    assert "Bangumi" in page.title()

    # 核心表单元素存在
    assert page.is_visible("#username")
    assert page.is_visible("#password")
    assert page.is_visible("#loginBtn")

    # 登录按钮可点击（非 disabled）
    assert page.is_enabled("#loginBtn")


def test_static_css_loaded(page, base_url: str):
    """静态 CSS 资源正常加载（style.css 拆分后的 6 个文件均 200）"""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")

    # style.css 已按功能拆分为 6 个独立文件，逐一校验加载
    expected = [
        "theme-base.css",
        "records-detail.css",
        "components.css",
        "pages.css",
        "dashboard.css",
        "effects.css",
    ]
    loaded = page.evaluate(
        """(expected) => {
            const links = Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
                .map(l => l.href);
            return expected.filter(name => links.some(s => s.includes(name)));
        }""",
        expected,
    )
    missing = set(expected) - set(loaded)
    assert not missing, f"以下 CSS 文件未被加载: {missing}"


def test_static_js_loaded(page, base_url: str):
    """静态 JS 资源正常加载（app.js 拆分后的 7 个模块均 200）"""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")

    # app.js 已按功能拆分为 7 个独立文件，逐一校验加载
    expected = [
        "api-utils.js",
        "toast-ui.js",
        "ui-utils.js",
        "records-detail.js",
        "auth.js",
        "sync-retry.js",
        "theme.js",
    ]
    loaded = page.evaluate(
        """(expected) => {
            const srcs = Array.from(document.querySelectorAll('script[src]'))
                .map(s => s.src);
            return expected.filter(name => srcs.some(s => s.includes(name)));
        }""",
        expected,
    )
    missing = set(expected) - set(loaded)
    assert not missing, f"以下 JS 模块未被加载: {missing}"


def test_root_redirects_when_unauthed(page, base_url: str):
    """未登录访问根路径重定向到 /login"""
    page.goto(f"{base_url}/")
    page.wait_for_load_state("networkidle")

    # 未登录应跳转到 /login
    assert "/login" in page.url
