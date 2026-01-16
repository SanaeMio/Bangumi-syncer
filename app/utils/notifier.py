"""
通知模块 - 支持 Webhook 和邮件通知
"""
import requests
import smtplib
import ssl
import time
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formatdate
from typing import Optional, Dict, Any
from datetime import datetime
from ..core.logging import logger


class Notifier:
    """通知管理器"""
    
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self._last_notification_time = {}
        self._notification_cooldown = 60  # 同一类型通知的冷却时间（秒）
    
    def _should_send_notification(self, notification_type: str) -> bool:
        """检查是否应该发送通知（防止通知轰炸）"""
        current_time = time.time()
        last_time = self._last_notification_time.get(notification_type, 0)
        
        if current_time - last_time < self._notification_cooldown:
            logger.debug(f"通知冷却中，跳过 {notification_type} 类型通知")
            return False
        
        self._last_notification_time[notification_type] = current_time
        return True
    
    def send_error_notification(self, error_type: str, error_message: str,
                               context: Optional[Dict[str, Any]] = None) -> None:
        """
        发送错误通知（保持向后兼容）

        Args:
            error_type: 错误类型（如 "sync_error", "proxy_error" 等）
            error_message: 错误消息
            context: 额外的上下文信息
        """
        # 检查是否启用邮件通知
        if self._is_email_enabled():
            notification_data = self._build_notification_data(error_type, error_message, context)
            self._send_email(notification_data)

        # 使用新的通知系统发送webhook通知
        context = context or {}
        data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'error_type': error_type,
            'error_message': error_message,
            'user_name': context.get('user_name', 'Unknown'),
            'title': context.get('title', 'Unknown'),
            'season': context.get('season', 0),
            'episode': context.get('episode', 0),
            'source': context.get('source', 'Unknown'),
            'additional_info': context.get('additional_info', '')
        }

        self.send_notification_by_type('mark_failed', data)
    
    def _is_notification_enabled(self) -> bool:
        """检查是否启用了任何通知方式"""
        return self._is_webhook_enabled() or self._is_email_enabled()
    
    def _is_webhook_enabled(self) -> bool:
        """检查是否启用 Webhook 通知"""
        enabled = self.config_manager.get('notification', 'webhook_enabled', fallback=False)
        webhook_url = self.config_manager.get('notification', 'webhook_url', fallback='')
        return bool(enabled) and bool(webhook_url)
    
    def _is_email_enabled(self) -> bool:
        """检查是否启用邮件通知"""
        enabled = self.config_manager.get('notification', 'email_enabled', fallback=False)
        smtp_server = self.config_manager.get('notification', 'smtp_server', fallback='')
        return bool(enabled) and bool(smtp_server)
    
    def _build_notification_data(self, error_type: str, error_message: str, 
                                 context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """构建通知数据"""
        context = context or {}
        
        return {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'error_type': error_type,
            'error_message': error_message,
            'user_name': context.get('user_name', 'Unknown'),
            'title': context.get('title', 'Unknown'),
            'season': context.get('season', 0),
            'episode': context.get('episode', 0),
            'source': context.get('source', 'Unknown'),
            'additional_info': context.get('additional_info', '')
        }
    
    def _send_webhook(self, data: Dict[str, Any], raise_on_error: bool = False) -> bool:
        """
        发送 Webhook 通知
        
        Args:
            data: 通知数据
            raise_on_error: 是否在错误时抛出异常（测试模式使用）
            
        Returns:
            是否发送成功
        """
        webhook_url = self.config_manager.get('notification', 'webhook_url', fallback='')
        webhook_method = self.config_manager.get('notification', 'webhook_method', fallback='POST').upper()
        webhook_format = self.config_manager.get('notification', 'webhook_format', fallback='json')
        custom_headers = self.config_manager.get('notification', 'webhook_headers', fallback='')
        
        try:
            # 构建请求头
            headers = {'User-Agent': 'Bangumi-Syncer-Notifier'}
            
            # 解析自定义请求头
            if custom_headers:
                for header in custom_headers.split(','):
                    if ':' in header:
                        key, value = header.split(':', 1)
                        headers[key.strip()] = value.strip()
            
            # 构建请求体
            if webhook_format == 'json':
                headers['Content-Type'] = 'application/json'
                payload = self._build_webhook_json_payload(data)
            else:  # text
                headers['Content-Type'] = 'text/plain'
                payload = self._build_webhook_text_payload(data)
            
            # 打印发送的内容（用于调试）
            import json
            if isinstance(payload, dict):
                logger.info(f"📤 发送 Webhook 通知到: {webhook_url}")
                logger.info(f"📋 请求方法: {webhook_method}")
                logger.info(f"📦 发送内容: {json.dumps(payload, ensure_ascii=False, indent=2)}")
            else:
                logger.info(f"📤 发送 Webhook 通知到: {webhook_url}")
                logger.info(f"📋 请求方法: {webhook_method}")
                logger.info(f"📦 发送内容: {payload}")
            
            # 发送请求
            if webhook_method == 'POST':
                if webhook_format == 'json':
                    response = requests.post(webhook_url, json=payload, headers=headers, timeout=10)
                else:
                    response = requests.post(webhook_url, data=payload, headers=headers, timeout=10)
            else:  # GET
                response = requests.get(webhook_url, params=payload if isinstance(payload, dict) else None, 
                                       headers=headers, timeout=10)
            
            if response.status_code < 300:
                logger.info(f"✅ Webhook 通知发送成功，响应状态码: {response.status_code}")
                return True
            else:
                error_msg = f"Webhook 返回非成功状态码: {response.status_code}"
                logger.warning(f"⚠️  {error_msg}")
                if raise_on_error:
                    raise Exception(error_msg)
                return False
                
        except Exception as e:
            logger.error(f"❌ Webhook 通知发送失败: {str(e)}")
            if raise_on_error:
                raise
            return False
    
    def _build_webhook_json_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """构建 JSON 格式的 Webhook 载荷"""
        webhook_template = self.config_manager.get('notification', 'webhook_template', fallback='')
        
        if webhook_template:
            # 使用自定义模板
            try:
                import json
                template = json.loads(webhook_template)
                # 替换模板中的变量
                return self._replace_template_variables(template, data)
            except Exception as e:
                logger.warning(f"自定义 Webhook 模板解析失败: {e}，使用默认格式")
        
        # 默认格式
        return {
            'title': '🚨 Bangumi-Syncer 同步错误',
            'type': data['error_type'],
            'message': data['error_message'],
            'timestamp': data['timestamp'],
            'details': {
                'user': data['user_name'],
                'anime': data['title'],
                'episode': f"S{data['season']:02d}E{data['episode']:02d}",
                'source': data['source']
            }
        }
    
    def _build_webhook_text_payload(self, data: Dict[str, Any]) -> str:
        """构建文本格式的 Webhook 载荷"""
        return f"""🚨 Bangumi-Syncer 同步错误

时间: {data['timestamp']}
错误类型: {data['error_type']}
错误消息: {data['error_message']}

详细信息:
- 用户: {data['user_name']}
- 番剧: {data['title']}
- 集数: S{data['season']:02d}E{data['episode']:02d}
- 来源: {data['source']}

{data['additional_info']}
"""
    
    def _replace_template_variables(self, template: Any, data: Dict[str, Any]) -> Any:
        """递归替换模板中的变量"""
        if isinstance(template, dict):
            return {k: self._replace_template_variables(v, data) for k, v in template.items()}
        elif isinstance(template, list):
            return [self._replace_template_variables(item, data) for item in template]
        elif isinstance(template, str):
            # 替换 {variable} 格式的变量
            import re
            # 使用正则表达式匹配所有 {variable} 格式的占位符
            pattern = r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}'
            def replace_match(match):
                key = match.group(1)
                # 如果键存在，使用其值；否则使用空字符串
                return str(data.get(key, ''))
            return re.sub(pattern, replace_match, template)
        else:
            return template
    
    def _send_email(self, data: Dict[str, Any], raise_on_error: bool = False) -> bool:
        """
        发送邮件通知
        
        Args:
            data: 通知数据
            raise_on_error: 是否在错误时抛出异常（测试模式使用）
            
        Returns:
            是否发送成功
        """
        try:
            # 获取邮件配置
            smtp_server = self.config_manager.get('notification', 'smtp_server', fallback='')
            smtp_port = self.config_manager.get('notification', 'smtp_port', fallback=587)
            smtp_username = self.config_manager.get('notification', 'smtp_username', fallback='')
            smtp_password = self.config_manager.get('notification', 'smtp_password', fallback='')
            smtp_use_tls = self.config_manager.get('notification', 'smtp_use_tls', fallback=True)
            
            from_email = self.config_manager.get('notification', 'email_from', fallback=smtp_username)
            to_email = self.config_manager.get('notification', 'email_to', fallback='')
            
            # 如果发件人为空，使用 SMTP 用户名
            if not from_email:
                from_email = smtp_username
            
            if not from_email:
                error_msg = "未配置发件人邮箱地址（email_from 或 smtp_username）"
                logger.error(error_msg)
                if raise_on_error:
                    raise Exception(error_msg)
                return False
            
            if not to_email:
                error_msg = "未配置收件人邮箱地址"
                logger.error(error_msg)
                if raise_on_error:
                    raise Exception(error_msg)
                return False
            
            # 打印邮件配置信息
            logger.info(f"📧 准备发送邮件通知到: {to_email}")
            
            # 获取自定义邮件标题和模板文件路径
            email_subject = self.config_manager.get('notification', 'email_subject', fallback='')
            email_template_file = self.config_manager.get('notification', 'email_template_file', fallback='')
            
            # 构建邮件
            msg = MIMEMultipart('alternative')
            
            # 使用自定义标题或默认标题
            if email_subject:
                subject = self._replace_template_variables(email_subject, data)
            else:
                subject = f"[Bangumi-Syncer] 同步错误 - {title} S{season}E{episode}"
            
            msg['Subject'] = subject
            msg['From'] = from_email
            msg['To'] = to_email
            msg['Date'] = formatdate(localtime=True)
            
            # 邮件正文 - 纯文本和HTML两种格式
            text_content = self._build_email_text(data)
            html_content = self._load_email_template(email_template_file, data)
            
            # 添加纯文本部分
            part1 = MIMEText(text_content, 'plain', 'utf-8')
            msg.attach(part1)
            
            # 添加HTML部分
            part2 = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(part2)
            
            # 根据端口和配置选择合适的连接方式
            smtp_port_int = int(smtp_port)
            
            # 465 端口必须使用 SSL（强制），587 端口使用 STARTTLS
            if smtp_port_int == 465:
                # 使用 SSL 连接（端口 465）
                context = ssl.create_default_context()
                
                server = smtplib.SMTP_SSL(smtp_server, smtp_port_int, timeout=30, context=context)
                try:
                    server.set_debuglevel(0)
                    if smtp_username and smtp_password:
                        server.login(smtp_username, smtp_password)
                    server.send_message(msg)
                    server.quit()
                except Exception as e:
                    try:
                        server.quit()
                    except:
                        pass
                    raise e
            else:
                # 使用 STARTTLS 连接（端口 587 或其他）
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
                    except:
                        pass
                    raise e
            
            logger.info(f"✅ 邮件通知发送成功: {to_email}")
            return True
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"❌ 邮件认证失败: {str(e)}")
            logger.error("请检查用户名和密码（QQ邮箱需要使用授权码，不是登录密码）")
            if raise_on_error:
                raise
            return False
        except smtplib.SMTPException as e:
            logger.error(f"❌ SMTP 错误: {str(e)}")
            if raise_on_error:
                raise
            return False
        except Exception as e:
            logger.error(f"❌ 邮件通知发送失败: {str(e)}")
            logger.error(f"错误类型: {type(e).__name__}")
            import traceback
            logger.debug(f"详细错误:\n{traceback.format_exc()}")
            if raise_on_error:
                raise
            return False
    
    def _build_email_text(self, data: Dict[str, Any]) -> str:
        """构建纯文本邮件内容"""
        return f"""Bangumi-Syncer 同步错误通知

时间: {data['timestamp']}
错误类型: {data['error_type']}
错误消息: {data['error_message']}

详细信息:
- 用户: {data['user_name']}
- 番剧: {data['title']}
- 集数: S{data['season']:02d}E{data['episode']:02d}
- 来源: {data['source']}

{data['additional_info']}

---
此邮件由 Bangumi-Syncer 自动发送
"""
    
    def _load_email_template(self, template_file: str, data: Dict[str, Any]) -> str:
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
            if os.getenv('DOCKER_CONTAINER') == 'true' and os.path.exists('/app/config/email_notification.html'):
                template_file = '/app/config/email_notification.html'
            else:
                template_file = 'templates/email_notification.html'
        
        # 支持相对路径和绝对路径
        if not os.path.isabs(template_file):
            # 相对于项目根目录
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            template_path = os.path.join(project_root, template_file)
        else:
            template_path = template_file
        
        try:
            # 读取模板文件
            if os.path.exists(template_path):
                with open(template_path, 'r', encoding='utf-8') as f:
                    template_content = f.read()
            else:
                # 如果指定的模板不存在，尝试加载默认模板
                logger.warning(f"邮件模板文件不存在: {template_path}")
                if template_file != 'templates/email_notification.html':
                    logger.info("尝试加载默认模板")
                    default_template_path = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        'templates/email_notification.html'
                    )
                    if os.path.exists(default_template_path):
                        with open(default_template_path, 'r', encoding='utf-8') as f:
                            template_content = f.read()
                        logger.info(f"成功加载默认模板")
                    else:
                        raise FileNotFoundError(f"默认模板文件也不存在: {default_template_path}")
                else:
                    raise FileNotFoundError(f"默认模板文件不存在: {template_path}")
            
            # 替换模板中的变量
            html_content = self._replace_template_variables(template_content, data)
            return html_content
            
        except Exception as e:
            logger.error(f"加载邮件模板失败: {e}，使用最简单的内置模板")
            # 最后的 fallback：使用最简单的内置模板
            return self._build_simple_email_html(data)
    
    def _build_simple_email_html(self, data: Dict[str, Any]) -> str:
        """构建简单的 HTML 邮件内容（仅在模板文件完全无法加载时使用）"""
        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; padding: 20px; color: #333;">
    <h2 style="color: #dc3545;">🚨 Bangumi-Syncer 同步错误</h2>
    <p><strong>时间:</strong> {data['timestamp']}</p>
    <p><strong>错误类型:</strong> {data['error_type']}</p>
    <p><strong>错误消息:</strong> {data['error_message']}</p>
    <hr>
    <p><strong>番剧:</strong> {data['title']}</p>
    <p><strong>集数:</strong> S{data['season']}E{data['episode']}</p>
    <p><strong>用户:</strong> {data['user_name']}</p>
    <p><strong>来源:</strong> {data['source']}</p>
