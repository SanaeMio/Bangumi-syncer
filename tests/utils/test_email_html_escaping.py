"""邮件 HTML 转义回归测试（防注入）

背景：通知数据在到达模板前**完全未转义**（`_build_data` 只做 getattr，
各 extractor 只做 .strip()）。而 `title` / `ori_title` 直接来自媒体服务器
的文件名/标题 —— 共享库或下载资源的命名是他人可控的。未转义时，一段
`<script>` 或 `<a href>` 会被原样写进邮件 HTML，可被用来伪造链接、
埋追踪像素、或（在宽松客户端上）执行脚本。

修复位置：只转义「输出目标是 HTML」的那条路径（``render_html``），
不影响 webhook（输出 JSON）与站内信/纯文本（应保留原文）。
"""

import pytest

from app.utils.notifier.template_manager import NotificationTemplateManager

PAYLOAD = "<script>alert(1)</script><img src=x onerror=alert(2)>"


def _data(**overrides) -> dict:
    data = {
        "notification_type": "mark_failed",
        "type_display_name": "同步失败",
        "type_icon": "❌",
        "payload_title": "❌ 同步失败",
        "timestamp": "2026-08-15 10:00:00",
        "user_name": "张三",
        "title": "葬送的芙莉莲",
        "season": 1,
        "episode": 12,
        "source": "plex",
        "summary_text": "正常正文",
        "error_message": "",
    }
    data.update(overrides)
    return data


class TestRenderHtmlEscaping:
    def test_script_escaped(self):
        out = NotificationTemplateManager.render_html(
            "<p>{title}</p>", {"title": PAYLOAD}
        )
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_img_onerror_escaped(self):
        out = NotificationTemplateManager.render_html(
            "<p>{title}</p>", {"title": PAYLOAD}
        )
        assert "<img" not in out
        assert "&lt;img" in out

    def test_ampersand_and_quotes_escaped(self):
        out = NotificationTemplateManager.render_html(
            "<p>{title}</p>", {"title": "a & b \"c\" 'd'"}
        )
        assert "&amp;" in out
        assert "&quot;" in out
        assert "&#x27;" in out

    def test_template_markup_itself_kept(self):
        """模板自身写死的 HTML 结构必须保留，只转义插入的值"""
        out = NotificationTemplateManager.render_html(
            '<div class="x"><p>{title}</p></div>', {"title": "安全"}
        )
        assert out == '<div class="x"><p>安全</p></div>'

    def test_chinese_not_escaped(self):
        out = NotificationTemplateManager.render_html(
            "<p>{title}</p>", {"title": "葬送的芙莉莲"}
        )
        assert "葬送的芙莉莲" in out

    def test_missing_key_ok(self):
        assert NotificationTemplateManager.render_html("<p>{nope}</p>", {}) == "<p></p>"

    def test_none_value_ok(self):
        assert (
            NotificationTemplateManager.render_html("<p>{v}</p>", {"v": None})
            == "<p></p>"
        )


class TestRenderStringNotEscaped:
    """通用 render_string 必须保持不转义（webhook JSON / 站内信纯文本依赖）"""

    def test_render_string_keeps_raw(self):
        out = NotificationTemplateManager.render_string("{title}", {"title": PAYLOAD})
        assert out == PAYLOAD


class TestEmailEscapingEndToEnd:
    def test_media_filename_cannot_inject(self):
        """核心回归：媒体库文件名不能往邮件 HTML 里注入标签"""
        rendered = NotificationTemplateManager().render_email(
            _data(title=PAYLOAD, ori_title=PAYLOAD)
        )
        html = rendered["html"]
        assert "<script>" not in html
        assert "onerror=alert" not in html or "&lt;img" in html
        assert "&lt;script&gt;" in html

    def test_user_name_cannot_inject(self):
        rendered = NotificationTemplateManager().render_email(_data(user_name=PAYLOAD))
        assert "<script>" not in rendered["html"]

    def test_summary_text_cannot_inject(self):
        """AI 正文也可能被文件名影响（prompt 注入），同样要转义"""
        rendered = NotificationTemplateManager().render_email(
            _data(summary_text=PAYLOAD)
        )
        assert "<script>" not in rendered["html"]
        assert "&lt;script&gt;" in rendered["html"]

    def test_subject_unescaped_for_header(self):
        """主题是邮件头（非 HTML），应还原成可读文本而非显示实体"""
        rendered = NotificationTemplateManager().render_email(
            _data(payload_title="追番 & 总结")
        )
        assert rendered["subject"] == "追番 & 总结"
        assert "&amp;" not in rendered["subject"]

    def test_plain_text_body_keeps_raw(self):
        """纯文本部件不应显示 HTML 实体"""
        rendered = NotificationTemplateManager().render_email(
            _data(summary_text=PAYLOAD)
        )
        body = rendered["body"]
        assert "<script>alert(1)</script>" in body
        assert "&lt;script&gt;" not in body

    def test_normal_content_unaffected(self):
        rendered = NotificationTemplateManager().render_email(_data())
        assert "葬送的芙莉莲" in rendered["html"]
        assert "张三" in rendered["html"]
        assert "葬送的芙莉莲" in rendered["body"]


class TestSubjectEdgeCases:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("❌ 同步失败", "❌ 同步失败"),
            ("A & B", "A & B"),
            ("<b>粗体</b>", "<b>粗体</b>"),
            ('引号"内', '引号"内'),
            ("单引号'内", "单引号'内"),
        ],
    )
    def test_subject_roundtrip(self, title, expected):
        rendered = NotificationTemplateManager().render_email(
            _data(payload_title=title)
        )
        assert rendered["subject"] == expected
