"""
配置页注释悬浮化改造测试

目标：验证 config 页面中原本以 ``<small class="text-muted">`` 平铺的 8 处注释，
全部改为 label/按钮旁的问号图标
（``<i class="bi bi-question-circle ms-1 text-muted" data-bs-toggle="tooltip" title="...">``），
弹窗内图标需带 ``data-bs-container="body"``。

参照：``templates/config/_bangumi_data.html`` 中"使用本地缓存"的写法。

改造已实现，本文件的测试当前全部通过（绿）。
"""

import re
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import pages

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _fetch_config_html():
    """以已认证用户访问 /config，返回 (full_text, llm_segment, modal_segment)。

    - llm_segment：``#config-section-llm`` 到 ``#config-section-summary`` 之间，即 LLM 配置卡片。
    - modal_segment：``#summaryJobModal`` 到 ``#deleteSummaryModal`` 之间，即追番总结任务弹窗。
    """
    app = FastAPI()
    app.include_router(pages.router)
    with patch.object(
        pages, "get_current_user_from_cookie", return_value={"username": "u"}
    ):
        client = TestClient(app)
        response = client.get("/config", follow_redirects=False)
    assert response.status_code == 200, f"期望 200，实际 {response.status_code}"
    text = response.text

    llm_start = text.index('id="config-section-llm"')
    llm_end = text.index('id="config-section-summary"')
    llm_segment = text[llm_start:llm_end]

    modal_start = text.index('id="summaryJobModal"')
    modal_end = text.index('id="deleteSummaryModal"')
    modal_segment = text[modal_start:modal_end]

    return text, llm_segment, modal_segment


# 问号图标 + tooltip + title（不要求 data-bs-container）
TOOLTIP_RE = re.compile(
    r"<i\b"
    r"(?=[^>]*\bbi-question-circle\b)"
    r'(?=[^>]*data-bs-toggle="tooltip")'
    r'[^>]*?title="([^"]*)"',
    re.DOTALL,
)


def _find_tooltip_title(segment, key):
    """在 segment 中查找 title 含 key 的问号 tooltip 图标，返回其 title 或 None。"""
    for m in TOOLTIP_RE.finditer(segment):
        if key in m.group(1):
            return m.group(1)
    return None


# 问号图标 + tooltip + data-bs-container="body" + title（弹窗专用）
MODAL_TOOLTIP_RE = re.compile(
    r"<i\b"
    r"(?=[^>]*\bbi-question-circle\b)"
    r'(?=[^>]*data-bs-toggle="tooltip")'
    r'(?=[^>]*data-bs-container="body")'
    r'[^>]*?title="([^"]*)"',
    re.DOTALL,
)


def _find_modal_tooltip_title(segment, key):
    """在弹窗 segment 中查找同时带 data-bs-container 的问号 tooltip 图标。"""
    for m in MODAL_TOOLTIP_RE.finditer(segment):
        if key in m.group(1):
            return m.group(1)
    return None


def _slice_between(text, start_marker, end_marker):
    """截取 text 中 start_marker 到 end_marker 之间的片段（不含 end_marker 本身）。"""
    s = text.index(start_marker)
    e = text.index(end_marker)
    return text[s:e]


# ---------------------------------------------------------------------------
# LLM 卡片：3 处图标
# ---------------------------------------------------------------------------


def test_llm_api_base_comment_becomes_tooltip_icon():
    """LLM 卡片 API 地址 label 旁应出现问号图标，title 含原文注释。"""
    _, llm, _ = _fetch_config_html()
    title = _find_tooltip_title(llm, "OpenAI 兼容接口请以 /v1 结尾填写完整地址")
    assert title is not None, (
        "LLM 卡片『API 地址』旁应出现问号图标，title 含『OpenAI 兼容接口请以 /v1 结尾填写完整地址』"
    )


def test_llm_thinking_level_comment_becomes_tooltip_icon():
    """LLM 卡片 思考强度 label 旁应出现问号图标，title 同时覆盖双 provider 映射。"""
    _, llm, _ = _fetch_config_html()
    title = _find_tooltip_title(llm, "reasoning_effort")
    assert title is not None, (
        "LLM 卡片『思考强度』旁应出现问号图标，title 含『reasoning_effort』"
    )
    assert "budget_tokens" in title, (
        "思考强度图标 title 需同时含『budget_tokens』，实际 title={title!r}"
    )


def test_llm_card_title_comment_becomes_tooltip_icon():
    """LLM 卡片标题『LLM 配置』旁应出现问号图标，title 含原底部安全提示。"""
    _, llm, _ = _fetch_config_html()
    title = _find_tooltip_title(llm, "API 密钥将加密存储")
    assert title is not None, (
        "LLM 卡片标题『LLM 配置』旁应出现问号图标，title 含原底部安全提示『API 密钥将加密存储』"
    )


# ---------------------------------------------------------------------------
# 追番总结弹窗：5 处图标（均须带 data-bs-container="body"）
# ---------------------------------------------------------------------------


def test_summary_max_records_comment_becomes_tooltip_icon():
    """最大记录数 label 旁应出现问号图标，title 含原文注释。"""
    _, _, modal = _fetch_config_html()
    title = _find_modal_tooltip_title(modal, "-1 表示不限制条数")
    assert title is not None, (
        "最大记录数 label 旁应出现带 data-bs-container 的问号图标，title 含『-1 表示不限制条数』"
    )


