"""LLM 配置表单 E2E 测试（Phase 1，Scenario 6.1-6.4）。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def _open_config_page(page, base_url: str):
    page.goto(f"{base_url}/config")
    page.wait_for_load_state("networkidle")


def test_llm_provider_select_renders(authed_page, base_url: str):
    """Scenario 6.1: LLM 卡片渲染 Provider 下拉框，默认 openai_compat。"""
    page = authed_page
    _open_config_page(page, base_url)

    select = page.locator("#llm-provider")
    assert select.is_visible()
    options = select.locator("option").all_inner_texts()
    assert options == ["openai_compat", "anthropic_compat"]
    assert select.input_value() == "openai_compat"


def test_llm_save_payload_includes_provider(authed_page, base_url: str):
    """保存并测试的 PUT 请求携带 provider（不再含 thinking_level）。"""
    page = authed_page
    _open_config_page(page, base_url)

    page.select_option("#llm-provider", "anthropic_compat")

    with page.expect_response(
        lambda r: "/api/llm/conf" in r.url and r.request.method == "PUT"
    ) as resp_info:
        page.get_by_role("button", name="保存并测试").click()

    resp = resp_info.value
    assert resp.status == 200
    body = resp.request.post_data_json
    assert body is not None
    assert body["provider"] == "anthropic_compat"
    # 不再发送 thinking_level
    assert "thinking_level" not in body


def test_llm_values_restored_after_reload(authed_page, base_url: str):
    """保存后刷新页面，下拉框回显保存值（仅 provider）。"""
    page = authed_page
    _open_config_page(page, base_url)

    page.select_option("#llm-provider", "anthropic_compat")
    with page.expect_response(
        lambda r: "/api/llm/conf" in r.url and r.request.method == "PUT"
    ):
        page.get_by_role("button", name="保存并测试").click()

    page.reload()
    page.wait_for_load_state("networkidle")
    assert page.locator("#llm-provider").input_value() == "anthropic_compat"
