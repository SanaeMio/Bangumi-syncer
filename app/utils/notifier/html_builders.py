"""通知邮件 HTML 内容构建（mixin）"""

from __future__ import annotations

import os
from typing import Any, Callable

from ...core.logging import logger


class EmailHtmlMixin:
    """邮件 HTML/文本/标题 构建相关方法（供 Notifier 组合）"""

    def _replace_template_variables(self, template: Any, data: dict[str, Any]) -> Any:
        """递归替换模板中的变量"""
        if isinstance(template, dict):
            return {
                k: self._replace_template_variables(v, data)
                for k, v in template.items()
            }
        elif isinstance(template, list):
            return [self._replace_template_variables(item, data) for item in template]
        elif isinstance(template, str):
            # 替换 {variable} 格式的变量
            import re

            # 使用正则表达式匹配所有 {variable} 格式的占位符
            pattern = r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}"

            def replace_match(match: Any) -> str:
                key = match.group(1)
                # 如果键存在，使用其值；否则使用空字符串
                return str(data.get(key, ""))

            return re.sub(pattern, replace_match, template)
        else:
            return template

    def _load_email_template(self, template_file: str, data: dict[str, Any]) -> str:
        """
        加载并渲染邮件 HTML 模板

        Args:
            template_file: 模板文件路径（相对或绝对路径）
            data: 用于替换模板变量的数据

        Returns:
            渲染后的 HTML 内容
        """
        # 如果没有指定模板文件，使用默认模板
        if not template_file:
            # 智能检测环境：Docker 环境优先使用 /config 目录的模板
            if os.getenv("DOCKER_CONTAINER") == "true" and os.path.exists(
                "/app/config/email_notification.html"
            ):
                template_file = "/app/config/email_notification.html"
            else:
                template_file = "templates/email_notification.html"

        # 支持相对路径和绝对路径
        if not os.path.isabs(template_file):
            # 相对于项目根目录
            project_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            template_path = os.path.join(project_root, template_file)
        else:
            template_path = template_file

        try:
            # 读取模板文件
            if os.path.exists(template_path):
                with open(template_path, encoding="utf-8") as f:
                    template_content = f.read()
            else:
                # 如果指定的模板不存在，尝试加载默认模板
                logger.warning(f"邮件模板文件不存在: {template_path}")
                if template_file != "templates/email_notification.html":
                    logger.info("尝试加载默认模板")
                    default_template_path = os.path.join(
                        os.path.dirname(
                            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        ),
                        "templates/email_notification.html",
                    )
                    if os.path.exists(default_template_path):
                        with open(default_template_path, encoding="utf-8") as f:
                            template_content = f.read()
                        logger.info("成功加载默认模板")
                    else:
                        raise FileNotFoundError(
                            f"默认模板文件也不存在: {default_template_path}"
                        )
                else:
                    raise FileNotFoundError(f"默认模板文件不存在: {template_path}")

            # 替换模板中的变量
            html_content = self._replace_template_variables(template_content, data)
            return html_content
        except Exception as e:
            logger.error(f"加载邮件模板失败: {e}，使用最简单的内置模板")
            # 最后的 fallback：使用最简单的内置模板
            return self._build_simple_email_html(data)

    def _build_simple_email_html(self, data: dict[str, Any]) -> str:
        """构建简单的 HTML 邮件内容（仅在模板文件完全无法加载时使用）"""
        notification_type = data.get("notification_type", "未知")

        # 根据通知类型设置标题颜色和图标
        type_config = {
            "request_received": {
                "color": "#0d6efd",
                "icon": "📥",
                "title": "收到同步请求",
            },
            "bangumi_id_found": {
                "color": "#198754",
                "icon": "🔍",
                "title": "匹配到番剧",
            },
            "mark_success": {"color": "#198754", "icon": "✅", "title": "同步成功"},
            "mark_failed": {"color": "#dc3545", "icon": "❌", "title": "同步失败"},
            "mark_skipped": {"color": "#6c757d", "icon": "⏭️", "title": "已看过，跳过"},
            "config_error": {"color": "#ffc107", "icon": "⚙️", "title": "配置错误"},
            "anime_not_found": {
                "color": "#fd7e14",
                "icon": "🔍",
                "title": "未找到番剧",
            },
            "episode_not_found": {
                "color": "#fd7e14",
                "icon": "📺",
                "title": "未找到剧集",
            },
            "api_auth_error": {
                "color": "#dc3545",
                "icon": "🔐",
                "title": "API认证失败",
            },
            "api_error": {"color": "#dc3545", "icon": "🌐", "title": "API错误"},
            "api_retry_failed": {
                "color": "#dc3545",
                "icon": "🔄",
                "title": "API重试失败",
            },
            "ip_locked": {"color": "#dc3545", "icon": "🔒", "title": "IP被锁定"},
            "pending_candidate": {
                "color": "#fd7e14",
                "icon": "📝",
                "title": "候选待确认",
            },
        }

        config = type_config.get(
            notification_type,
            {"color": "#6c757d", "icon": "📢", "title": notification_type},
        )

        # 构建详细信息HTML
        details_html = ""

        # 通用信息
        if data.get("timestamp"):
            details_html += f"<p><strong>时间:</strong> {data['timestamp']}</p>"

        # 番剧相关信息
        if data.get("title"):
            details_html += f"<p><strong>番剧:</strong> {data['title']}</p>"
        if data.get("season", 0) > 0 or data.get("episode", 0) > 0:
            details_html += f"<p><strong>集数:</strong> 第 {data.get('season', 0)} 季 第 {data.get('episode', 0)} 集</p>"
        if data.get("user_name"):
            details_html += f"<p><strong>用户:</strong> {data['user_name']}</p>"
        if data.get("source"):
            details_html += f"<p><strong>来源:</strong> {data['source']}</p>"

        # 错误相关信息
        if data.get("error_message"):
            details_html += f"<p><strong>错误信息:</strong> {data['error_message']}</p>"
        if data.get("error_type"):
            details_html += f"<p><strong>错误类型:</strong> {data['error_type']}</p>"

        # API相关信息
        if data.get("status_code"):
            details_html += f"<p><strong>状态码:</strong> {data['status_code']}</p>"
        if data.get("url"):
            details_html += f"<p><strong>URL:</strong> {data['url']}</p>"

        # ID相关信息
        if data.get("subject_id"):
            details_html += f"<p><strong>Subject ID:</strong> {data['subject_id']}</p>"
        if data.get("episode_id"):
            details_html += f"<p><strong>Episode ID:</strong> {data['episode_id']}</p>"

        # 候选待确认相关信息
        if data.get("candidates_count"):
            details_html += (
                f"<p><strong>候选数:</strong> {data['candidates_count']}</p>"
            )
        if data.get("top_candidate_name"):
            top_id = data.get("top_candidate_id", "")
            top_name = data.get("top_candidate_name", "")
            if top_id:
                details_html += (
                    f"<p><strong>首选候选:</strong> {top_name} (ID: {top_id})</p>"
                )
            else:
                details_html += f"<p><strong>首选候选:</strong> {top_name}</p>"

        # 动态内容（如果存在）
        if data.get("dynamic_content"):
            details_html += f'<div style="margin: 15px 0; padding: 10px; background-color: #f8f9fa; border-radius: 5px;">{data["dynamic_content"]}</div>'

        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{
            font-family: Arial, sans-serif;
            padding: 20px;
            color: #333;
            line-height: 1.6;
        }}
        h2 {{
            color: {config["color"]};
            margin-bottom: 20px;
        }}
        p {{
            margin: 5px 0;
        }}
        strong {{
            color: #495057;
        }}
        hr {{
            border: none;
            border-top: 1px solid #dee2e6;
            margin: 20px 0;
        }}
    </style>
