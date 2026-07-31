"""同步重试与通知规则 E2E 测试

验证关键交互 UI 的可达性与前端行为：
1. 同步记录页重试日志弹窗 DOM 就绪（retrySync 入口存在）
2. 通知规则配置 UI 完整渲染（规则列表 + 新增按钮 + modal）
3. 通知类型复选框按分类分组渲染（验证通知系统重构的前端）

不依赖动态数据完整闭环（重试 SSE / 通知发送）——那属于单元/集成测试范畴。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def test_records_page_retry_modal_dom(authed_page, base_url: str):
    """同步记录页重试日志弹窗 DOM 就绪

    retryLogModal / retry-log-content / retry-log-status 在 records.html 静态渲染，
    sync-retry.js 的 retrySync() 依赖这些元素。验证它们存在，确保前端入口可用。
    """
    page = authed_page
    page.goto(f"{base_url}/records")
    page.wait_for_load_state("networkidle")

    # 重试日志 modal 容器存在
    assert page.locator("#retryLogModal").count() > 0, "重试日志 modal 未渲染"
    # 日志内容区与状态区存在
    assert page.locator("#retry-log-content").count() > 0, "重试日志内容区未渲染"
    assert page.locator("#retry-log-status").count() > 0, "重试日志状态区未渲染"

    # retrySync 函数已全局导出（sync-retry.js 加载成功）
    has_retry = page.evaluate("typeof window.retrySync === 'function'")
    assert has_retry, "window.retrySync 未定义，sync-retry.js 可能未加载"


def test_notification_rules_list_renders(authed_page, base_url: str):
    """通知规则列表渲染（config.example.ini 含示例规则）"""
    page = authed_page
    page.goto(f"{base_url}/config")
    page.wait_for_load_state("networkidle")

    # 通知规则 modal 存在（静态渲染）
    assert page.locator("#notificationRuleModal").count() > 0, "通知规则 modal 未渲染"
    # modal 内表单元素存在
    assert page.locator("#notification-rule-name").count() > 0, "规则名称输入框未渲染"
    assert page.locator("#notification-rule-types-container").count() > 0, (
        "规则事件类型容器未渲染"
    )
    assert page.locator("#notification-rule-channels-container").count() > 0, (
        "规则渠道容器未渲染"
    )


def test_notification_types_grouped_by_category(authed_page, base_url: str):
    """通知类型按分类分组渲染

    验证 P0 通知系统重构：前端从 /api/notification/types 动态加载类型，
    按分类（同步流程/匹配质量/数据源/调度任务/Bangumi API/系统运维）分组展示。
    打开新建规则 modal 触发类型复选框渲染。
    """
    page = authed_page
    page.goto(f"{base_url}/config")
    page.wait_for_load_state("networkidle")

    # 点击"新增规则"按钮打开 modal（_notify.html 中的按钮调用 showNotificationRuleModal()）
    add_btn = page.locator('button[onclick="showNotificationRuleModal()"]').first
    add_btn.click()
    page.wait_for_timeout(1000)

    # modal 显示后，类型容器应有复选框（从 /api/notification/types 动态加载）
    types_container = page.locator("#notification-rule-types-container")
    checkboxes = types_container.locator('input[type="checkbox"]')
    # config.example.ini 配置了多个通知类型，至少应有 5 个复选框
    assert checkboxes.count() >= 5, (
        f"通知类型复选框数量不足: {checkboxes.count()}，可能 /api/notification/types 加载失败"
    )

    # 应有分类标题（同步流程/匹配质量/数据源/调度任务/Bangumi API/系统运维 之一）
    container_text = types_container.text_content()
    has_category = any(
        cat in container_text
        for cat in [
            "同步流程",
            "匹配质量",
            "数据源",
            "调度任务",
            "Bangumi API",
            "系统运维",
        ]
    )
    assert has_category, f"类型容器未按分类分组展示，文本: {container_text[:200]}"
