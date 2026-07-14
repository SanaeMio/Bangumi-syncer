"""邮件发送（mixin）"""

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any

from ...core.logging import logger


class EmailSenderMixin:
    """邮件发送相关方法（供 Notifier 组合）"""

    def _send_email_by_config(
        self, email_config: dict[str, Any], notification_type: str, data: dict[str, Any]
    ) -> bool:
        """
        根据配置发送邮件通知

        Args:
            email_config: 邮件配置字典
            notification_type: 通知类型
            data: 通知数据
        """
        try:
            # 获取配置
            smtp_server = email_config["smtp_server"]
            smtp_port = int(email_config.get("smtp_port", 587))
            smtp_username = email_config["smtp_username"]
            smtp_password = email_config["smtp_password"]
            smtp_use_tls = email_config.get("smtp_use_tls", True)
            from_email = email_config.get("email_from", smtp_username)
            to_email = email_config.get("email_to", "")
            email_subject = email_config.get("email_subject", "")
            email_template_file = email_config.get("email_template_file", "")

            # 验证配置
            if not from_email:
                from_email = smtp_username
            if not to_email:
                logger.warning(
                    f"邮件配置 ID={email_config.get('id')} 未配置收件人地址，跳过发送"
                )
                return False

            # 构建邮件
            msg = MIMEMultipart("alternative")

            # 使用自定义标题或根据通知类型生成标题
            if email_subject:
                subject = self._replace_template_variables(email_subject, data)
            else:
                subject = self._build_email_subject_by_type(notification_type, data)

            msg["Subject"] = subject
            msg["From"] = from_email
            msg["To"] = to_email
            msg["Date"] = formatdate(localtime=True)

            # 将 notification_type 添加到 data 字典中，以便模板可以使用
            data["notification_type"] = notification_type

            # 生成动态内容
            data["dynamic_content"] = self._build_email_dynamic_content(
                notification_type, data
            )

            # 邮件正文
            text_content = self._build_email_text_by_type(notification_type, data)
            html_content = self._load_email_template(email_template_file, data)

            # 添加纯文本部分
            part1 = MIMEText(text_content, "plain", "utf-8")
            msg.attach(part1)

            # 添加HTML部分
            part2 = MIMEText(html_content, "html", "utf-8")
            msg.attach(part2)

            # 发送邮件
            smtp_port_int = int(smtp_port)

            if smtp_port_int == 465:
                # 使用 SSL 连接
                context = ssl.create_default_context()
                server = smtplib.SMTP_SSL(
                    smtp_server, smtp_port_int, timeout=30, context=context
                )
                try:
                    server.set_debuglevel(0)
                    if smtp_username and smtp_password:
                        server.login(smtp_username, smtp_password)
                    server.send_message(msg)
                    server.quit()
                except Exception as e:
                    try:
                        server.quit()
                    except Exception:
                        pass
                    raise e
            else:
                # 使用 STARTTLS 连接
                server = smtplib.SMTP(smtp_server, smtp_port_int, timeout=30)
                try:
                    server.set_debuglevel(0)
                    if smtp_use_tls:
                        server.starttls()
                    if smtp_username and smtp_password:
                        server.login(smtp_username, smtp_password)
                    server.send_message(msg)
                    server.quit()
                except Exception as e:
                    try:
                        server.quit()
                    except Exception:
                        pass
                    raise e

            logger.info(
                f"✅ 邮件通知发送成功: {to_email} (配置ID={email_config.get('id')})"
            )
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"❌ 邮件认证失败 (配置ID={email_config.get('id')}): {str(e)}")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"❌ SMTP 错误 (配置ID={email_config.get('id')}): {str(e)}")
            return False
        except Exception as e:
            logger.error(
                f"❌ 邮件通知发送失败 (配置ID={email_config.get('id')}): {str(e)}"
            )
            return False
