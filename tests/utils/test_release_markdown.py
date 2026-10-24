"""Release Markdown → 安全 HTML。"""

from app.utils.release_markdown import markdown_to_safe_html


def test_markdown_to_safe_html_basic():
    html = markdown_to_safe_html("# T\n\n[x](javascript:alert(1))")
    assert "<h1>T</h1>" in html
    assert "javascript:" not in html


def test_markdown_to_safe_html_empty():
    assert markdown_to_safe_html("") == ""
    assert markdown_to_safe_html("   ") == ""
    assert markdown_to_safe_html(None) == ""


def test_strip_leading_changes_heading():
    html = markdown_to_safe_html("## Changes\n\nHello")
    assert "Hello" in html
    assert "<h2>Changes</h2>" not in html


def test_task_list_checkbox():
    html = markdown_to_safe_html("- [x] 完成\n- [ ] 待办")
    assert 'type="checkbox"' in html
    assert "checked" in html
    assert "待办" in html


def test_image_renders_with_remote_source():
    """正文图片保留 <img> 渲染（CSP 放行 https 图片源）。"""
    html = markdown_to_safe_html("![界面](https://github.com/x/abc.png)")
    assert "<img" in html
    assert 'src="https://github.com/x/abc.png"' in html
    assert 'alt="界面"' in html


def test_image_script_source_is_stripped():
    """图片地址不受脚本协议注入。"""
    html = markdown_to_safe_html("![x](javascript:alert(1))")
    assert "javascript:" not in html
