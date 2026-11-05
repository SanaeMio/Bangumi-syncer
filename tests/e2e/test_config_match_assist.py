"""「匹配增强（LLM）」开关条件渲染 E2E。

依赖浏览器，CI 执行；本地无浏览器时由 tests/api/test_config_match_assist.py
的渲染测试 + 手测清单覆盖。

场景：
- LLM 未配置（/api/config 的 llm.api_key 为空）：开关不显示 + 展示「需先配置 LLM」提示
- LLM 已配置（llm.api_key 非空）：开关显示；读取当前值；保存走 /api/config

开关显隐由 loadConfig() 内的 loadLLMMatchAssist(config) 依据已加载的
/api/config 数据控制，不再有独立的 /api/sync/config 请求。
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.e2e


def _open_config_page(page, base_url: str):
    page.goto(f"{base_url}/config")
    page.wait_for_load_state("networkidle")


def test_match_assist_switch_hidden_when_llm_not_configured(authed_page, base_url: str):
    """LLM 未配置 → 开关不显示 + 提示「需先配置 LLM」。"""
    page = authed_page
    _open_config_page(page, base_url)

    switch = page.locator("#llm-match-assist-switch")
    tip = page.locator("#llm-match-assist-tip")

    # 等待条件渲染完成（/api/config 数据就绪后 JS 控制显隐）
    tip.wait_for(state="visible", timeout=10000)

    # 未配置时：提示可见、开关不可见
    assert tip.is_visible()
    assert "需先配置 LLM" in tip.inner_text()
    assert not switch.is_visible()


def test_match_assist_switch_visible_when_llm_configured(authed_page, base_url: str):
    """LLM 已配置 → 开关显示；保存生效（sync.llm_match_assist 写入）。"""
    page = authed_page

    # 自行配置 LLM（写入 api_key），使 /api/config 的 llm.api_key 非空
    put = page.request.put(
        f"{base_url}/api/llm/conf",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "api_base": "https://example.com/v1",
                "api_key": "sk-test-1234",
                "model": "gpt-4o-mini",
                "max_tokens": 2000,
                "temperature": 0.7,
                "timeout": 60,
                "provider": "openai_compat",
            }
        ),
    )
    assert put.status == 200

    _open_config_page(page, base_url)

    switch = page.locator("#llm-match-assist-switch")
    tip = page.locator("#llm-match-assist-tip")

    # 等待条件渲染完成（/api/config 数据就绪后 JS 控制显隐）
    switch.wait_for(state="visible", timeout=10000)

    # 已配置时：开关可见、提示不可见
    assert switch.is_visible()
    assert not tip.is_visible()

    # 切换开关并保存，验证请求携带 sync.llm_match_assist
    page.locator("#llm-match-assist").check()
    with page.expect_response(
        lambda r: "/api/config" in r.url and r.request.method == "POST"
    ) as resp_info:
        # 侧栏与移动端 fallback 各有一个「保存配置」，取可见的那个
        page.locator("#config-section-actions").get_by_role(
            "button", name="保存配置"
        ).click()

    resp = resp_info.value
    assert resp.status == 200
    body = resp.request.post_data_json or {}
    assert body.get("sync", {}).get("llm_match_assist") is True