</body>
</html>"""
    
    
    def send_notification_by_type(
        self,
        notification_type: str,
        data: Dict[str, Any]
    ) -> None:
        """
        根据通知类型发送通知

        Args:
            notification_type: 通知类型（request_received, bangumi_id_found, mark_success, mark_failed, mark_skipped）
            data: 通知数据（包含timestamp, user_name, title, season, episode, source等）
        """
        # 获取所有启用的webhook配置
        webhook_configs = self._get_webhook_configs()

        for webhook_config in webhook_configs:
            # 检查是否启用
            if not webhook_config.get('enabled', False):
                continue

            # 检查是否支持此通知类型
            types = webhook_config.get('types', '')
            if types != 'all' and notification_type not in types:
                continue

            # 检查冷却时间
            cooldown_key = f"{webhook_config['id']}_{notification_type}"
            if not self._should_send_notification(cooldown_key):
                continue

            # 发送webhook通知
            self._send_webhook_by_config(webhook_config, notification_type, data)

        # 获取所有启用的邮件配置
        email_configs = self._get_email_configs()

        for email_config in email_configs:
            # 检查是否启用
            if not email_config.get('enabled', False):
                continue

            # 检查是否支持此通知类型
            types = email_config.get('types', '')
            if types != 'all' and notification_type not in types:
                continue

            # 检查冷却时间
            cooldown_key = f"email_{email_config['id']}_{notification_type}"
            if not self._should_send_notification(cooldown_key):
                continue

            # 发送邮件通知
            self._send_email_by_config(email_config, notification_type, data)

    def _get_webhook_configs(self) -> list:
        """获取所有webhook配置"""
        config = self.config_manager.get_config_parser()
        webhook_configs = []

        for section_name in config.sections():
            if section_name.startswith('webhook-'):
                section_config = self.config_manager.get_section(section_name)
                if section_config.get('url'):  # 必须有URL才有效
                    webhook_configs.append(section_config)

        return webhook_configs

    def _get_email_configs(self) -> list:
        """获取所有邮件配置"""
        config = self.config_manager.get_config_parser()
        email_configs = []

        for section_name in config.sections():
            if section_name.startswith('email-'):
                section_config = self.config_manager.get_section(section_name)
                if section_config.get('smtp_server'):  # 必须有SMTP服务器才有效
                    email_configs.append(section_config)

        return email_configs

    def _send_webhook_by_config(
        self,
        webhook_config: Dict[str, Any],
        notification_type: str,
        data: Dict[str, Any]
    ) -> bool:
        """
        根据配置发送webhook通知

        Args:
            webhook_config: webhook配置字典
            notification_type: 通知类型
            data: 通知数据
        """
        try:
            url = webhook_config['url']
            method = webhook_config.get('method', 'POST').upper()
            headers = self._parse_headers(webhook_config.get('headers', ''))
            template = webhook_config.get('template', '')

            # 构建载荷
            payload = self._build_payload_by_type(notification_type, data, template)

            # 发送请求
            logger.info(f"📤 发送 {notification_type} 通知到: {url}")

            if method == 'POST':
                response = requests.post(url, json=payload, headers=headers, timeout=10)
            else:  # GET
                response = requests.get(url, params=payload if isinstance(payload, dict) else None,
                                       headers=headers, timeout=10)

            if response.status_code < 300:
                logger.info(f"✅ Webhook通知发送成功，响应状态码: {response.status_code}")
                return True
            else:
                logger.warning(f"⚠️  Webhook返回非成功状态码: {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"❌ Webhook通知发送失败: {str(e)}")
            return False

    def _send_email_by_config(
        self,
        email_config: Dict[str, Any],
        notification_type: str,
        data: Dict[str, Any]
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
            smtp_server = email_config['smtp_server']
            smtp_port = int(email_config.get('smtp_port', 587))
            smtp_username = email_config['smtp_username']
            smtp_password = email_config['smtp_password']
            smtp_use_tls = email_config.get('smtp_use_tls', True)
            from_email = email_config.get('email_from', smtp_username)
            to_email = email_config.get('email_to', '')
            email_subject = email_config.get('email_subject', '')
            email_template_file = email_config.get('email_template_file', '')

            # 验证配置
            if not from_email:
                from_email = smtp_username
            if not to_email:
                logger.warning(f"邮件配置 ID={email_config.get('id')} 未配置收件人地址，跳过发送")
                return False

            # 构建邮件
            msg = MIMEMultipart('alternative')

            # 使用自定义标题或根据通知类型生成标题
            if email_subject:
                subject = self._replace_template_variables(email_subject, data)
            else:
                subject = self._build_email_subject_by_type(notification_type, data)

            msg['Subject'] = subject
            msg['From'] = from_email
            msg['To'] = to_email
            msg['Date'] = formatdate(localtime=True)

            # 将 notification_type 添加到 data 字典中，以便模板可以使用
            data['notification_type'] = notification_type

            # 生成动态内容
            data['dynamic_content'] = self._build_email_dynamic_content(notification_type, data)

            # 邮件正文
            text_content = self._build_email_text_by_type(notification_type, data)
            html_content = self._load_email_template(email_template_file, data)

            # 添加纯文本部分
            part1 = MIMEText(text_content, 'plain', 'utf-8')
            msg.attach(part1)

            # 添加HTML部分
            part2 = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(part2)

            # 发送邮件
            smtp_port_int = int(smtp_port)

            if smtp_port_int == 465:
                # 使用 SSL 连接
                context = ssl.create_default_context()
                server = smtplib.SMTP_SSL(smtp_server, smtp_port_int, timeout=30, context=context)
                try:
                    server.set_debuglevel(0)
                    if smtp_username and smtp_password:
                        server.login(smtp_username, smtp_password)
                    server.send_message(msg)
                    server.quit()
                except Exception as e:
                    try:
                        server.quit()
                    except:
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
                    except:
                        pass
                    raise e

            logger.info(f"✅ 邮件通知发送成功: {to_email} (配置ID={email_config.get('id')})")
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"❌ 邮件认证失败 (配置ID={email_config.get('id')}): {str(e)}")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"❌ SMTP 错误 (配置ID={email_config.get('id')}): {str(e)}")
            return False
        except Exception as e:
            logger.error(f"❌ 邮件通知发送失败 (配置ID={email_config.get('id')}): {str(e)}")
            return False

    def _build_email_subject_by_type(self, notification_type: str, data: Dict[str, Any]) -> str:
        """根据通知类型构建邮件标题"""
        subjects = {
            'request_received': f"[Bangumi-Syncer] 收到同步请求 - {data.get('title', '')} S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
            'bangumi_id_found': f"[Bangumi-Syncer] 匹配到番剧 - {data.get('title', '')}",
            'mark_success': f"[Bangumi-Syncer] 同步成功 - {data.get('title', '')} S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
            'mark_failed': f"[Bangumi-Syncer] 同步失败 - {data.get('title', '')} S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
            'mark_skipped': f"[Bangumi-Syncer] 已看过 - {data.get('title', '')} S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
            'config_error': f"[Bangumi-Syncer] 配置错误",
            'anime_not_found': f"[Bangumi-Syncer] 未找到番剧 - {data.get('title', '')}",
            'episode_not_found': f"[Bangumi-Syncer] 未找到剧集 - {data.get('title', '')} S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
            'api_auth_error': f"[Bangumi-Syncer] API认证失败",
            'api_error': f"[Bangumi-Syncer] API错误",
            'api_retry_failed': f"[Bangumi-Syncer] API重试失败",
            'ip_locked': f"[Bangumi-Syncer] IP被锁定"
        }
        return subjects.get(notification_type, f"[Bangumi-Syncer] {notification_type}")

    def _build_email_text_by_type(self, notification_type: str, data: Dict[str, Any]) -> str:
        """根据通知类型构建纯文本邮件内容"""
        # 通知类型描述
        type_descriptions = {
            'request_received': '收到同步请求',
            'bangumi_id_found': '匹配到Bangumi番剧',
            'mark_success': '同步成功',
            'mark_failed': '同步失败',
            'mark_skipped': '已看过，跳过',
            'config_error': '配置错误',
            'anime_not_found': '未找到番剧',
            'episode_not_found': '未找到剧集',
            'api_auth_error': 'API认证失败',
            'api_error': 'API错误',
            'api_retry_failed': 'API重试失败',
            'ip_locked': 'IP被锁定'
        }

        type_desc = type_descriptions.get(notification_type, notification_type)

        # 基础内容
        content = f"""Bangumi-Syncer 通知

