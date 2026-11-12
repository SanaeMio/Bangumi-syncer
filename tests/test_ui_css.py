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

    # dashboard.html 唯一一处胶囊在「我在追的放送」卡片头部，该卡片已暂时隐藏
    TEMPLATES = {
        "config.html": 2,
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


class TestProgressBarAnimation:
    """进度条：条纹可见、可停、结束态用状态色。"""

    def test_fill_uses_solid_color_so_state_class_wins(self):
        """底色必须是 background-color：渐变简写会盖掉 bg-success 等状态色。"""
        block = _block(_css("components.css"), ".progress-bar")
        assert "background-color: var(--app-primary)" in block
        assert "background: var(--app-gradient-primary)" not in block

    def test_stripes_stack_on_top_of_fill(self):
        """条纹只覆盖 background-image，才能叠在底色之上。"""
        block = _block(_css("components.css"), ".progress-bar-striped")
        assert "background-image: linear-gradient(" in block
        assert "background-size: 0.9rem 0.9rem" in block

    def test_animation_uses_project_keyframes(self):
        """动画指向本仓库的 keyframes，慢一点更稳，避免高频重绘看着抖。"""
        css = _css("components.css")
        assert "animation: app-progress-stripes 1.1s linear infinite" in _block(
            css, ".progress-bar-animated"
        )
        keyframes = css.split("@keyframes app-progress-stripes {", 1)[1]
        assert "background-position: 0.9rem 0" in keyframes

    def test_reduced_motion_stops_animation(self):
        """系统开启减少动效时不再流动。"""
        block = _last_media_block(
            _css("components.css"), "(prefers-reduced-motion: reduce)"
        )
        assert ".progress-bar-animated" in block
        assert "animation: none" in block

    def test_archive_progress_bar_animation_is_stage_driven(self):
        """导入进度条由阶段决定是否显示条纹，完成后停在纯色。"""
        html = (_REPO_ROOT / "templates/bangumi_archive.html").read_text(
            encoding="utf-8"
        )
        assert 'class="progress-bar"' in html
        assert "progress-bar-animated progress-bar-striped" not in html
        assert "bar.classList.toggle('progress-bar-animated', busy)" in html
        assert "['idle', 'done', 'error', 'skipped'].includes(stage)" in html

    def test_upgrade_progress_bar_stops_at_completion(self):
        """一键升级进度条在完成后摘下动画，避免 100% 还在流动。"""
        js = (_REPO_ROOT / "static/js/upgrade.js").read_text(encoding="utf-8")
        assert "function setBarRunning(running)" in js
        assert js.count("setBarRunning(false)") == 2


class TestDashboardRealtime:
    """首页实时更新：SSE 接线、增量插入与变化动效。"""

    def test_dashboard_opens_stats_stream(self):
        """仪表板通过一条 SSE 连接接收统计/热力图/最近记录。"""
        js = (_REPO_ROOT / "static/js/dashboard.js").read_text(encoding="utf-8")
        assert "new EventSource('/api/stats/stream')" in js
        assert "source.addEventListener('dashboard'" in js
        # 首屏静态数据落地后才连流，避免两路同时渲染
        assert "dashboardLoaded = true" in js
        assert "connectDashboardStream()" in js

    def test_new_records_are_prepended_not_rerendered(self):
        """新记录插到时间线顶部，已有条目原地不动。"""
        js = (_REPO_ROOT / "static/js/dashboard.js").read_text(encoding="utf-8")
        assert "function prependTimelineRecords(records)" in js
        assert "track.insertBefore(scratch.firstElementChild, firstItem)" in js
        # 只保留最近 6 条，多出来的从尾部移除
        assert "for (let i = DASHBOARD_TIMELINE_LIMIT; i < items.length" in js

    def test_stat_values_animate_on_change(self):
        """数字变化才触发动画，值没变不动。"""
        js = (_REPO_ROOT / "static/js/dashboard.js").read_text(encoding="utf-8")
        assert "function setStatValue(id, value)" in js
        assert "el.classList.add('stats-value--updated')" in js

        css = _css("dashboard.css")
        assert "animation: stats-value-bump" in _block(css, ".stats-value--updated")
        assert "@keyframes stats-value-bump {" in css
        assert "animation: tl-item-highlight" in _block(css, ".tl-item--new::after")

    def test_live_dot_marks_open_stream(self):
        """卡片头的绿点只在连接建立后出现。"""
        html = (_REPO_ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
        assert 'id="dashboardLiveDot"' in html
        assert "dashboard-live-dot d-none" in html

        js = (_REPO_ROOT / "static/js/dashboard.js").read_text(encoding="utf-8")
        assert "dot.classList.remove('d-none')" in js
        assert "dot.classList.add('d-none')" in js

    def test_realtime_animations_respect_reduced_motion(self):
        """系统要求减少动效时不再跳动与高亮。"""
        block = _last_media_block(
            _css("dashboard.css"), "(prefers-reduced-motion: reduce)"
        )
        for selector in (
            ".stats-value--updated",
            ".tl-item--new::after",
            ".dashboard-live-dot",
        ):
            assert selector in block


class TestRecordDetailCopy:
    """同步记录详情：步骤状态文案、说明去重与封面。"""

    def test_step_badges_follow_pipeline_stage(self):
        """同一个 hit 在不同阶段说不同的话，不再一律叫「命中」。"""
        js = (_REPO_ROOT / "static/js/records-detail.js").read_text(encoding="utf-8")
        assert "STEP_STAGE_STATUS_LABELS" in js
        assert "getStepStatusMeta(status, step.stage)" in js
        # 徽标上不再出现「命中 / 未命中」这种内部术语
        assert "hit: '命中'" not in js
        assert "miss: '未命中'" not in js

    def test_step_reason_is_normalized_and_deduped(self):
        """说明文字展示前翻译，与总览卡重复的不再重复显示。"""
        js = (_REPO_ROOT / "static/js/records-detail.js").read_text(encoding="utf-8")
        assert "RECORD_TEXT_REWRITES" in js
        assert "normalizeRecordText(step.reason)" in js
        assert "reasonText !== options.heroMessage" in js

    def test_links_inside_steps_are_deduped(self):
        """同一条目/单集在多个步骤里只挂一次链接。"""
        js = (_REPO_ROOT / "static/js/records-detail.js").read_text(encoding="utf-8")
        assert "seen.subjects.has" in js
        assert "seen.episodes.has" in js

    def test_poster_shown_in_modal_header(self):
        """详情弹窗头部左侧显示条目封面。"""
        html = (_REPO_ROOT / "templates/base.html").read_text(encoding="utf-8")
        assert 'id="record-detail-modal-poster"' in html

        js = (_REPO_ROOT / "static/js/records-detail.js").read_text(encoding="utf-8")
        assert "function setRecordPoster(subjectId)" in js
        assert "/poster" in js

        css = _css("records-detail.css")
        assert "aspect-ratio: 3 / 4" in _block(css, ".record-detail-modal__poster")