</head>
<body>
    <h2>{config["icon"]} {config["title"]}</h2>
    {details_html}
    <hr>
    <p style="color: #6c757d; font-size: 12px;">此邮件由 Bangumi-Syncer 自动发送</p>
</body>
</html>"""

    def _build_email_subject_by_type(
        self, notification_type: str, data: dict[str, Any]
    ) -> str:
        """根据通知类型构建邮件标题"""
        if "watching_summary" in notification_type:
            return f"[Bangumi-Syncer] 📊 追番总结 - {data.get('job_name', '')}"

        subjects = {
            "request_received": f"[Bangumi-Syncer] 收到同步请求 - {data.get('title', '')} S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
            "bangumi_id_found": f"[Bangumi-Syncer] 匹配到番剧 - {data.get('title', '')}",
            "mark_success": f"[Bangumi-Syncer] 同步成功 - {data.get('title', '')} S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
            "mark_failed": f"[Bangumi-Syncer] 同步失败 - {data.get('title', '')} S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
            "mark_skipped": f"[Bangumi-Syncer] 已看过 - {data.get('title', '')} S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
            "config_error": "[Bangumi-Syncer] 配置错误",
            "anime_not_found": f"[Bangumi-Syncer] 未找到番剧 - {data.get('title', '')}",
            "episode_not_found": f"[Bangumi-Syncer] 未找到剧集 - {data.get('title', '')} S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
            "api_auth_error": "[Bangumi-Syncer] API认证失败",
            "api_error": "[Bangumi-Syncer] API错误",
            "api_retry_failed": "[Bangumi-Syncer] API重试失败",
            "ip_locked": "[Bangumi-Syncer] IP被锁定",
            "pending_candidate": f"[Bangumi-Syncer] 候选待确认 - {data.get('title', '')} S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
        }
        return subjects.get(notification_type, f"[Bangumi-Syncer] {notification_type}")

    def _build_email_text_by_type(
        self, notification_type: str, data: dict[str, Any]
    ) -> str:
        """根据通知类型构建纯文本邮件内容"""
        # 通知类型描述
        type_descriptions = {
            "request_received": "收到同步请求",
            "bangumi_id_found": "匹配到Bangumi番剧",
            "mark_success": "同步成功",
            "mark_failed": "同步失败",
            "mark_skipped": "已看过，跳过",
            "config_error": "配置错误",
            "anime_not_found": "未找到番剧",
            "episode_not_found": "未找到剧集",
            "api_auth_error": "API认证失败",
            "api_error": "API错误",
            "api_retry_failed": "API重试失败",
            "ip_locked": "IP被锁定",
            "pending_candidate": "候选待确认",
        }

        type_desc = type_descriptions.get(notification_type, notification_type)

        # 基础内容
        content = f"""Bangumi-Syncer 通知