时间: {data.get('timestamp', '')}
类型: {type_desc}
"""

        # 根据通知类型添加额外信息
        if notification_type in ['request_received', 'bangumi_id_found', 'mark_success', 'mark_failed', 'mark_skipped']:
            content += f"""
用户: {data.get('user_name', '')}
番剧: {data.get('title', '')}
集数: S{data.get('season', 0):02d}E{data.get('episode', 0):02d}
来源: {data.get('source', '')}
"""
            if notification_type == 'mark_failed':
                content += f"\n错误信息: {data.get('error_message', '')}\n"
                content += f"错误类型: {data.get('error_type', '')}\n"
            elif notification_type in ['mark_success', 'mark_skipped']:
                content += f"\nSubject ID: {data.get('subject_id', '')}\n"
                content += f"Episode ID: {data.get('episode_id', '')}\n"

        elif notification_type == 'bangumi_id_found':
            content += f"\nSubject ID: {data.get('subject_id', '')}\n"

        elif notification_type == 'anime_not_found':
            content += f"""
用户: {data.get('user_name', '')}
搜索标题: {data.get('title', '')}
原始标题: {data.get('ori_title', '')}
季数: {data.get('season', 0)}
来源: {data.get('source', '')}
搜索方式: {data.get('search_method', '')}
"""

        elif notification_type == 'episode_not_found':
            content += f"""
