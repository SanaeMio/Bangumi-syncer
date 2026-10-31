"""配置页「匹配增强（LLM）」开关条件渲染 —— 渲染层测试。

覆盖"服务端渲染产出开关与提示 DOM"的部分；客户端条件显隐逻辑
（llm_available 决策）由 tests/e2e/test_config_match_assist.py 的 Playwright
用例覆盖（需要浏览器，CI 执行），此处不重复。

验证目标：
- 同步配置 section 增加「匹配增强（LLM）」开关（checkbox，name=sync.llm_match_assist）
- 提供「需先配置 LLM」提示元素的 DOM 骨架（显隐由 JS 控制）
"""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import pages


def _get_config_html() -> str:
    app = FastAPI()
    app.include_router(pages.router)
    # 配置页需登录，mock 当前用户以渲染模板
    with patch.object(
        pages, "get_current_user_from_cookie", return_value={"username": "u"}
    ):
        client = TestClient(app)
        resp = client.get("/config", follow_redirects=False)
        assert resp.status_code == 200
        return resp.text


def test_config_page_renders_match_assist_switch():
    """配置页同步设置 section 渲染「匹配增强（LLM）」开关与提示 DOM。"""
    html = _get_config_html()
    # 开关 checkbox 存在，键名对齐 sync.llm_match_assist（现有保存机制）
    assert 'id="llm-match-assist"' in html
    assert 'name="sync.llm_match_assist"' in html
    # 「需先配置 LLM」提示 DOM 存在（显隐由条件渲染 JS 控制）
    assert 'id="llm-match-assist-tip"' in html
    # 标题文案存在，便于用户理解
    assert "匹配增强（LLM）" in html
