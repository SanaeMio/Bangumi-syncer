"""暗色主题 CSS 回归：主题变量与组件暗色覆盖。"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _css(name: str) -> str:
    return (_REPO_ROOT / "static/css" / name).read_text(encoding="utf-8")


def test_dark_theme_defines_tertiary_bg():
    """bg-body-tertiary 依赖 tertiary 变量，暗色下需覆盖避免白底。

    Bootstrap 的 .bg-body-tertiary 取的是 --bs-tertiary-bg-rgb（RGB 分量），
    其它消费方取 --bs-tertiary-bg（颜色值），两者都需要提供。
    """
    css = _css("theme-base.css")
    assert "--bs-tertiary-bg: #2c2e38" in css
    assert "--bs-tertiary-bg-rgb: 44, 46, 56" in css


def test_rule_channel_item_hover_uses_theme_token():
    """通知规则选项悬停背景应随主题，不能用近白的 --bs-gray-100。"""
    css = _css("pages.css")
    block = css.split(".rule-channel-item:hover {", 1)[1].split("}", 1)[0]
    assert "var(--app-bg-hover)" in block
    assert "--bs-gray-100" not in block


def test_rule_channel_item_checked_uses_theme_token():
    """通知规则选项选中背景应使用主题色，而非硬编码蓝色。"""
    css = _css("pages.css")
    block = css.split(".rule-channel-item:has(input:checked) {", 1)[1].split("}", 1)[0]
    assert "var(--app-primary-soft)" in block


def test_dark_alert_light_override():
    """alert-light 需提供暗色覆盖（网络诊断结果使用该类）。"""
    assert '[data-theme="dark"] .alert-light' in _css("pages.css")


def test_dark_file_selector_button_styled():
    """原生文件选择的按钮与文字在暗色下随主题。"""
    assert 'input[type="file"].form-control::file-selector-button' in _css(
        "theme-base.css"
    )


def test_dark_file_selector_button_hover_is_opaque():
    """文件选择按钮的悬停态须为不透明色。

    半透明背景会透出下层（暗色下呈白底叠加浅色文字），故悬停用不透明色。
    """
    css = _css("theme-base.css")
    block = css.split("::file-selector-button:hover {", 1)[1].split("}", 1)[0]
    assert "color-mix" in block
    assert "--app-bg-hover" not in block


def test_dark_moon_icon_uses_theme_color():
    """暗色模式的月亮图标应使用主题色。"""
    css = _css("dashboard.css")
    block = css.split(
        ".btn-toolbar:has(.header-theme-switcher) .theme-toggle-capsule--dark"
        " .theme-toggle-capsule__icon--moon {",
        1,
    )[1].split("}", 1)[0]
    assert "color: var(--app-primary)" in block
