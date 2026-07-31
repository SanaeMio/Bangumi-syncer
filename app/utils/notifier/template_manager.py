"""通知模板管理器（NotificationTemplateManager）

提供「模板查找 → 变量替换 → 渲染」能力，解耦事件数据与具体渠道的消息格式。

查找优先级：
1. ``<custom_dir>/<channel>/<template_name>``（自定义目录中的模板，优先级最高）
2. ``<default_dir>/<channel>/<template_name>``（仓库内置默认模板）
3. 渠道自带的 fallback（最后兜底，保证永不返回 ``None``）

目录结构示例：
    templates/notifications/          # 默认模板目录（随仓库分发）
        webhook/
            mark_success.json
            mark_failed.json
            ...
        email/
            mark_success_subject.txt
            mark_success.txt
            mark_success.html
            ...
    custom_templates/                  # 用户自定义目录（可通过 config 指定）
        webhook/
            mark_failed.json           # 仅覆盖该类型
        email/
            mark_failed.html
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.logging import logger

# 占位符正则：匹配 {variable}，变量名仅允许字母/数字/下划线
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass(frozen=True)
class TemplateAsset:
    """单个模板资产描述"""

    channel: str  # 如 "webhook" / "email" / "in_app"
    name: str  # 如 "mark_success"
    part: str  # 如 "payload" / "subject" / "body" / "html"


class NotificationTemplateManager:
    """通知模板管理器

    实例属性：
        default_dir: 内置默认模板目录
        custom_dir:  用户自定义模板目录（可空）
    """

    def __init__(
        self,
        default_dir: str | os.PathLike[str] | None = None,
        custom_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        if default_dir is None:
            default_dir = (
                Path(__file__).resolve().parents[3] / "templates" / "notifications"
            )
        self.default_dir = Path(default_dir)
        self.custom_dir = Path(custom_dir) if custom_dir else None

    # ── 模板查找 ──────────────────────────────────────────────────────────

    def _candidate_paths(
        self, channel: str, template_name: str, ext: str
    ) -> list[Path]:
        """返回候选路径列表（按优先级从高到低）"""
        fname = f"{template_name}.{ext}"
        cands: list[Path] = []
        if self.custom_dir:
            cands.append(self.custom_dir / channel / fname)
        cands.append(self.default_dir / channel / fname)
        return cands

    def find_asset(
        self,
        channel: str,
        template_name: str,
        ext: str,
        fallback: str | None = None,
    ) -> str | None:
        """按优先级查找并返回模板原始内容字符串；找不到返回 ``fallback``。

        注意：本方法只返回文本，JSON 解析由调用方负责（如 render_webhook_payload）。
        """
        for path in self._candidate_paths(channel, template_name, ext):
            if path.is_file():
                try:
                    return path.read_text(encoding="utf-8")
                except OSError as e:  # pragma: no cover
                    logger.warning(f"读取模板失败 {path}: {e}")
        return fallback

    # ── 变量替换 ──────────────────────────────────────────────────────────

    @staticmethod
    def render_string(template: str, data: dict[str, Any]) -> str:
        """将 ``{var}`` 占位符替换为 ``data`` 中的值；缺失变量替换为空串。"""

        def _sub(match: re.Match[str]) -> str:
            key = match.group(1)
            val = data.get(key, "")
            if val is None:
                return ""
            return str(val)

        return _PLACEHOLDER_RE.sub(_sub, template)

    @classmethod
    def render_value(cls, value: Any, data: dict[str, Any]) -> Any:
        """递归渲染：字符串替换变量；dict/list 递归处理；其他原样返回。"""
        if isinstance(value, dict):
            return {k: cls.render_value(v, data) for k, v in value.items()}
        if isinstance(value, list):
            return [cls.render_value(item, data) for item in value]
        if isinstance(value, str):
            return cls.render_string(value, data)
        return value

    # ── 各渠道便捷渲染 ────────────────────────────────────────────────────

    def render_webhook_payload(
        self, data: dict[str, Any], fallback: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """渲染 webhook payload，统一使用 ``webhook/default.json`` 模板。

        所有通知类型共用同一个默认 JSON；用户可在自定义目录放置
        ``webhook/default.json`` 覆盖整体格式，无需按类型拆分。
        """
        raw = self.find_asset("webhook", "default", "json")
        if raw is None:
            return fallback or {}
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("webhook 模板 default.json 非合法 JSON，按纯文本处理")
            return {"raw": raw}
        rendered = self.render_value(obj, data)
        if not isinstance(rendered, dict):
            return {"data": rendered}
        return rendered

    def render_email(
        self, data: dict[str, Any], template_name: str = "default"
    ) -> dict[str, str | None]:
        """渲染邮件，使用 ``email/<template_name>.html`` 模板。

        所有通知类型共用同一套默认邮件模板。subject 从 HTML 的 ``<title>``
        标签提取，body 为纯文本 fallback，html 为渲染后的完整 HTML。

        Returns:
            ``{"subject": str, "body": str, "html": str | None}``
        """
        raw_html = self.find_asset("email", template_name, "html")

        type_display_name = data.get("type_display_name", "")
        subject_fallback = f"[Bangumi-Syncer] {type_display_name}"

        if raw_html:
            rendered_html = self.render_string(raw_html, data)
            # 从 <title> 标签提取 subject
            title_match = re.search(
                r"<title[^>]*>(.*?)</title>", rendered_html, re.IGNORECASE | re.DOTALL
            )
            subject = title_match.group(1).strip() if title_match else subject_fallback
            # body 为去掉 HTML 标签的纯文本（先移除 style/script 块，避免残留 CSS/JS 文本）
            text_html = re.sub(
                r"<(style|script)[^>]*>.*?</\1>",
                "",
                rendered_html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            body = re.sub(r"<[^>]+>", "", text_html).strip()
            return {"subject": subject, "body": body, "html": rendered_html}

        return {"subject": subject_fallback, "body": None, "html": None}

    def get_template_content(
        self, channel: str, template_name: str, ext: str
    ) -> str | None:
        """获取指定渠道和模板名的原始内容"""
        return self.find_asset(channel, template_name, ext)

    def list_templates(self, channel: str, ext: str = "html") -> list[dict[str, str]]:
        """列出指定渠道的可用模板，区分默认和自定义来源。

        返回格式: ``[{"name": "default", "source": "default"}, {"name": "custom1", "source": "custom"}]``
        """
        templates: list[dict[str, str]] = []
        seen_names: set[str] = set()

        # 默认目录
        default_channel_dir = self.default_dir / channel
        if default_channel_dir.is_dir():
            for f in sorted(default_channel_dir.glob(f"*.{ext}")):
                name = f.stem
                if name not in seen_names:
                    templates.append({"name": name, "source": "default"})
                    seen_names.add(name)

        # 自定义目录
        if self.custom_dir:
            custom_channel_dir = self.custom_dir / channel
            if custom_channel_dir.is_dir():
                for f in sorted(custom_channel_dir.glob(f"*.{ext}")):
                    name = f.stem
                    if name not in seen_names:
                        templates.append({"name": name, "source": "custom"})
                        seen_names.add(name)
                    else:
                        # 覆盖默认模板，标记为 custom
                        for t in templates:
                            if t["name"] == name:
                                t["source"] = "custom"
                                break

        return templates

    def render_in_app(
        self, template_name: str, data: dict[str, Any]
    ) -> dict[str, str | None]:
        """渲染站内信标题和正文。"""
        title = self.find_asset("in_app", template_name, "title.txt")
        body = self.find_asset("in_app", template_name, "body.txt")
        return {
            "title": self.render_string(title, data) if title else None,
            "body": self.render_string(body, data) if body else None,
        }


# 模块级单例
template_manager = NotificationTemplateManager()