时间: {data.get("timestamp", "")}
类型: {type_desc}
"""

        # 根据通知类型添加额外信息
        if notification_type in [
            "request_received",
            "bangumi_id_found",
            "mark_success",
            "mark_failed",
            "mark_skipped",
        ]:
            content += f"""
用户: {data.get("user_name", "")}
番剧: {data.get("title", "")}
集数: S{data.get("season", 0):02d}E{data.get("episode", 0):02d}
来源: {data.get("source", "")}
"""
            if notification_type == "mark_failed":
                content += f"\n错误信息: {data.get('error_message', '')}\n"
                content += f"错误类型: {data.get('error_type', '')}\n"
            elif notification_type in ["mark_success", "mark_skipped"]:
                content += f"\nSubject ID: {data.get('subject_id', '')}\n"
                content += f"Episode ID: {data.get('episode_id', '')}\n"

        elif notification_type == "bangumi_id_found":
            content += f"\nSubject ID: {data.get('subject_id', '')}\n"

        elif notification_type == "anime_not_found":
            content += f"""
用户: {data.get("user_name", "")}
搜索标题: {data.get("title", "")}
原始标题: {data.get("ori_title", "")}
季数: {data.get("season", 0)}
来源: {data.get("source", "")}
搜索方式: {data.get("search_method", "")}
"""

        elif notification_type == "episode_not_found":
            content += f"""
用户: {data.get("user_name", "")}
番剧: {data.get("title", "")}
季数: {data.get("season", 0)}
集数: {data.get("episode", 0)}
Subject ID: {data.get("subject_id", "")}
来源: {data.get("source", "")}
"""

        elif notification_type == "config_error":
            content += f"""
错误信息: {data.get("error_message", "")}
配置类型: {data.get("config_type", "")}
用户名: {data.get("user_name", "")}
模式: {data.get("mode", "")}
"""

        elif notification_type in ["api_auth_error", "api_error"]:
            content += f"""
状态码: {data.get("status_code", "")}
错误信息: {data.get("error_message", "")}
"""
            if notification_type == "api_auth_error":
                content += f"用户名: {data.get('username', '')}\n"
            elif notification_type == "api_error":
                content += f"URL: {data.get('url', '')}\n"
                content += f"方法: {data.get('method', '')}\n"
                content += f"重试次数: {data.get('retry_count', 0)}\n"

        elif notification_type == "api_retry_failed":
            content += f"""
