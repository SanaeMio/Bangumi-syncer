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
