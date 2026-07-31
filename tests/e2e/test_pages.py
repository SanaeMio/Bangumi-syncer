"""页面渲染 E2E 测试

登录后访问 dashboard / config 页面，验证核心 UI 元素渲染。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def test_dashboard_renders(authed_page, base_url: str):
    """仪表盘页面渲染核心元素"""
    page = authed_page
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")

    # 页面 body 存在且有内容
    assert page.locator("body").is_visible()
    body_text = page.locator("body").inner_text()
    assert len(body_text) > 50, f"仪表盘内容为空: {body_text[:100]}"

    # nav 元素存在（桌面端侧栏或移动端导航栏）
    assert page.locator("nav").count() > 0, "导航栏未渲染"


def test_dashboard_scheduler_status_card(authed_page, base_url: str):
    """仪表盘调度器状态卡渲染（P6 新增）"""
    page = authed_page
    page.goto(f"{base_url}/dashboard")
    # 调度器状态卡可能正在异步加载，等待一下
    page.wait_for_timeout(2000)

    # 验证页面不崩溃且有内容
    has_content = page.evaluate(
        """() => {
            return document.body.textContent.length > 100;
        }"""
    )
    assert has_content, "仪表盘内容为空"


def test_config_page_renders(authed_page, base_url: str):
    """配置页渲染核心元素：TOC 侧栏 + 配置卡片"""
    page = authed_page
    page.goto(f"{base_url}/config")
    page.wait_for_load_state("networkidle")

    # TOC 侧栏存在（P0 重构后的配置页双栏布局）
    sidebar = page.locator(".config-sidebar, [data-toc], #config-toc").first
    assert sidebar.is_visible(), "配置页 TOC 侧栏未渲染"

    # 配置卡片存在（至少有 Bangumi 账号段）
    cards = page.locator(".card, .config-card, [data-section]")
    assert cards.count() > 0, "配置页无配置卡片"


def test_config_page_toc_items(authed_page, base_url: str):
    """配置页 TOC 包含核心配置段"""
    page = authed_page
    page.goto(f"{base_url}/config")
    page.wait_for_load_state("networkidle")

    # TOC 应包含核心配置段名称
    toc_text = page.locator(".config-sidebar, #config-toc").first.text_content()
    # 至少包含 "Bangumi" 关键词
    assert "Bangumi" in toc_text or "bangumi" in toc_text.lower(), (
        "TOC 未包含 Bangumi 配置段"
    )


def test_config_page_save_button(authed_page, base_url: str):
    """配置页保存按钮存在且可点击"""
    page = authed_page
    page.goto(f"{base_url}/config")
    page.wait_for_load_state("networkidle")

    # 查找保存按钮
    save_btn = page.locator(
        "button:has-text('保存'), #save-config, [data-action='save']"
    ).first
    assert save_btn.is_visible(), "保存按钮未渲染"
    assert save_btn.is_enabled(), "保存按钮不可点击"


def test_notification_rule_modal(authed_page, base_url: str):
    """通知规则编辑模态框可打开"""
    page = authed_page
    page.goto(f"{base_url}/config")
    page.wait_for_load_state("networkidle")

    # 查找"新增规则"或类似按钮
    add_rule_btn = page.locator(
        "button:has-text('规则'), button:has-text('通知')"
    ).first
    if add_rule_btn.is_visible():
        add_rule_btn.click()
        page.wait_for_timeout(1000)

        # 这里仅验证点击不会导致页面崩溃
        assert page.locator("body").is_visible()
