"""render_email 纯文本 body 的排版回归测试

历史问题：body 由「HTML 去标签」生成，而 HTML 模板缩进很深，去标签后
在 text/plain 部件里留下大量空白行，纯文本阅读体验很差。

现方案（B）：为纯文本单独提供 ``email/<name>.txt`` 模板。缺失时才退化为
「去标签 + 压缩空白」。
"""

from app.utils.notifier.template_manager import NotificationTemplateManager


def _data(**overrides) -> dict:
    data = {
        "notification_type": "watching_summary_每日总结",
        "type_display_name": "追番总结",
        "type_icon": "📊",
        "payload_title": "📊 追番总结 - 每日总结",
        "timestamp": "2026-08-15 10:00:00",
        "user_name": "张三",
        "title": "葬送的芙莉莲",
        "season": 1,
        "episode": 12,
        "source": "plex",
        "summary_text": "本周看了 3 部番：\n1. 甲\n2. 乙\n\n整体不错。",
        "error_message": "",
    }
    data.update(overrides)
    return data


class TestPlainTextTemplate:
    def test_body_uses_txt_template(self):
        """默认走 .txt 模板，body 不含 HTML 缩进残留"""
        rendered = NotificationTemplateManager().render_email(_data())
        body = rendered["body"]
        assert body is not None
        # 关键行都在
        assert "📊 追番总结 - 每日总结" in body
        assert "时间：2026-08-15 10:00:00" in body
        assert "番剧：葬送的芙莉莲 S01E12" in body
        assert "用户：张三" in body
        assert "来源：plex" in body

    def test_summary_text_multiline_preserved(self):
        rendered = NotificationTemplateManager().render_email(_data())
        body = rendered["body"]
        assert "本周看了 3 部番：" in body
        assert "1. 甲" in body
        assert "整体不错。" in body

    def test_no_indent_garbage(self):
        """不应出现整行只有空格的情况（旧实现的主要病症）"""
        rendered = NotificationTemplateManager().render_email(_data())
        body = rendered["body"]
        for line in body.splitlines():
            assert line == line.strip(), f"该行有首尾空白: {line!r}"

    def test_no_consecutive_blank_lines(self):
        """不应出现连续空行堆叠"""
        rendered = NotificationTemplateManager().render_email(_data())
        assert "\n\n\n" not in rendered["body"]

    def test_subject_still_from_html_title(self):
        rendered = NotificationTemplateManager().render_email(_data())
        assert rendered["subject"] == "📊 追番总结 - 每日总结"

    def test_html_still_rendered(self):
        rendered = NotificationTemplateManager().render_email(_data())
        assert rendered["html"] is not None
        assert "葬送的芙莉莲" in rendered["html"]

    def test_chinese_not_mangled(self):
        rendered = NotificationTemplateManager().render_email(_data())
        assert "张三" in rendered["body"]
        assert "张三" in rendered["html"]


class TestHtmlToTextFallback:
    """用户只提供 .html 自定义模板时的兜底（无 .txt）"""

    def test_fallback_strips_tags_and_blank_lines(self, tmp_path):
        custom = tmp_path / "email"
        custom.mkdir()
        (custom / "only_html.html").write_text(
            "<html><head><title>{payload_title}</title></head><body>\n"
            "    <div>\n"
            "        <p>番剧：{title}</p>\n"
            "\n"
            "        <p>{summary_text}</p>\n"
            "    </div>\n"
            "</body></html>",
            encoding="utf-8",
        )
        mgr = NotificationTemplateManager(default_dir=None, custom_dir=tmp_path)
        rendered = mgr.render_email(_data(), template_name="only_html")
        body = rendered["body"]
        assert "番剧：葬送的芙莉莲" in body
        assert "本周看了 3 部番：" in body
        # 不残留缩进行
        for line in body.splitlines():
            assert line == line.strip()
        assert "\n\n\n" not in body

    def test_fallback_drops_style_block(self, tmp_path):
        custom = tmp_path / "email"
        custom.mkdir()
        (custom / "styled.html").write_text(
            "<html><head><style>body{color:red}</style>"
            "<title>{payload_title}</title></head>"
            "<body><p>{title}</p></body></html>",
            encoding="utf-8",
        )
        mgr = NotificationTemplateManager(default_dir=None, custom_dir=tmp_path)
        body = mgr.render_email(_data(), template_name="styled")["body"]
        assert "color:red" not in body
        assert "葬送的芙莉莲" in body
