"""BangumiApi HTTP 层：直连/诊断/状态码通知（mixin）

重试逻辑由 SyncHttpClient 内置 max_retries 统一处理，
本模块仅负责代理失败后直连回退、DNS 诊断和重试耗尽通知。
"""

from __future__ import annotations

import socket
from typing import Any

import httpx

from ...core.logging import logger
from ..http_base import SyncHttpClient
from ..retry import RETRY_EXCEPTIONS, RETRY_STATUS_CODES


class HttpLayerMixin:
    """HTTP 请求层相关方法（供 BangumiApi 组合）"""

    def _try_direct_connection(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response | None:
        """尝试直连（不使用代理）"""
        logger.info(f"🔄 尝试直连: {url}")

        # 创建一个临时的 SyncHttpClient，不使用代理
        temp_session = (
            SyncHttpClient(
                label="Bangumi-直连",
                verify=self.ssl_verify,
                max_retries=0,
            )
            .prefix("📚")
            .success_tpl("直连请求成功")
            .failure_tpl("直连请求失败")
        )
        temp_session.client.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "SanaeMio/Bangumi-syncer (https://github.com/SanaeMio/Bangumi-syncer)",
            }
        )

        if self.access_token:
            temp_session.client.headers.update(
                {"Authorization": f"Bearer {self.access_token}"}
            )

        # 移除kwargs中可能存在的代理设置（httpx 通过构造函数传代理）
        kwargs_copy = kwargs.copy()
        if "proxies" in kwargs_copy:
            del kwargs_copy["proxies"]

        # 设置较短的超时时间，避免直连等待过久
        if "timeout" not in kwargs_copy:
            kwargs_copy["timeout"] = 15

        try:
            res = temp_session.request(method, url, **kwargs_copy)

            # 检查响应状态
            if res.status_code < 400:
                return res
            else:
                logger.warning(f"⚠️  直连请求返回错误状态码: {res.status_code}")
                return None

        except Exception as e:
            logger.error(f"直连请求失败: {str(e)}")
            raise e
        finally:
            temp_session.close()

    def _diagnose_network_issue(self, url: str) -> None:
        """诊断网络连接问题"""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        logger.info(f"🔍 开始网络诊断 - 目标: {hostname}:{port}")

        # 1. DNS解析测试
        try:
            ip_list = socket.getaddrinfo(
                hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
            ips = [ip[4][0] for ip in ip_list]
            logger.info(f"✅ DNS解析成功: {hostname} -> {', '.join(set(ips))}")
        except socket.gaierror as e:
            logger.error(f"❌ DNS解析失败: {e}")
            logger.info("💡 建议检查:")
            logger.info("   1. 网络连接是否正常")
            logger.info("   2. DNS设置是否正确 (可尝试8.8.8.8或114.114.114.114)")
            logger.info("   3. 是否需要配置代理")
            return
        except Exception as e:
            logger.error(f"❌ DNS解析异常: {e}")
            return

        # 2. TCP连接测试
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            result = sock.connect_ex((ips[0], port))
            sock.close()

            if result == 0:
                logger.info(f"✅ TCP连接成功: {ips[0]}:{port}")
            else:
                logger.error(f"❌ TCP连接失败: {ips[0]}:{port} (错误码: {result})")
                logger.info("💡 建议检查:")
                logger.info("   1. 防火墙设置")
                logger.info("   2. 网络代理配置")
                logger.info("   3. 是否需要VPN或其他网络工具")
        except OSError as e:
            logger.error(f"❌ TCP连接测试异常: {e}")

    def _request_with_retry(
        self,
        method: str,
        session: SyncHttpClient,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """请求方法（重试由 SyncHttpClient 内置 max_retries 处理）

        本方法仅负责：
        - 代理失败后直连回退
        - DNS 错误网络诊断
        - 重试耗尽后的状态码通知
        """
        kwargs.setdefault("timeout", 15)
        # httpx.Client 在构造时已设置 verify 和 proxy，移除 per-request 残留
        kwargs.pop("verify", None)
        kwargs.pop("proxies", None)

        # 如果之前代理已经失败过，直接使用直连
        if self.http_proxy and self._proxy_failed:
            logger.info("💡 检测到代理之前已失败，本次请求直接使用直连")
            return self._try_direct_connection(method, url, **kwargs)

        try:
            res = session.request(method, url, **kwargs)
        except RETRY_EXCEPTIONS as e:
            # SyncHttpClient 重试耗尽后仍抛出异常
            dns_error = "Failed to resolve" in str(
                e
            ) or "Temporary failure in name resolution" in str(e)

            # 如果配置了代理且重试失败，尝试直连
            if self.http_proxy:
                logger.warning("⚠️  代理请求失败，尝试抛弃代理直连...")
                try:
                    direct_result = self._try_direct_connection(method, url, **kwargs)
                    if direct_result:
                        self._proxy_failed = True
                        logger.info("✅ 直连成功！已成功绕过代理问题")
                        return direct_result
                except (httpx.HTTPError, ValueError) as direct_error:
                    logger.error(f"❌ 直连也失败了: {str(direct_error)}")

            # 如果是DNS错误，进行网络诊断
            if dns_error:
                logger.warning("⚠️  检测到DNS解析问题，开始网络诊断...")
                self._diagnose_network_issue(url)

            raise e

        # 重试耗尽后仍返回重试状态码（429/500/502/503/504）
        if res.status_code in RETRY_STATUS_CODES:
            from ..notifier import send_notify

            send_notify(
                "api_error",
                status_code=res.status_code,
                url=url,
                method=method,
                error_message=f"HTTP {res.status_code} 错误，已达到最大重试次数",
                retry_count=session._max_retries,
            )
            raise httpx.HTTPStatusError(
                f"HTTP {res.status_code} 错误，已达到最大重试次数",
                request=res.request,
                response=res,
            )

        return res

    def _check_auth_error(self, res: httpx.Response) -> httpx.Response:
        """统一检查认证错误"""
        if res.status_code == 401:
            error_msg = "Bangumi API 认证失败: access_token可能已过期（有效期1年）或无效，请更新token"
            logger.error(error_msg)

            # 发送API认证失败通知（webhook和邮件）
            from ..notifier import send_notify

            send_notify(
                "api_auth_error",
                user_name=self.username,
                status_code=res.status_code,
                error_message=error_msg,
            )

            raise ValueError(error_msg)
        return res
