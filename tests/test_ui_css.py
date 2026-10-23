"""UI 布局 CSS 回归：主题切换控件与页面操作按钮的窄屏表现。"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _css(name: str) -> str:
    return (_REPO_ROOT / "static/css" / name).read_text(encoding="utf-8")


def _block(css: str, selector: str) -> str:
    """提取选择器对应的声明块内容。"""
    return css.split(f"{selector} {{", 1)[1].split("}", 1)[0]


class TestThemeToggleAlignment:
    """主题切换胶囊：圆点与图标的居中约束。"""

    def test_thumb_vertically_centered(self):
        """圆点须垂直居中：轨道有 1px 边框，用固定 top 会让圆点偏低 1px。"""
        block = _block(
            _css("dashboard.css"),
            ".btn-toolbar:has(.header-theme-switcher) .theme-toggle-capsule__thumb",
        )
        assert "top: 50%" in block
        assert "translateY(-50%)" in block

    def test_thumb_travel_excludes_track_border(self):
        """圆点行程须扣掉轨道左右各 1px 边框，否则暗色态圆点与月亮图标错位。"""
        css = _css("dashboard.css")
        travel = css.split("--theme-toggle-travel:", 1)[1].split(";", 1)[0]
        assert "2px" in travel
        block = _block(
            css,
            ".btn-toolbar:has(.header-theme-switcher) .theme-toggle-capsule--dark"
            " .theme-toggle-capsule__thumb",
        )
        assert "translate(var(--theme-toggle-travel), -50%)" in block

    def test_toggle_icons_centered_on_thumb(self):
        """日/月图标按自身中心对齐圆点中心，而非按图标边缘定位。"""
        css = _css("dashboard.css")
        sun = _block(
            css,
            ".btn-toolbar:has(.header-theme-switcher) .theme-toggle-capsule__icon--sun",
        )
        moon = _block(
            css,
            ".btn-toolbar:has(.header-theme-switcher) .theme-toggle-capsule__icon--moon",
        )
        assert "translate(-50%, -50%)" in sun
        assert "translate(50%, -50%)" in moon


class TestArchivePageActions:
    """Archive 离线查询层页操作按钮：窄屏保持单行。"""

    def test_actions_group_marked_in_template(self):
        """归档页操作按钮组需带专用类名，以便窄屏覆盖换行行为。"""
        template = (_REPO_ROOT / "templates/bangumi_archive.html").read_text(
            encoding="utf-8"
        )
        assert "btn-group archive-page-actions" in template

    def test_actions_group_never_wraps(self):
        """按钮组不允许换行，否则三个按钮会被拆成两行。"""
        block = _block(_css("pages.css"), ".btn-group.archive-page-actions")
        assert "flex-wrap: nowrap" in block

    def test_narrow_screen_buttons_shrink_instead_of_wrapping(self):
        """窄屏下按钮改为紧凑尺寸并禁止文字换行。"""
        block = _block(_css("pages.css"), ".btn-group.archive-page-actions .btn")
        assert "flex: 0 0 auto" in block
        assert "--bs-btn-padding-x: var(--archive-actions-padding-x)" in block
        assert "--bs-btn-font-size: var(--archive-actions-font-size)" in block
        assert "white-space: nowrap" in block

    def test_narrow_screen_group_defines_compact_metrics(self):
        """窄屏按钮的紧凑内边距与字号由按钮组上的变量给出。"""
        css = _css("pages.css")
        idx = css.index("--archive-actions-padding-x: 0.5rem")
        assert "--archive-actions-font-size: 0.8125rem" in css[idx : idx + 200]

    def test_extreme_narrow_screen_uses_same_selectors(self):
        """极窄屏须用与窄屏相同的选择器覆盖，否则被高特异性规则压过。

        窄屏规则里按钮尺寸取自按钮组上的变量，若极窄屏改用更弱的选择器，
        变量不会被重新赋值，收紧尺寸不会生效。
        """
        css = _css("pages.css")
        narrow = css.split("@media (max-width: 380px) {", 1)[1]
        block = narrow.split("}", 1)[0]
        assert "--archive-actions-padding-x: 0.375rem" in block
        assert "--archive-actions-font-size: 0.75rem" in block
        assert ".btn-group.archive-page-actions" in block

    def test_mobile_generic_btn_group_wrap_is_overridden(self):
        """全局移动端 .btn-group 的 wrap 规则须被归档页按钮组覆盖。"""
        css = _css("pages.css")
        # 全局规则位于首个移动端媒体查询中，归档页覆盖须出现在其之后
        assert len(css.split("@media (max-width: 767.98px) {", 1)) > 1
        override = css.rindex(".btn-group.archive-page-actions {")
        generic = css.index(".btn-group {")
        assert override > generic
