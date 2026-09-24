"""MCP 服务公共 URL 一键填入当前访问地址（配置页）测试

目标：验证「配置 → 开发与代理」中 MCP 服务公共 URL 字段旁提供
「检测到当前访问地址」提示与「一键填入」按钮；点击仅把
``window.location.origin`` 填入输入框，不自动保存、不自动覆盖已有值。

参照项目既有模式：HTML partial（``templates/config/_advanced.html``）里放
按钮与 ``onclick``，全局函数定义在 ``templates/config.html`` 的脚本区
（同 ``showProxyHelper`` / ``testHostConnectivity``）。测试以页面渲染结果为
准做静态校验（与 ``test_config_page_comments.py`` 同风格）。
"""

import re
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import pages


def _fetch_config_html():
    """以已认证用户访问 /config，返回完整页面 HTML。"""
    app = FastAPI()
    app.include_router(pages.router)
    with patch.object(
        pages, "get_current_user_from_cookie", return_value={"username": "u"}
    ):
        client = TestClient(app)
        response = client.get("/config", follow_redirects=False)
    assert response.status_code == 200, f"期望 200，实际 {response.status_code}"
    return response.text


def _mcp_segment(text):
    """截取 mcp-base-url 字段（label 起）到下一个字段（bgm-api-proxy）之间的片段。"""
    start = text.index('for="mcp-base-url"')
    end = text.index('id="bgm-api-proxy"')
    return text[start:end]


def _function_body(text, signature):
    """截取顶层函数体：从签名到下一个顶层 ``function `` 定义之前。"""
    start = text.index(signature)
    end = text.index("function ", start + len(signature))
    return text[start:end]


# ---------------------------------------------------------------------------
# 1. 提示行与「一键填入」按钮
# ---------------------------------------------------------------------------


def test_mcp_base_url_hint_shows_detected_origin_placeholder():
    """字段下方应有「检测到当前访问地址」提示，且带脚本回填占位元素。"""
    seg = _mcp_segment(_fetch_config_html())
    assert "检测到当前访问地址" in seg, (
        "MCP 服务公共 URL 字段下方应显示『检测到当前访问地址』提示"
    )
    assert 'id="mcp-detected-origin"' in seg, (
        '提示中应包含 id="mcp-detected-origin" 元素，供脚本填入 window.location.origin'
    )
    assert "一键填入" in seg, "字段下方应有『一键填入』按钮"


def test_mcp_base_url_fill_button_uses_global_handler():
    """「一键填入」按钮应为 type=button 且 onclick 调用全局 fillMcpBaseUrl()。"""
    seg = _mcp_segment(_fetch_config_html())
    button = re.search(
        r"<button\b(?=[^>]*\bonclick=\"fillMcpBaseUrl\(\)\")[^>]*>",
        seg,
        re.DOTALL,
    )
    assert button is not None, '应存在 onclick="fillMcpBaseUrl()" 的按钮'
    assert 'type="button"' in button.group(0), (
        "一键填入按钮必须是 type=button，避免触发表单提交/保存"
    )


def test_mcp_base_url_hint_explains_reverse_proxy_case():
    """提示文案需说明反代/局域网/域名部署场景与含端口、不含子路径。"""
    seg = _mcp_segment(_fetch_config_html())
    assert "反代" in seg and "局域网" in seg and "域名" in seg, (
        "提示应覆盖反代 / 局域网 / 域名部署场景"
    )
    assert "MCP 客户端将通过该地址访问" in seg, "提示应说明 MCP 客户端将通过该地址访问"


def test_mcp_base_url_existing_tooltip_keeps_priority_and_restart_info():
    """既有 tooltip 的关键信息（OAuth issuer、优先级、重启生效）不得丢失。"""
    seg = _mcp_segment(_fetch_config_html())
    assert "OAuth issuer" in seg, "tooltip 应保留『OAuth issuer』说明"
    assert "重启服务才生效" in seg, "tooltip 应保留『修改后需要重启服务才生效』说明"
    assert "MCP_BASE_URL" in seg, "tooltip 应保留环境变量优先级说明"


# ---------------------------------------------------------------------------
# 2. 脚本行为：仅点击时填入，不自动保存 / 不覆盖已有值
# ---------------------------------------------------------------------------


def test_fill_mcp_base_url_function_reads_origin_into_input():
    """fillMcpBaseUrl 应把 window.location.origin 写入 mcp-base-url 输入框。"""
    body = _function_body(_fetch_config_html(), "function fillMcpBaseUrl(")
    assert "window.location.origin" in body, (
        "fillMcpBaseUrl 应读取 window.location.origin"
    )
    assert "mcp-base-url" in body, "fillMcpBaseUrl 应定位 mcp-base-url 输入框"
    assert ".value" in body, "fillMcpBaseUrl 应把地址写入输入框的 value"


def test_fill_mcp_base_url_does_not_autosave_or_force_overwrite():
    """一键填入只填表，不得调用保存接口、不得自动覆盖已有非空值。"""
    body = _function_body(_fetch_config_html(), "function fillMcpBaseUrl(")
    assert "apiFetch" not in body, "一键填入不得调用 apiFetch（不自动保存）"
    assert "save" not in body.lower(), (
        "一键填入不得触发保存逻辑，仅写入输入框由页面保存按钮落库"
    )
    # 仅当输入框为空时才填入，避免覆盖用户已有配置。
    assert "value" in body and ("trim()" in body or "===" in body), (
        "fillMcpBaseUrl 应判断输入框是否已有值，仅在为空时填入"
    )


def test_detected_origin_is_initialized_from_origin():
    """页面初始化时（DOMContentLoaded）应把 origin 展示到检测提示元素。"""
    text = _fetch_config_html()
    body = _function_body(text, "function initMcpDetectedOrigin(")
    assert "window.location.origin" in body, (
        "initMcpDetectedOrigin 应从 window.location.origin 取值"
    )
    assert "mcp-detected-origin" in body, (
        'initMcpDetectedOrigin 应写入 id="mcp-detected-origin" 元素'
    )
    # 初始化应在配置页自身的 DOMContentLoaded 回调内被调用
    # （渲染后的页面还包含 base 布局的 DOMContentLoaded，需锚定回调内的初始化注释）。
    anchor = text.index("// 展示当前访问地址，供「MCP 服务公共 URL」一键填入")
    dom_start = text.rindex("document.addEventListener('DOMContentLoaded'", 0, anchor)
    # 从回调体开始处截取，避免把签名里的 "function () {" 当成函数边界。
    dom_body_start = text.index("{", dom_start) + 1
    dom_seg = text[dom_body_start : text.index("function ", dom_body_start)]
    assert "initMcpDetectedOrigin()" in dom_seg, (
        "应在 DOMContentLoaded 回调中调用 initMcpDetectedOrigin()"
    )