Subject ID: {data.get("subject_id", "")}
Episode ID: {data.get("episode_id", "")}
最大重试次数: {data.get("max_retries", 0)}
错误信息: {data.get("error_message", "")}
"""

        elif notification_type == "ip_locked":
            content += f"""
IP地址: {data.get("ip", "")}
锁定至: {data.get("locked_until", "")}
尝试次数: {data.get("attempt_count", 0)}
最大尝试次数: {data.get("max_attempts", 0)}
"""

        elif notification_type == "pending_candidate":
            content += f"""
用户: {data.get("user_name", "")}
番剧: {data.get("title", "")}
集数: S{data.get("season", 0):02d}E{data.get("episode", 0):02d}
来源: {data.get("source", "")}
候选数: {data.get("candidates_count", 0)}
首选候选: {data.get("top_candidate_name", "")} (ID: {data.get("top_candidate_id", "")})
"""

        content += "\n---\n此邮件由 Bangumi-Syncer 自动发送\n"
        return content

    def _build_email_dynamic_content(
        self, notification_type: str, data: dict[str, Any]
    ) -> str:
        """
        根据通知类型构建邮件的动态HTML内容

        Args:
            notification_type: 通知类型
            data: 通知数据

        Returns:
            HTML内容字符串
        """
        if "watching_summary" in notification_type:
            content = f"""
            <div class="info-box" style="background: #f0f4ff; border-left: 4px solid #6c8ebf;">
                <div class="title">📊 {data.get("job_name", "追番总结")}</div>
                <div class="message">Period: {data.get("date_range", "")} | Records: {data.get("record_count", 0)}</div>
            </div>
            <div class="summary-section" style="padding: 12px; background: #fafafa; border-radius: 4px; margin-top: 8px;">
                <pre style="white-space: pre-wrap; font-family: inherit;">{data.get("summary_text", "")}</pre>
            </div>"""
            return content

        builders: dict[str, Callable[[dict[str, Any]], str]] = {
            "request_received": self._build_request_received_html,
            "bangumi_id_found": self._build_bangumi_id_found_html,
            "mark_success": self._build_mark_success_html,
            "mark_failed": self._build_mark_failed_html,
            "mark_skipped": self._build_mark_skipped_html,
            "anime_not_found": self._build_anime_not_found_html,
            "episode_not_found": self._build_episode_not_found_html,
            "config_error": self._build_config_error_html,
            "api_auth_error": self._build_api_auth_error_html,
            "api_error": self._build_api_error_html,
            "api_retry_failed": self._build_api_retry_failed_html,
            "ip_locked": self._build_ip_locked_html,
            "pending_candidate": self._build_pending_candidate_html,
        }
        builder = builders.get(notification_type)
        return builder(data) if builder else ""

    def _build_anime_section_html(
        self,
        data: dict[str, Any],
        *,
        with_subject_id: bool = False,
        with_episode_id: bool = False,
    ) -> str:
        """构建番剧信息区块 HTML（供 bangumi 事件类型共享）"""
        content = f"""
            <div class="anime-section">
                <div class="section-title"><span class="emoji">📺</span> 番剧信息</div>
                <div class="anime-info">
                    <div><strong>标题:</strong> {data.get("title", "")}</div>
                    <div><strong>集数:</strong> 第 {data.get("season", 0)} 季 第 {data.get("episode", 0)} 集</div>
                    <div><strong>用户:</strong> {data.get("user_name", "")}</div>
                    <div><strong>来源:</strong> {data.get("source", "")}</div>"""
        if with_subject_id:
            content += f"""
                    <div><strong>Subject ID:</strong> {data.get("subject_id", "")}</div>"""
        if with_episode_id:
            content += f"""
                    <div><strong>Episode ID:</strong> {data.get("episode_id", "")}</div>"""
        content += """
                </div>
            </div>"""
        return content

    def _build_request_received_html(self, data: dict[str, Any]) -> str:
        """构建 request_received 通知 HTML"""
        content = f"""
            <div class="info-box">
                <div class="title"><span class="emoji">📥</span> 收到同步请求</div>
                <div class="message">收到来自 {data.get("source", "")} 的同步请求</div>
            </div>"""
        return content + self._build_anime_section_html(data)

    def _build_bangumi_id_found_html(self, data: dict[str, Any]) -> str:
        """构建 bangumi_id_found 通知 HTML"""
        content = """
            <div class="info-box">
                <div class="title"><span class="emoji">🔍</span> 匹配到番剧</div>
                <div class="message">成功匹配到 Bangumi 番剧信息</div>
            </div>"""
        return content + self._build_anime_section_html(data, with_subject_id=True)

    def _build_mark_success_html(self, data: dict[str, Any]) -> str:
        """构建 mark_success 通知 HTML"""
        content = """
            <div class="info-box success">
                <div class="title"><span class="emoji">✅</span> 同步成功</div>
                <div class="message">番剧已成功标记为已看</div>
            </div>"""
        return content + self._build_anime_section_html(
            data, with_subject_id=True, with_episode_id=True
        )

    def _build_mark_failed_html(self, data: dict[str, Any]) -> str:
        """构建 mark_failed 通知 HTML"""
        content = f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">❌</span> 错误详情</div>
                <div class="message">{data.get("error_message", "")}</div>
            </div>"""
        return content + self._build_anime_section_html(data)

    def _build_mark_skipped_html(self, data: dict[str, Any]) -> str:
        """构建 mark_skipped 通知 HTML"""
        content = """
            <div class="info-box">
                <div class="title"><span class="emoji">⏭️</span> 已看过</div>
                <div class="message">该集已经标记为已看，跳过标记</div>
            </div>"""
        return content + self._build_anime_section_html(
            data, with_subject_id=True, with_episode_id=True
        )

    def _build_anime_not_found_html(self, data: dict[str, Any]) -> str:
        """构建 anime_not_found 通知 HTML"""
        return f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">🔍</span> 未找到番剧</div>
                <div class="message">
                    未能找到匹配的番剧信息<br>
                    搜索标题: {data.get("title", "")}<br>
                    原始标题: {data.get("ori_title", "")}<br>
                    搜索方式: {data.get("search_method", "")}
                </div>
            </div>
            <div class="anime-section">
                <div class="section-title"><span class="emoji">📺</span> 番剧信息</div>
                <div class="anime-info">
                    <div><strong>标题:</strong> {data.get("title", "")}</div>
                    <div><strong>季数:</strong> 第 {data.get("season", 0)} 季</div>
                    <div><strong>用户:</strong> {data.get("user_name", "")}</div>
                    <div><strong>来源:</strong> {data.get("source", "")}</div>
                </div>
            </div>"""

    def _build_episode_not_found_html(self, data: dict[str, Any]) -> str:
        """构建 episode_not_found 通知 HTML"""
        return f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">🔍</span> 未找到剧集</div>
                <div class="message">
                    未能找到匹配的剧集信息<br>
                    Subject ID: {data.get("subject_id", "")}
                </div>
            </div>
            <div class="anime-section">
                <div class="section-title"><span class="emoji">📺</span> 番剧信息</div>
                <div class="anime-info">
                    <div><strong>标题:</strong> {data.get("title", "")}</div>
                    <div><strong>集数:</strong> 第 {data.get("season", 0)} 季 第 {data.get("episode", 0)} 集</div>
                    <div><strong>用户:</strong> {data.get("user_name", "")}</div>
                    <div><strong>来源:</strong> {data.get("source", "")}</div>
                </div>
            </div>"""

    def _build_config_error_html(self, data: dict[str, Any]) -> str:
        """构建 config_error 通知 HTML"""
        return f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">⚙️</span> 配置错误</div>
                <div class="message">
                    {data.get("error_message", "")}<br>
                    配置类型: {data.get("config_type", "")}<br>
                    模式: {data.get("mode", "")}
                </div>
            </div>
            <div class="info-grid">
                <div class="info-row">
                    <div class="info-label"><span class="emoji">👤</span> 用户名</div>
                    <div class="info-value">{data.get("user_name", "")}</div>
                </div>
            </div>"""

    def _build_api_auth_error_html(self, data: dict[str, Any]) -> str:
        """构建 api_auth_error 通知 HTML"""
        return f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">🔐</span> API认证失败</div>
                <div class="message">
                    Bangumi API 认证失败<br>
                    请检查 access_token 是否正确
                </div>
            </div>
            <div class="info-grid">
                <div class="info-row">
                    <div class="info-label"><span class="emoji">👤</span> 用户名</div>
                    <div class="info-value">{data.get("username", "")}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">📊</span> 状态码</div>
                    <div class="info-value">{data.get("status_code", "")}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">💬</span> 错误信息</div>
                    <div class="info-value">{data.get("error_message", "")}</div>
                </div>
            </div>"""

    def _build_api_error_html(self, data: dict[str, Any]) -> str:
        """构建 api_error 通知 HTML"""
        return f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">🌐</span> API错误</div>
                <div class="message">
                    Bangumi API 返回错误状态码<br>
                    {data.get("error_message", "")}
                </div>
            </div>
            <div class="info-grid">
                <div class="info-row">
                    <div class="info-label"><span class="emoji">📊</span> 状态码</div>
                    <div class="info-value">{data.get("status_code", "")}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">🔗</span> URL</div>
                    <div class="info-value">{data.get("url", "")}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">📝</span> 方法</div>
                    <div class="info-value">{data.get("method", "")}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">🔄</span> 重试次数</div>
                    <div class="info-value">{data.get("retry_count", 0)}</div>
                </div>
            </div>"""

    def _build_api_retry_failed_html(self, data: dict[str, Any]) -> str:
        """构建 api_retry_failed 通知 HTML"""
        return f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">🔄</span> API重试失败</div>
                <div class="message">
                    API请求重试多次后仍然失败<br>
                    {data.get("error_message", "")}
                </div>
            </div>
            <div class="info-grid">
                <div class="info-row">
                    <div class="info-label"><span class="emoji">📺</span> Subject ID</div>
                    <div class="info-value">{data.get("subject_id", "")}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">📼</span> Episode ID</div>
                    <div class="info-value">{data.get("episode_id", "")}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">🔄</span> 最大重试次数</div>
                    <div class="info-value">{data.get("max_retries", 0)}</div>
                </div>
            </div>"""

    def _build_ip_locked_html(self, data: dict[str, Any]) -> str:
        """构建 ip_locked 通知 HTML"""
        return f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">🔒</span> IP被锁定</div>
                <div class="message">
                    由于登录失败次数过多，IP地址已被锁定<br>
                    请等待锁定时间结束后重试
                </div>
            </div>
            <div class="info-grid">
                <div class="info-row">
                    <div class="info-label"><span class="emoji">🌐</span> IP地址</div>
                    <div class="info-value">{data.get("ip", "")}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">⏰</span> 锁定至</div>
                    <div class="info-value">{data.get("locked_until", "")}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">🔢</span> 尝试次数</div>
                    <div class="info-value">{data.get("attempt_count", 0)} / {data.get("max_attempts", 0)}</div>
                </div>
            </div>"""

    def _build_pending_candidate_html(self, data: dict[str, Any]) -> str:
        """构建 pending_candidate 通知 HTML"""
        top_name = data.get("top_candidate_name", "")
        top_id = data.get("top_candidate_id", "")
        top_html = ""
        if top_name or top_id:
            top_html = f"""
                <div class="info-row">
                    <div class="info-label"><span class="emoji">🎯</span> 首选候选</div>
                    <div class="info-value">{top_name} (ID: {top_id})</div>
                </div>"""
        return f"""
            <div class="info-box">
                <div class="title"><span class="emoji">📝</span> 候选待确认</div>
                <div class="message">
                    匹配失败但存在候选，已沉淀到候选确认页面<br>
                    请前往 WebUI「候选确认」手动确认
                </div>
            </div>
            <div class="anime-section">
                <div class="section-title"><span class="emoji">📺</span> 番剧信息</div>
                <div class="anime-info">
                    <div><strong>标题:</strong> {data.get("title", "")}</div>
                    <div><strong>集数:</strong> 第 {data.get("season", 0)} 季 第 {data.get("episode", 0)} 集</div>
                    <div><strong>用户:</strong> {data.get("user_name", "")}</div>
                    <div><strong>来源:</strong> {data.get("source", "")}</div>
                    <div><strong>候选数:</strong> {data.get("candidates_count", 0)}</div>
                </div>
            </div>
            <div class="info-grid">{top_html}
            </div>"""