用户: {data.get('user_name', '')}
番剧: {data.get('title', '')}
季数: {data.get('season', 0)}
集数: {data.get('episode', 0)}
Subject ID: {data.get('subject_id', '')}
来源: {data.get('source', '')}
"""

        elif notification_type == 'config_error':
            content += f"""
错误信息: {data.get('error_message', '')}
配置类型: {data.get('config_type', '')}
用户名: {data.get('user_name', '')}
模式: {data.get('mode', '')}
"""

        elif notification_type in ['api_auth_error', 'api_error']:
            content += f"""
状态码: {data.get('status_code', '')}
错误信息: {data.get('error_message', '')}
"""
            if notification_type == 'api_auth_error':
                content += f"用户名: {data.get('username', '')}\n"
            elif notification_type == 'api_error':
                content += f"URL: {data.get('url', '')}\n"
                content += f"方法: {data.get('method', '')}\n"
                content += f"重试次数: {data.get('retry_count', 0)}\n"

        elif notification_type == 'api_retry_failed':
            content += f"""
Subject ID: {data.get('subject_id', '')}
Episode ID: {data.get('episode_id', '')}
最大重试次数: {data.get('max_retries', 0)}
错误信息: {data.get('error_message', '')}
"""

        elif notification_type == 'ip_locked':
            content += f"""
