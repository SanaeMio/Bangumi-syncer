"""UI 布局 CSS 回归：主题切换控件与页面操作按钮的窄屏表现。"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _css(name: str) -> str:
    return (_REPO_ROOT / "static/css" / name).read_text(encoding="utf-8")


def _block(css: str, selector: str) -> str:
    """提取选择器对应的声明块内容。"""
    return css.split(f"{selector} {{", 1)[1].split("}", 1)[0]


def _last_media_block(css: str, query: str) -> str:
    """提取最后一个同名媒体查询的整块内容（按大括号配对）。"""
    head = f"@media {query} {{"
    start = css.rindex(head) + len(head)
    depth = 1
    idx = start
    while depth and idx < len(css):
        if css[idx] == "{":
            depth += 1
        elif css[idx] == "}":
            depth -= 1
        idx += 1
    return css[start : idx - 1]


class TestThemeToggleAlignment:
    """主题切换胶囊：圆点与图标的居中、配色约束。"""

    def test_active_icons_follow_theme_color(self):
        """亮起的图标使用主题色：亮色下是太阳，暗色下是月亮。"""
        css = _css("dashboard.css")
        sun = _block(css, ".theme-toggle-capsule__icon--sun")
        assert "color: var(--app-primary)" in sun
        moon = _block(
            css,
            ".btn-toolbar:has(.header-theme-switcher) .theme-toggle-capsule--dark "
            ".theme-toggle-capsule__icon--moon",
        )
        assert "color: var(--app-primary)" in moon

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


class TestActionCapsule:
    """操作按钮胶囊：窄屏内部分段不换行、整体收窄。"""

    TEMPLATES = {
        "config.html": 2,
        "dashboard.html": 1,
        "mappings.html": 4,
        "bangumi_archive.html": 1,
        "bangumi_replay.html": 1,
        "logs.html": 1,
    }

    @staticmethod
    def _template(name: str) -> str:
        return (_REPO_ROOT / "templates" / name).read_text(encoding="utf-8")

    def test_capsules_marked_in_templates(self):
        """各操作胶囊在模板中带统一类名（含 btn-group-sm 与修饰类）。"""
        for name, count in self.TEMPLATES.items():
            found = re.findall(
                r'class="btn-group[^"]*app-action-capsule', self._template(name)
            )
            assert len(found) == count, name

    def test_archive_specific_class_removed(self):
        """归档页使用共享胶囊类，无专用类名。"""
        for name in self.TEMPLATES:
            assert "archive-page-actions" not in self._template(name), name

    def test_row_action_capsules_marked(self):
        """映射列表的行内操作按钮同样使用胶囊类，保持单行。"""
        mappings = self._template("mappings.html")
        assert mappings.count("btn-group btn-group-sm app-action-capsule") == 2

    def test_fill_and_inline_modifiers_marked(self):
        """卡片内胶囊标记 --fill，两键标题胶囊标记 --inline。"""
        assert self._template("config.html").count("app-action-capsule--fill") == 2
        mappings = self._template("mappings.html")
        assert mappings.count("app-action-capsule--fill") == 1
        assert mappings.count("app-action-capsule--inline") == 1
        assert self._template("logs.html").count("app-action-capsule--inline") == 1

    def test_capsule_never_wraps(self):
        """胶囊内部分段不换行，避免按钮被拆行。"""
        block = _block(_css("pages.css"), ".btn-group.app-action-capsule")
        assert "flex-wrap: nowrap" in block

    def test_narrow_screen_capsule_shrinks_to_content(self):
        """窄屏下胶囊按内容收缩，覆盖标题区与配置表单的撑满/列向规则。"""
        media = _last_media_block(_css("pages.css"), "(max-width: 768px)")
        rule = media.split(".btn-group.app-action-capsule {", 1)[1].split("}", 1)[0]
        assert "width: auto" in rule
        assert "flex-direction: row" in rule
        assert "flex-wrap: nowrap" in rule
        assert "--app-capsule-padding-x: 0.625rem" in rule
        assert "--app-capsule-font-size: 0.875rem" in rule

    def test_narrow_screen_buttons_keep_natural_width(self):
        """窄屏按钮不参与拉伸、文字不换行，内边距与字号按变量收紧。"""
        block = _block(_css("pages.css"), ".btn-group.app-action-capsule .btn")
        assert "flex: 0 0 auto" in block
        assert "padding: 0.3125rem var(--app-capsule-padding-x)" in block
        assert "font-size: var(--app-capsule-font-size)" in block
        assert "white-space: nowrap" in block
        assert "margin-bottom: 0" in block

    def test_config_form_capsule_keeps_row_and_end_radius(self):
        """配置表单的窄屏规则把表单内按钮组设为列向并给每个按钮单独加圆角；
        胶囊保持整行与首尾圆角，维持莲藕外形。"""
        media = _last_media_block(_css("pages.css"), "(max-width: 768px)")
        assert ".config-form .btn-group.app-action-capsule .btn {" in media
        assert "border-radius: 0 !important" in media
        assert "border-top-left-radius: var(--app-radius-btn) !important" in media
        assert "border-bottom-right-radius: var(--app-radius-btn) !important" in media

    def test_extreme_narrow_resets_compact_metrics(self):
        """窄屏与最窄视口各有收紧档，均只作用于按内容收缩的胶囊。"""
        css = _css("pages.css")
        for query, padding, font in (
            ("(max-width: 380px)", "0.5rem", "0.8125rem"),
            ("(max-width: 340px)", "0.375rem", "0.75rem"),
        ):
            media = _last_media_block(css, query)
            assert f"--app-capsule-padding-x: {padding}" in media, query
            assert f"--app-capsule-font-size: {font}" in media, query
            assert ":not(.app-action-capsule--fill)" in media, query

    def test_generic_mobile_btn_group_wrap_is_overridden(self):
        """通用移动端 .btn-group 的 wrap 规则须被胶囊规则覆盖（胶囊规则在其后）。"""
        css = _css("pages.css")
        assert css.rindex(".btn-group.app-action-capsule {") > css.index(".btn-group {")

    def test_fill_capsules_track_container_width(self):
        """卡片内胶囊占满容器宽度并限宽，分段等比分配。"""
        media = _last_media_block(_css("pages.css"), "(max-width: 768px)")
        assert ".btn-group.app-action-capsule--fill {" in media
        fill = media.split(".btn-group.app-action-capsule--fill {", 1)[1].split("}", 1)[
            0
        ]
        assert "width: 100%" in fill
        assert "max-width: 24rem" in fill
        segment = media.split(".btn-group.app-action-capsule--fill .btn {", 1)[1].split(
            "}", 1
        )[0]
        assert "flex: 1 1 0" in segment

    def test_inline_capsules_sit_beside_title(self):
        """两键胶囊在窄屏与标题同排靠右，标题超出时省略。"""
        media = _last_media_block(_css("pages.css"), "(max-width: 768px)")
        assert (
            ".app-main>.d-flex:first-child:has(.app-action-capsule--inline) {" in media
        )
        header = media.split(
            ".app-main>.d-flex:first-child:has(.app-action-capsule--inline) {", 1
        )[1].split("}", 1)[0]
        assert "flex-direction: row" in header
        assert "justify-content: space-between" in header
        title = media.split(".app-action-capsule--inline) .app-page-title {", 1)[
            1
        ].split("}", 1)[0]
        assert "text-overflow: ellipsis" in title

    def test_inline_capsules_drop_below_on_tiny_screens(self):
        """最窄视口下两键胶囊位于标题下方，卡片内胶囊同步收紧。"""
        media = _last_media_block(_css("pages.css"), "(max-width: 300px)")
        assert "flex-direction: column" in media
        assert ".btn-group.app-action-capsule--fill {" in media
        assert "--app-capsule-padding-x: 0.375rem" in media

    def test_config_actions_card_spaces_wrapped_capsules(self):
        """配置操作卡片的两个胶囊由 flex 容器承载，换行时保留间距。"""
        assert (
            "card-body d-flex flex-wrap align-items-center justify-content-center gap-3 py-4"
            in self._template("config.html")
        )


class TestArchiveUploadRow:
    """Archive 上传行：文件选择与导入按钮保持单行。"""

    @staticmethod
    def _template(name: str) -> str:
        return (_REPO_ROOT / "templates" / name).read_text(encoding="utf-8")

    def test_archive_upload_row_marked(self):
        """Archive 上传行带专用类名，与按钮胶囊区分。"""
        assert 'class="input-group archive-upload-row mb-3"' in self._template(
            "bangumi_archive.html"
        )

    def test_archive_upload_row_stays_one_line(self):
        """上传行的文件选择与导入按钮保持单行，文件框可收缩。"""
        css = _css("pages.css")
        block = _block(css, ".input-group.archive-upload-row")
        assert "flex-wrap: nowrap" in block
        control = _block(css, ".input-group.archive-upload-row .form-control")
        assert "min-width: 0" in control
