"""总结任务弹窗思考强度下拉 E2E 测试。

覆盖：
1. 新建弹窗思考强度默认 off
2. 编辑弹窗回填思考强度
3. 保存提交思考强度
4. 弹窗渲染思考强度控件（含四个选项）
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def _open_config_page(page, base_url: str):
    page.goto(f"{base_url}/config")
    page.wait_for_load_state("networkidle")


def _open_new_summary_modal(page, base_url: str):
    _open_config_page(page, base_url)
    page.get_by_role("button", name="新建任务").first.click()
    page.wait_for_selector("#summaryJobModal.show", timeout=5000)


def test_新建弹窗思考强度默认_off(authed_page, base_url: str):
    """场景 1：打开新建总结任务弹窗 → 下拉选中值为 off。"""
    page = authed_page
    _open_new_summary_modal(page, base_url)

    select = page.locator("#summary-thinking-level")
    assert select.is_visible()
    assert select.input_value() == "off"


def test_弹窗渲染思考强度控件(authed_page, base_url: str):
    """场景 4：#summaryJobModal 内存在 #summary-thinking-level 且含四个选项。"""
    page = authed_page
    _open_new_summary_modal(page, base_url)

    select = page.locator("#summary-thinking-level")
    assert select.is_visible()
    options = select.locator("option").all_inner_texts()
    # 四个选项：off / low / medium / high
    assert len(options) == 4
    values = select.locator("option").evaluate_all("els => els.map(e => e.value)")
    assert values == ["off", "low", "medium", "high"]


def test_保存提交思考强度(authed_page, base_url: str):
    """场景 3：修改为 medium 并保存 → 请求 payload 含 thinking_level=medium。"""
    page = authed_page
    _open_new_summary_modal(page, base_url)

    page.locator("#summary-job-name").fill("E2E思考强度任务")
    page.select_option("#summary-thinking-level", "medium")

    with page.expect_response(
        lambda r: "/api/summary/jobs" in r.url and r.request.method == "POST"
    ) as resp_info:
        page.get_by_role("button", name="保存", exact=True).click()

    resp = resp_info.value
    assert resp.status == 200
    body = resp.request.post_data_json
    assert body["thinking_level"] == "medium"


def test_编辑弹窗回填思考强度(authed_page, base_url: str):
    """场景 2：给定 thinking_level=low 的任务，打开编辑弹窗 → 下拉回填 low。"""
    page = authed_page
    _open_new_summary_modal(page, base_url)

    # 先创建一个 thinking_level=low 的任务
    page.locator("#summary-job-name").fill("E2E回填任务")
    page.select_option("#summary-thinking-level", "low")
    with page.expect_response(
        lambda r: "/api/summary/jobs" in r.url and r.request.method == "POST"
    ):
        page.get_by_role("button", name="保存", exact=True).click()

    # 等待保存成功 toast，并确保模态框完全关闭（动画结束）再操作列表
    page.wait_for_selector(".toast", timeout=5000)
    page.wait_for_function(
        "() => !document.getElementById('summaryJobModal').classList.contains('show')"
    )

    # 编辑该任务
    page.locator("#summary-jobs-list .card", has_text="E2E回填任务").get_by_role(
        "button", name="编辑"
    ).click()
    page.wait_for_selector("#summaryJobModal.show", timeout=5000)

    select = page.locator("#summary-thinking-level")
    assert select.input_value() == "low"
