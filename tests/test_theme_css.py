"""暗色主题 CSS 回归：主题变量与组件暗色覆盖。"""

import re
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


def test_dark_overrides_secondary_bg_token():
    """暗色需覆盖 Bootstrap 的 --bs-secondary-bg。

    文件选择按钮的悬停背景由 Bootstrap 的
    .form-control:hover:not(:disabled):not([readonly])::file-selector-button
    规则提供（:hover 作用于输入框），该规则读取 --bs-secondary-bg；
    不覆盖时取浅色值 #e9ecef，暗色下呈白底叠加浅色文字。该 token 同时决定
    禁用表单、分页禁用项与进度条轨道在暗色下的背景。
    """
    css = _css("theme-base.css")
    dark_block = css.split('[data-theme="dark"] {', 1)[1].split("}", 1)[0]
    line = next(
        (
            raw.strip()
            for raw in dark_block.splitlines()
            if raw.strip().startswith("--bs-secondary-bg:")
        ),
        "",
    )
    assert "#e9ecef" not in line
    assert "var(--app-bg-card-header)" in line


def test_dark_file_selector_button_has_no_hover_rule():
    """项目不另写文件选择按钮的悬停背景规则。

    悬停由 Bootstrap 规则经 --bs-secondary-bg 提供；Bootstrap 基础规则对
    ::file-selector-button 设置 pointer-events: none，伪元素不进入 :hover
    状态，写在伪元素之后的 :hover 不命中，此类规则不生效。
    """
    assert "::file-selector-button:hover" not in _css("theme-base.css")


def test_dark_moon_icon_uses_theme_color():
    """暗色模式的月亮图标应使用主题色。"""
    css = _css("dashboard.css")
    block = css.split(
        ".btn-toolbar:has(.header-theme-switcher) .theme-toggle-capsule--dark"
        " .theme-toggle-capsule__icon--moon {",
        1,
    )[1].split("}", 1)[0]
    assert "color: var(--app-primary)" in block


def test_dark_secondary_bg_rgb_matches_color_mix():
    """--bs-secondary-bg-rgb 与 --bs-secondary-bg 的 color-mix 计算结果对应。

    .bg-body-secondary 经 rgba(var(--bs-secondary-bg-rgb), 1) 取色，映射页
    正则示例区使用该类；只覆盖颜色值不覆盖分量时，暗色下仍取浅色默认
    233,236,239 呈白底叠加浅色文字。CSS 自定义属性无法从 color-mix 表达式
    取 RGB 分量，-rgb 需按计算结果手工填写，改动 --app-bg-card-header 或
    混合比例时须同步。
    """
    css = _css("theme-base.css")
    dark_block = css.split('[data-theme="dark"] {', 1)[1].split("}", 1)[0]

    def _token(name: str) -> str:
        line = next(
            (
                raw.strip()
                for raw in dark_block.splitlines()
                if raw.strip().startswith(name + ":")
            ),
            "",
        )
        return line.split(":", 1)[1].strip().rstrip(";") if ":" in line else ""

    header = _token("--app-bg-card-header")
    m = re.fullmatch(r"#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})", header)
    assert m, f"--app-bg-card-header 需为 #rrggbb 字面量，当前: {header}"
    mix = _token("--bs-secondary-bg")
    pct = re.search(r"(\d+)%", mix)
    assert pct, f"--bs-secondary-bg 需为按比例混白的 color-mix，当前: {mix}"
    ratio = int(pct.group(1)) / 100
    expected = ", ".join(
        str(round(int(m.group(i + 1), 16) * ratio + 255 * (1 - ratio)))
        for i in range(3)
    )
    rgb = _token("--bs-secondary-bg-rgb")
    assert rgb == expected, (
        f"--bs-secondary-bg-rgb 应为 {expected}（color-mix 计算结果），当前: {rgb}"
    )


def test_dark_overrides_code_color():
    """暗色需覆盖 Bootstrap 的 --bs-code-color。

    Bootstrap 的 code 元素规则取 var(--bs-code-color)，其暗色值由
    [data-bs-theme=dark] 提供，而项目只设 data-theme；卡片外的 code
    （Docker 升级提示、Trakt 授权成功页、映射导入弹窗）不匹配 .card code
    的项目规则，不覆盖时取浅色粉 #d63384。
    """
    css = _css("theme-base.css")
    dark_block = css.split('[data-theme="dark"] {', 1)[1].split("}", 1)[0]
    line = next(
        (
            raw.strip()
            for raw in dark_block.splitlines()
            if raw.strip().startswith("--bs-code-color:")
        ),
        "",
    )
    assert line, "暗色块缺少 --bs-code-color 覆盖"
    assert "#d63384" not in line


def test_dark_btn_close_uses_white_filter():
    """暗色下关闭按钮图标需反色为浅色。

    Bootstrap 的 .btn-close 背景 SVG 填充固定为黑色，其暗色处理挂在
    [data-bs-theme=dark] 选择器上，项目只设 data-theme 故不生效，暗色下
    黑叉不可见；以 data-theme 选择器复用 Bootstrap 同一滤镜变量处理。
    """
    css = _css("theme-base.css")
    assert '[data-theme="dark"] .btn-close {' in css
    block = css.split('[data-theme="dark"] .btn-close {', 1)[1].split("}", 1)[0]
    assert "filter: var(--bs-btn-close-white-filter)" in block


def test_dark_badge_subtle_variants_themed():
    """通知规则的启用/禁用徽章使用 subtle 变体，需暗色覆盖。

    bg-success-subtle 与 bg-secondary-subtle 的 token 未随暗色覆盖，
    徽章在暗色下呈浅色底；覆盖值与既有 .badge.bg-success /
    .badge.bg-secondary 暗色规则保持一致。
    """
    css = _css("pages.css")
    success = css.split('[data-theme="dark"] .badge.bg-success-subtle {', 1)[1].split(
        "}", 1
    )[0]
    assert "rgba(var(--app-green-rgb), 0.18)" in success
    secondary = css.split('[data-theme="dark"] .badge.bg-secondary-subtle {', 1)[
        1
    ].split("}", 1)[0]
    assert "rgba(var(--app-gray-rgb), 0.2)" in secondary


def test_templates_avoid_light_only_utility_classes():
    """模板不使用仅浅色的 Bootstrap 工具类。

    bg-light 与 table-light 的背景/文字取浅色固定值，暗色下呈白底或黑字；
    应使用跟随主题的 bg-body-tertiary（浅色取值与 bg-light 一致）或裸
    thead（项目其余表格的惯例）。
    """
    for path in sorted((_REPO_ROOT / "templates").rglob("*.html")):
        content = path.read_text(encoding="utf-8")
        rel = path.relative_to(_REPO_ROOT)
        assert "bg-light" not in content, f"{rel} 含 bg-light"
        assert "table-light" not in content, f"{rel} 含 table-light"
