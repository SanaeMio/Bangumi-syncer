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


def test_image_uses_native_lazy_loading():
    """正文图片带原生懒加载属性：弹窗内滚动到视口才请求。"""
    html = markdown_to_safe_html("![a](https://github.com/user-attachments/a.png)")
    assert 'loading="lazy"' in html
    assert 'decoding="async"' in html


def test_image_from_allowed_host_is_kept():
    """GitHub 附件主机的图片保留 <img> 并带原生懒加载属性。"""
    for url in (
        "https://github.com/user-attachments/assets/a.png",
        "https://private-user-images.githubusercontent.com/1/a.png",
    ):
        html = markdown_to_safe_html(f"![a]({url})")
        assert "<img" in html, url
        assert 'loading="lazy"' in html, url
        assert 'decoding="async"' in html, url


def test_image_from_other_host_becomes_link():
    """名单外主机与非 https 的图片改为链接，页面不会请求未放行的主机。"""
    for url in (
        "https://img.example.com/a.png",
        "http://github.com/a.png",
        "https://github.com.evil.example/a.png",
    ):
        html = markdown_to_safe_html(f"![截图]({url})")
        assert "<img" not in html, url
        assert f'<a href="{url}"' in html, url
        assert 'rel="noopener noreferrer"' in html, url
        assert ">截图</a>" in html, url


def test_image_host_check_follows_url_host():
    """主机判定以 URL 的 host 为准：userinfo 不影响结论，冒名域名不放行。"""
    assert "<img" in markdown_to_safe_html(
        "![x](https://evil.example@github.com/a.png)"
    )
    html = markdown_to_safe_html("![x](https://github.com@evil.example/a.png)")
    assert "<img" not in html
    assert '<a href="https://github.com@evil.example/a.png"' in html


def test_image_with_unparsable_host_is_dropped():
    """主机无法解析（非法 IPv6 字面量）时图片既不渲染也不转链接。"""
    html = markdown_to_safe_html("![x](https://[abc/a.png)")
    assert "<img" not in html
    assert "<a" not in html