def test_summary_memory_limit_comment_becomes_tooltip_icon():
    """记忆条数 label 旁应出现问号图标，title 含原文注释。"""
    _, _, modal = _fetch_config_html()
    title = _find_modal_tooltip_title(modal, "0 = 关闭记忆")
    assert title is not None, (
        "记忆条数 label 旁应出现带 data-bs-container 的问号图标，title 含『0 = 关闭记忆』"
    )


def test_summary_related_limit_comment_becomes_tooltip_icon():
    """同剧关联条数 label 旁应出现问号图标，title 含原文注释。"""
    _, _, modal = _fetch_config_html()
    title = _find_modal_tooltip_title(modal, "0 = 关闭；>0 = 按日期倒序")
    assert title is not None, (
        "同剧关联条数 label 旁应出现带 data-bs-container 的问号图标，title 含『0 = 关闭；>0 = 按日期倒序』"
    )


def test_summary_clear_memory_comment_becomes_tooltip_icon():
    """清空记忆按钮旁应出现问号图标，title 含原文注释。"""
    _, _, modal = _fetch_config_html()
    title = _find_modal_tooltip_title(modal, "不可恢复，清空后从零开始")
    assert title is not None, (
        "清空记忆按钮旁应出现带 data-bs-container 的问号图标，title 含『不可恢复，清空后从零开始』"
    )


def test_summary_prompt_comment_becomes_tooltip_icon():
    """系统提示词 label 旁应出现问号图标，title 含原文注释。"""
    _, _, modal = _fetch_config_html()
    title = _find_modal_tooltip_title(modal, "告诉LLM如何总结")
    assert title is not None, (
        "系统提示词 label 旁应出现带 data-bs-container 的问号图标，title 含『告诉LLM如何总结』"
    )


# ---------------------------------------------------------------------------
# 原有 <small class="text-muted"> 平铺注释应被移除
# ---------------------------------------------------------------------------


def test_llm_card_has_no_small_text_muted():
    """LLM 卡片片段内不应再出现 <small class="text-muted"> 平铺注释。"""
    _, llm, _ = _fetch_config_html()
    assert '<small class="text-muted">' not in llm, (
        'LLM 卡片内不应再出现 <small class="text-muted"> 平铺注释'
    )


def test_summary_modal_has_no_small_text_muted():
    """追番总结弹窗片段内不应再出现 <small class="text-muted"> 平铺注释。"""
    _, _, modal = _fetch_config_html()
    assert '<small class="text-muted">' not in modal, (
        '追番总结弹窗内不应再出现 <small class="text-muted"> 平铺注释'
    )


# ---------------------------------------------------------------------------
# 弹窗内每个问号图标均须带 data-bs-container="body"
# ---------------------------------------------------------------------------


def test_summary_modal_all_tooltips_have_bs_container():
    """弹窗内 tooltip 数量 == data-bs-container 数量，且应为 5 个。"""
    _, _, modal = _fetch_config_html()
    tooltip_count = modal.count('data-bs-toggle="tooltip"')
    container_count = modal.count('data-bs-container="body"')
    assert tooltip_count == container_count, (
        '每个问号图标都应带 data-bs-container="body"，'
        f"tooltip={tooltip_count} container={container_count}"
    )
    assert tooltip_count == 5, (
        f"追番总结弹窗应有 5 个悬浮注释图标，实际 {tooltip_count}"
    )


# ---------------------------------------------------------------------------
# 边界：无注释字段不新增图标
# ---------------------------------------------------------------------------


def test_llm_api_key_field_has_no_tooltip():
    """API 密钥（llm-api-key）字段所在段不应新增悬浮注释图标。"""
    text, _, _ = _fetch_config_html()
    seg = _slice_between(text, 'id="llm-api-key"', 'id="llm-provider"')
    assert 'data-bs-toggle="tooltip"' not in seg, "API 密钥字段不应新增悬浮注释图标"


def test_summary_cron_field_has_no_tooltip():
    """Cron 表达式（summary-job-cron）label 段不应新增悬浮注释图标。"""
    text, _, _ = _fetch_config_html()
    seg = _slice_between(text, 'id="summary-job-cron"', 'id="summary-job-lookback"')
    assert 'data-bs-toggle="tooltip"' not in seg, "Cron 表达式字段不应新增悬浮注释图标"


def test_summary_lookback_field_has_no_tooltip():
    """回溯天数（summary-job-lookback）label 段不应新增悬浮注释图标。"""
    text, _, _ = _fetch_config_html()
    seg = _slice_between(text, 'id="summary-job-lookback"', 'id="summary-job-user"')
    assert 'data-bs-toggle="tooltip"' not in seg, "回溯天数字段不应新增悬浮注释图标"


def test_summary_user_field_has_no_tooltip():
    """用户名（summary-job-user）label 段不应新增悬浮注释图标。"""
    text, _, _ = _fetch_config_html()
    # 切片为"用户名 label 起始"到"用户名 input id"之间的段落（用户名字段无图标）。
    seg = _slice_between(
        text, '<label class="form-label">用户名', 'id="summary-job-user"'
    )
    assert 'data-bs-toggle="tooltip"' not in seg, "用户名字段不应新增悬浮注释图标"
