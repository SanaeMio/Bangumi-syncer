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
from html import escape, unescape
from pathlib import Path
from typing import Any

from ...core.logging import logger

# 占位符正则：匹配 {variable}，变量名仅允许字母/数字/下划线
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# webhook 默认模板中代表"该类型的特有字段集合"的哨兵值。
# 渲染时若某字段恰好等于此字符串，说明模板在此处声明了类型特有字段的位置，
# 该字段会被替换成 registry 中声明的 payload_fields。
_TYPE_FIELDS_PLACEHOLDER = "__type_fields__"


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

    @staticmethod
    def render_html(template: str, data: dict[str, Any]) -> str:
        """渲染 HTML 模板：``{var}`` 替换前先做 HTML 转义。

        与 :meth:`render_string` 的区别是**值会被转义**。模板里写死的 HTML
        结构保持原样，只有插入的变量被转义 —— 因此 ``<p>{title}</p>`` 里的
        标题无法注入标签。

        为什么单独一个方法而不是直接改 ``render_string``：同一套占位符替换
        还被 webhook（产出 JSON）和站内信（纯文本）复用，在那里做 HTML 转义
        是错的。转义只属于「输出目标是 HTML」的这一条路径。
        """

        def _sub(match: re.Match[str]) -> str:
            key = match.group(1)
            val = data.get(key, "")
            if val is None:
                return ""
            return escape(str(val))

        return _PLACEHOLDER_RE.sub(_sub, template)

    # ── 各渠道便捷渲染 ────────────────────────────────────────────────────

    def render_webhook_payload(
        self, data: dict[str, Any], fallback: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """渲染 webhook payload，统一使用 ``webhook/default.json`` 模板。

        所有通知类型共用同一个默认 JSON；用户可在自定义目录放置
        ``webhook/default.json`` 覆盖整体格式，无需按类型拆分。

        模板中形如 ``{type_fields}`` 的占位符会被替换为该通知类型在
        :mod:`app.core.notification_registry` 中声明的类型特有字段
        （如追番总结的 ``summary``），从而让通用模板也能表达类型专属内容，
        不必为每个类型单独准备模板。
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
        # 展开类型特有字段（占位符不会出现在 data 里的键，故需单独处理）
        return self.merge_type_fields(rendered, data)

    @staticmethod
    def type_fields(data: dict[str, Any]) -> dict[str, Any]:
        """该通知类型的特有字段（占位符名 → 值，缺失的丢弃）"""
        from ...core.notification_registry import get_type_meta

        notification_type = str(data.get("notification_type") or "")
        meta = get_type_meta(notification_type)
        if meta is None:
            return {}
        out: dict[str, Any] = {}
        for field_name, data_key in meta.default_payload_fields().items():
            value = data.get(data_key)
            if value not in (None, ""):
                out[field_name] = value
        return out

    def merge_type_fields(
        self, payload: Any, data: dict[str, Any], inject_default: bool = True
    ) -> Any:
        """按模板作者的意愿并入类型特有字段。

        契约（用户自定义模板是**完全替换**，不是"默认模板 + 打补丁"）：

        - 模板里写了哨兵 ``__type_fields__`` → 把该类型的专属字段展开到顶层；
        - 模板里**没写**哨兵 → 视为作者有意自行排版，**不注入**任何字段，
          只把已写出的内容原样发出（严格 schema 的接收端不会被塞入未知键）；
        无论哪种情况，哨兵本身（含嵌套位置）都会被消费掉，绝不外发字面量。

        ``inject_default=True``（默认）保留"无条件补齐"的语义，用于内置默认
        模板这条路径：它按设计就依赖类型特有字段（如追番总结的 ``summary``），
        故无需哨兵也补齐。用户自定义模板的调用方应传 ``inject_default=False``。

        非 dict 的 payload 原样返回。
        """
        if not isinstance(payload, dict):
            return payload
        # 先判断模板是否显式声明了哨兵，再消费掉它：31 个类型里有 7 个没有
        # 专属字段，若把删除放在"无字段就返回"之后，这些类型会把字面量
        # "__type_fields__" 原样发出去。递归处理是因为用户可能把哨兵写在
        # 嵌套对象/数组里，只查顶层会漏。
        declared = self._consume_sentinel(payload)
        if not declared and not inject_default:
            return payload
        for key, value in self.type_fields(data).items():
            payload.setdefault(key, value)
        return payload

    @classmethod
    def _consume_sentinel(cls, node: Any) -> bool:
        """就地移除结构里所有等于哨兵的键（含嵌套 dict / list）。

        返回是否至少命中一次 —— 调用方据此判断模板作者是否显式请求了
        类型特有字段的注入。
        """
        found = False
        if isinstance(node, dict):
            for key in [k for k, v in node.items() if v == _TYPE_FIELDS_PLACEHOLDER]:
                del node[key]
                found = True
            for value in node.values():
                found = cls._consume_sentinel(value) or found
        elif isinstance(node, list):
            for item in node:
                found = cls._consume_sentinel(item) or found
        return found

    @staticmethod
    def derive_ep_label(data: dict[str, Any]) -> str:
        """推导集数标签：``"S01E02"`` / ``"剧场版"``；无集数信息时为空串。

        调用方已显式给出 ``ep_label`` 时原样返回（不覆盖）。

        判定以**数据本身**为准（是否真的带 season/episode），而非类型：
        追番总结、API 错误、磁盘告警这类通知根本没有集数字段，补零兜底会在
        正文里留下无意义的 ``S00E00``；返回空串配合模板的「无值省略」才是
        正确语义。
        """
        existing = data.get("ep_label")
        if existing:
            return str(existing)
        if str(data.get("media_type") or "") == "movie":
            return "剧场版"
        season = data.get("season")
        episode = data.get("episode")
        # 两者都缺（或为 None）才认为「没有集数信息」；0 是合法季/集号，保留。
        if season is None and episode is None:
            return ""
        return f"S{int(season or 0):02d}E{int(episode or 0):02d}"

    def render_email(
        self, data: dict[str, Any], template_name: str = "default"
    ) -> dict[str, str | None]:
        """渲染邮件，使用 ``email/<template_name>.html``（可配 ``.txt``）模板。

        所有通知类型共用同一套默认邮件模板。subject 从 HTML 的 ``<title>``
        标签提取，body 为纯文本 fallback，html 为渲染后的完整 HTML。

        body 优先取同名的 ``.txt`` 模板（``email/<name>.txt``）——它专门为
        text/plain 部件排版，不会带上 HTML 模板的缩进空白。若用户只提供
        了 ``.html``，则退化为「去标签 + 压缩空白」，保证结果可读。

        Returns:
            ``{"subject": str, "body": str, "html": str | None}``
        """
        raw_html = self.find_asset("email", template_name, "html")
        raw_text = self.find_asset("email", template_name, "txt")

        # 集数标签在正文模板里被引用，但调用方不一定注入（如直接调 render_email）。
        # 这里补齐，保证模板单独可用；已有值时不覆盖。
        if not data.get("ep_label"):
            data = {**data, "ep_label": self.derive_ep_label(data)}

        # 主题回退：优先用类型标题（如 "📊 追番总结 - 每日总结"），
        # 与 webhook payload 的 title 保持同一来源。
        subject_title = str(data.get("payload_title") or "").strip()
        if not subject_title:
            subject_title = str(data.get("type_display_name") or "")
        subject_fallback = f"[Bangumi-Syncer] {subject_title}".strip()

        if raw_html:
            # HTML 部件：变量先转义再插入，避免媒体库文件名/AI 正文里的标记
            # 被当作 HTML 执行（伪造链接、追踪像素等）。
            rendered_html = self.render_html(raw_html, data)
            # 从 <title> 标签提取 subject。HTML 已转义，主题是邮件头（非 HTML
            # 上下文），需反转义还原出可读文本。
            title_match = re.search(
                r"<title[^>]*>(.*?)</title>", rendered_html, re.IGNORECASE | re.DOTALL
            )
            subject = (
                unescape(title_match.group(1)).strip()
                if title_match
                else subject_fallback
            )
            if raw_text:
                body = self._collapse_blank_lines(self.render_string(raw_text, data))
            else:
                body = self._html_to_text(rendered_html)
            return {"subject": subject, "body": body, "html": rendered_html}

        if raw_text:
            # 只有纯文本模板：html 留空，由渠道按纯文本发送
            return {
                "subject": subject_fallback,
                "body": self._collapse_blank_lines(self.render_string(raw_text, data)),
                "html": None,
            }

        return {"subject": subject_fallback, "body": None, "html": None}

    @staticmethod
    def _collapse_blank_lines(text: str) -> str:
        """行首尾去空白、连续空行压成一个、去掉首尾空行。

        模板里的可选占位符（如 ``{summary_text}``）没值时会被替换成空串，
        在文本里留下一串空行；此处统一压掉。
        """
        out: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                out.append(line)
            elif out and out[-1] != "":
                out.append("")
        while out and out[-1] == "":
            out.pop()
        return "\n".join(out)

    @staticmethod
    def _html_to_text(rendered_html: str) -> str:
        """把渲染后的 HTML 转成可读的纯文本（供没有 .txt 模板时兜底）。

        HTML 模板普遍带缩进换行，直接去标签会留下大量空白行。这里按块级标签
        换行、丢弃纯空白行、并把每行首尾空白去掉。
        """
        # 先移除 style/script 块，避免残留 CSS/JS 文本
        text = re.sub(
            r"<(style|script)[^>]*>.*?</\1>",
            "",
            rendered_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        # 块级标签 → 换行，避免相邻文本粘连
        text = re.sub(
            r"</?(?:p|div|br|tr|li|h[1-6]|table|tbody|thead)[^>]*>",
            "\n",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"<[^>]+>", "", text)
        # 逐行去空白、丢弃空行（保留段落间最多一个空行）
        lines = [line.strip() for line in text.splitlines()]
        out: list[str] = []
        for line in lines:
            if line:
                out.append(line)
            elif out and out[-1] != "":
                out.append("")
        return "\n".join(out).strip()

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