IP地址: {data.get('ip', '')}
锁定至: {data.get('locked_until', '')}
尝试次数: {data.get('attempt_count', 0)}
最大尝试次数: {data.get('max_attempts', 0)}
"""

        content += "\n---\n此邮件由 Bangumi-Syncer 自动发送\n"
        return content

    def _build_email_dynamic_content(self, notification_type: str, data: Dict[str, Any]) -> str:
        """
        根据通知类型构建邮件的动态HTML内容

        Args:
            notification_type: 通知类型
            data: 通知数据

        Returns:
            HTML内容字符串
        """
        content = ""

        # 根据不同的通知类型生成不同的内容
        if notification_type in ['request_received', 'bangumi_id_found', 'mark_success', 'mark_failed', 'mark_skipped']:
            # 番剧相关通知
            if notification_type == 'mark_failed':
                content += f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">❌</span> 错误详情</div>
                <div class="message">{data.get('error_message', '')}</div>
            </div>"""
            elif notification_type == 'mark_success':
                content += f"""
            <div class="info-box success">
                <div class="title"><span class="emoji">✅</span> 同步成功</div>
                <div class="message">番剧已成功标记为已看</div>
            </div>"""
            elif notification_type == 'mark_skipped':
                content += f"""
            <div class="info-box">
                <div class="title"><span class="emoji">⏭️</span> 已看过</div>
                <div class="message">该集已经标记为已看，跳过标记</div>
            </div>"""
            elif notification_type == 'bangumi_id_found':
                content += f"""
            <div class="info-box">
                <div class="title"><span class="emoji">🔍</span> 匹配到番剧</div>
                <div class="message">成功匹配到 Bangumi 番剧信息</div>
            </div>"""
            elif notification_type == 'request_received':
                content += f"""
            <div class="info-box">
                <div class="title"><span class="emoji">📥</span> 收到同步请求</div>
                <div class="message">收到来自 {data.get('source', '')} 的同步请求</div>
            </div>"""

            # 番剧信息
            content += f"""
            <div class="anime-section">
                <div class="section-title"><span class="emoji">📺</span> 番剧信息</div>
                <div class="anime-info">
                    <div><strong>标题:</strong> {data.get('title', '')}</div>
                    <div><strong>集数:</strong> 第 {data.get('season', 0)} 季 第 {data.get('episode', 0)} 集</div>
                    <div><strong>用户:</strong> {data.get('user_name', '')}</div>
                    <div><strong>来源:</strong> {data.get('source', '')}</div>"""
            if notification_type in ['mark_success', 'mark_skipped', 'bangumi_id_found']:
                content += f"""
                    <div><strong>Subject ID:</strong> {data.get('subject_id', '')}</div>"""
            if notification_type in ['mark_success', 'mark_skipped']:
                content += f"""
                    <div><strong>Episode ID:</strong> {data.get('episode_id', '')}</div>"""
            content += """
                </div>
            </div>"""

        elif notification_type == 'anime_not_found':
            content += f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">🔍</span> 未找到番剧</div>
                <div class="message">
                    未能找到匹配的番剧信息<br>
                    搜索标题: {data.get('title', '')}<br>
                    原始标题: {data.get('ori_title', '')}<br>
                    搜索方式: {data.get('search_method', '')}
                </div>
            </div>
            <div class="anime-section">
                <div class="section-title"><span class="emoji">📺</span> 番剧信息</div>
                <div class="anime-info">
                    <div><strong>标题:</strong> {data.get('title', '')}</div>
                    <div><strong>季数:</strong> 第 {data.get('season', 0)} 季</div>
                    <div><strong>用户:</strong> {data.get('user_name', '')}</div>
                    <div><strong>来源:</strong> {data.get('source', '')}</div>
                </div>
            </div>"""

        elif notification_type == 'episode_not_found':
            content += f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">🔍</span> 未找到剧集</div>
                <div class="message">
                    未能找到匹配的剧集信息<br>
                    Subject ID: {data.get('subject_id', '')}
                </div>
            </div>
            <div class="anime-section">
                <div class="section-title"><span class="emoji">📺</span> 番剧信息</div>
                <div class="anime-info">
                    <div><strong>标题:</strong> {data.get('title', '')}</div>
                    <div><strong>集数:</strong> 第 {data.get('season', 0)} 季 第 {data.get('episode', 0)} 集</div>
                    <div><strong>用户:</strong> {data.get('user_name', '')}</div>
                    <div><strong>来源:</strong> {data.get('source', '')}</div>
                </div>
            </div>"""

        elif notification_type == 'config_error':
            content += f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">⚙️</span> 配置错误</div>
                <div class="message">
                    {data.get('error_message', '')}<br>
                    配置类型: {data.get('config_type', '')}<br>
                    模式: {data.get('mode', '')}
                </div>
            </div>
            <div class="info-grid">
                <div class="info-row">
                    <div class="info-label"><span class="emoji">👤</span> 用户名</div>
                    <div class="info-value">{data.get('user_name', '')}</div>
                </div>
            </div>"""

        elif notification_type == 'api_auth_error':
            content += f"""
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
                    <div class="info-value">{data.get('username', '')}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">📊</span> 状态码</div>
                    <div class="info-value">{data.get('status_code', '')}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">💬</span> 错误信息</div>
                    <div class="info-value">{data.get('error_message', '')}</div>
                </div>
            </div>"""

        elif notification_type == 'api_error':
            content += f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">🌐</span> API错误</div>
                <div class="message">
                    Bangumi API 返回错误状态码<br>
                    {data.get('error_message', '')}
                </div>
            </div>
            <div class="info-grid">
                <div class="info-row">
                    <div class="info-label"><span class="emoji">📊</span> 状态码</div>
                    <div class="info-value">{data.get('status_code', '')}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">🔗</span> URL</div>
                    <div class="info-value">{data.get('url', '')}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">📝</span> 方法</div>
                    <div class="info-value">{data.get('method', '')}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">🔄</span> 重试次数</div>
                    <div class="info-value">{data.get('retry_count', 0)}</div>
                </div>
            </div>"""

        elif notification_type == 'api_retry_failed':
            content += f"""
            <div class="info-box error">
                <div class="title"><span class="emoji">🔄</span> API重试失败</div>
                <div class="message">
                    API请求重试多次后仍然失败<br>
                    {data.get('error_message', '')}
                </div>
            </div>
            <div class="info-grid">
                <div class="info-row">
                    <div class="info-label"><span class="emoji">📺</span> Subject ID</div>
                    <div class="info-value">{data.get('subject_id', '')}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">📼</span> Episode ID</div>
                    <div class="info-value">{data.get('episode_id', '')}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">🔄</span> 最大重试次数</div>
                    <div class="info-value">{data.get('max_retries', 0)}</div>
                </div>
            </div>"""

        elif notification_type == 'ip_locked':
            content += f"""
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
                    <div class="info-value">{data.get('ip', '')}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">⏰</span> 锁定至</div>
                    <div class="info-value">{data.get('locked_until', '')}</div>
                </div>
                <div class="info-row">
                    <div class="info-label"><span class="emoji">🔢</span> 尝试次数</div>
                    <div class="info-value">{data.get('attempt_count', 0)} / {data.get('max_attempts', 0)}</div>
                </div>
            </div>"""

        return content

    def _build_payload_by_type(
        self,
        notification_type: str,
        data: Dict[str, Any],
        template: str
    ) -> Dict[str, Any]:
        """
        根据通知类型构建载荷

        Args:
            notification_type: 通知类型
            data: 原始数据
            template: 自定义模板
        """
        # 添加通知类型到数据中
        data['notification_type'] = notification_type

        # 如果有自定义模板，使用模板
        if template:
            try:
                import json
                template_obj = json.loads(template)
                return self._replace_template_variables(template_obj, data)
            except Exception as e:
                logger.warning(f"自定义模板解析失败: {e}，使用默认格式")

        # 根据通知类型使用不同的默认格式
        default_templates = {
            'request_received': {
                'title': '📥 收到同步请求',
                'type': notification_type,
                'timestamp': data.get('timestamp', ''),
                'user': data.get('user_name', ''),
                'anime': data.get('title', ''),
                'episode': f"S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
                'source': data.get('source', '')
            },
            'bangumi_id_found': {
                'title': '🔍 匹配到Bangumi番剧',
                'type': notification_type,
                'timestamp': data.get('timestamp', ''),
                'user': data.get('user_name', ''),
                'anime': data.get('title', ''),
                'episode': f"S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
                'source': data.get('source', ''),
                'subject_id': data.get('subject_id', '')
            },
            'mark_success': {
                'title': '✅ 同步成功',
                'type': notification_type,
                'timestamp': data.get('timestamp', ''),
                'user': data.get('user_name', ''),
                'anime': data.get('title', ''),
                'episode': f"S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
                'source': data.get('source', ''),
                'subject_id': data.get('subject_id', ''),
                'episode_id': data.get('episode_id', '')
            },
            'mark_failed': {
                'title': '❌ 同步失败',
                'type': notification_type,
                'timestamp': data.get('timestamp', ''),
                'user': data.get('user_name', ''),
                'anime': data.get('title', ''),
                'episode': f"S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
                'source': data.get('source', ''),
                'error': data.get('error_message', ''),
                'error_type': data.get('error_type', '')
            },
            'mark_skipped': {
                'title': '⏭️ 已看过，跳过',
                'type': notification_type,
                'timestamp': data.get('timestamp', ''),
                'user': data.get('user_name', ''),
                'anime': data.get('title', ''),
                'episode': f"S{data.get('season', 0):02d}E{data.get('episode', 0):02d}",
                'source': data.get('source', ''),
                'subject_id': data.get('subject_id', ''),
                'episode_id': data.get('episode_id', '')
            },
            'config_error': {
                'title': '⚠️ 配置错误',
                'type': notification_type,
                'timestamp': data.get('timestamp', ''),
                'error_message': data.get('error_message', ''),
                'config_type': data.get('config_type', ''),
                'user_name': data.get('user_name', ''),
                'mode': data.get('mode', '')
            },
            'anime_not_found': {
                'title': '🔍 未找到番剧',
                'type': notification_type,
                'timestamp': data.get('timestamp', ''),
                'user': data.get('user_name', ''),
                'title': data.get('title', ''),
                'ori_title': data.get('ori_title', ''),
                'season': data.get('season', 0),
                'source': data.get('source', ''),
                'search_method': data.get('search_method', '')
            },
            'episode_not_found': {
                'title': '📺 未找到剧集',
                'type': notification_type,
                'timestamp': data.get('timestamp', ''),
                'user': data.get('user_name', ''),
                'title': data.get('title', ''),
                'season': data.get('season', 0),
                'episode': data.get('episode', 0),
                'subject_id': data.get('subject_id', ''),
                'source': data.get('source', '')
            },
            'api_auth_error': {
                'title': '🔑 API认证失败',
                'type': notification_type,
                'timestamp': data.get('timestamp', ''),
                'username': data.get('username', ''),
                'status_code': data.get('status_code', 0),
                'error_message': data.get('error_message', '')
            },
            'api_error': {
                'title': '🌐 API错误',
                'type': notification_type,
                'timestamp': data.get('timestamp', ''),
                'status_code': data.get('status_code', 0),
                'url': data.get('url', ''),
                'method': data.get('method', ''),
                'error_message': data.get('error_message', ''),
                'retry_count': data.get('retry_count', 0)
            },
            'api_retry_failed': {
                'title': '🔄 API重试失败',
                'type': notification_type,
                'timestamp': data.get('timestamp', ''),
                'subject_id': data.get('subject_id', ''),
                'episode_id': data.get('episode_id', ''),
                'max_retries': data.get('max_retries', 0),
                'error_message': data.get('error_message', '')
            },
            'ip_locked': {
                'title': '🔒 IP被锁定',
                'type': notification_type,
                'timestamp': data.get('timestamp', ''),
                'ip': data.get('ip', ''),
                'locked_until': data.get('locked_until', ''),
                'attempt_count': data.get('attempt_count', 0),
                'max_attempts': data.get('max_attempts', 0)
            }
        }

        return default_templates.get(notification_type, {
            'title': f'📢 {notification_type}',
            'type': notification_type,
            'timestamp': data.get('timestamp', ''),
            'data': data
        })

    def _parse_headers(self, headers_str: str) -> Dict[str, str]:
        """解析请求头字符串"""
        headers = {'User-Agent': 'Bangumi-Syncer-Notifier'}

        # 确保headers_str是字符串类型
        if not headers_str:
            return headers

        # 如果headers_str不是字符串，转换为字符串
        if not isinstance(headers_str, str):
            headers_str = str(headers_str)

        try:
            # 尝试解析为JSON
            import json
            parsed = json.loads(headers_str)
            if isinstance(parsed, dict):
                headers.update(parsed)
        except:
            # 如果不是JSON，尝试解析为逗号分隔的键值对
            try:
                for header in headers_str.split(','):
                    if ':' in header:
                        key, value = header.split(':', 1)
                        headers[key.strip()] = value.strip()
            except Exception as e:
                logger.warning(f"解析headers失败: {e}")

        return headers

    def test_notification(self, notification_type: Optional[str] = None,
                     webhook_id: Optional[int] = None,
                     email_id: Optional[int] = None) -> Dict[str, Any]:
        """
        测试通知功能

        Args:
            notification_type: 通知类型，可选值: 'webhook', 'email', 'all'
            webhook_id: 指定测试的webhook ID（可选）
            email_id: 指定测试的email ID（可选）

        Returns:
            测试结果字典
        """
        results = {
            'webhook': {'enabled': False, 'success': False, 'message': '', 'webhooks': []},
            'email': {'enabled': False, 'success': False, 'message': '', 'emails': []}
        }

        test_data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'user_name': 'TestUser',
            'title': '测试番剧',
            'season': 1,
            'episode': 1,
            'source': 'test',
            'error_message': '这是一条测试通知'
        }

        # 测试webhook
        if notification_type in (None, 'webhook', 'all'):
            webhook_configs = self._get_webhook_configs()

            if webhook_id:
                # 测试指定的webhook
                webhook_configs = [w for w in webhook_configs if w.get('id') == webhook_id]

            for webhook_config in webhook_configs:
                webhook_result = {
                    'id': webhook_config.get('id'),
                    'url': webhook_config.get('url', ''),
                    'enabled': webhook_config.get('enabled', False),
                    'success': False,
                    'message': ''
                }

                if webhook_config.get('enabled', False):
                    webhook_result['enabled'] = True
                    try:
                        success = self._send_webhook_by_config(
                            webhook_config,
                            'mark_success',  # 测试使用成功通知类型
                            test_data
                        )
                        if success:
                            webhook_result['success'] = True
                            webhook_result['message'] = f'Webhook {webhook_config["id"]} 测试成功'
                        else:
                            webhook_result['message'] = f'Webhook {webhook_config["id"]} 测试失败'
                    except Exception as e:
                        webhook_result['message'] = f'Webhook {webhook_config["id"]} 测试失败: {str(e)}'
                else:
                    webhook_result['message'] = f'Webhook {webhook_config["id"]} 未启用'

                results['webhook']['webhooks'].append(webhook_result)

            results['webhook']['enabled'] = len(webhook_configs) > 0
            results['webhook']['message'] = f'测试了 {len(webhook_configs)} 个webhook'

        # 测试邮件
        if notification_type in (None, 'email', 'all'):
            email_configs = self._get_email_configs()

            if email_id:
                # 测试指定的email
                email_configs = [e for e in email_configs if e.get('id') == email_id]

            for email_config in email_configs:
                email_result = {
                    'id': email_config.get('id'),
                    'email_to': email_config.get('email_to', ''),
                    'enabled': email_config.get('enabled', False),
                    'success': False,
                    'message': ''
                }

                if email_config.get('enabled', False):
                    email_result['enabled'] = True
                    try:
                        success = self._send_email_by_config(
                            email_config,
                            'mark_success',  # 测试使用成功通知类型
                            test_data
                        )
                        if success:
                            email_result['success'] = True
                            email_result['message'] = f'邮件配置 {email_config["id"]} 测试成功'
                        else:
                            email_result['message'] = f'邮件配置 {email_config["id"]} 测试失败'
                    except Exception as e:
                        email_result['message'] = f'邮件配置 {email_config["id"]} 测试失败: {str(e)}'
                else:
                    email_result['message'] = f'邮件配置 {email_config["id"]} 未启用'

                results['email']['emails'].append(email_result)

            results['email']['enabled'] = len(email_configs) > 0
            results['email']['message'] = f'测试了 {len(email_configs)} 个邮件配置'

        return results


# 全局通知器实例（延迟初始化）
_notifier_instance: Optional[Notifier] = None


def get_notifier():
    """获取通知器实例"""
    global _notifier_instance
    if _notifier_instance is None:
        from ..core.config import config_manager
        _notifier_instance = Notifier(config_manager)
    return _notifier_instance