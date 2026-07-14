"""BangumiApi HTTP 层：直连/重试/诊断（mixin）"""

import socket
import time

import httpx

from ...core.logging import logger
from ..http_client import create_sync_client
from ..retry import RETRY_STATUS_CODES, compute_backoff_delay


class HttpLayerMixin:
    """HTTP 请求层相关方法（供 BangumiApi 组合）"""

    def _try_direct_connection(self, method, url, **kwargs):
        """尝试直连（不使用代理）"""
        logger.info(f"🔄 尝试直连: {url}")

        # 创建一个临时的 httpx.Client，不使用代理
        temp_session = create_sync_client(verify=self.ssl_verify)
        temp_session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "SanaeMio/Bangumi-syncer (https://github.com/SanaeMio/Bangumi-syncer)",
            }
        )

        if self.access_token:
            temp_session.headers.update(
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
            if method.upper() == "GET":
                res = temp_session.get(url, **kwargs_copy)
            elif method.upper() == "POST":
                res = temp_session.post(url, **kwargs_copy)
            elif method.upper() == "PUT":
                res = temp_session.put(url, **kwargs_copy)
            elif method.upper() == "PATCH":
                res = temp_session.patch(url, **kwargs_copy)
            else:
                raise ValueError(f"不支持的HTTP方法: {method}")

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

    def _diagnose_network_issue(self, url):
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
        except Exception as e:
            logger.error(f"❌ TCP连接测试异常: {e}")

    def _request_with_retry(self, method, session, url, max_retries=3, **kwargs):
        """带重试机制的请求方法（支持代理失败后直连重试）"""
        kwargs.setdefault("timeout", 15)
        dns_error_occurred = False

        # 如果之前代理已经失败过，直接使用直连
        if self.http_proxy and self._proxy_failed:
            logger.info("💡 检测到代理之前已失败，本次请求直接使用直连")
            try:
                return self._try_direct_connection(method, url, **kwargs)
            except Exception as e:
                logger.error(f"直连请求失败: {str(e)}")
                raise e

        for attempt in range(max_retries + 1):
            try:
                # httpx.Client 在构造时已设置 verify 和 proxies，
                # 需从 kwargs 中移除这些参数（requests 风格的逐请求传参）
                kwargs.pop("verify", None)
                kwargs.pop("proxies", None)

                if method.upper() == "GET":
                    res = session.get(url, **kwargs)
                elif method.upper() == "POST":
                    res = session.post(url, **kwargs)
                elif method.upper() == "PUT":
                    res = session.put(url, **kwargs)
                elif method.upper() == "PATCH":
                    res = session.patch(url, **kwargs)
                else:
                    raise ValueError(f"不支持的HTTP方法: {method}")

                # 检查是否需要重试的状态码
                if res.status_code in RETRY_STATUS_CODES:
                    if attempt < max_retries:
                        delay = compute_backoff_delay(attempt)
                        logger.error(
                            f"HTTP {res.status_code} 错误，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(
                            f"HTTP {res.status_code} 错误，已达到最大重试次数 {max_retries}"
                        )
                        # 发送API错误通知
                        from ..notifier import send_notify

                        send_notify(
                            "api_error",
                            status_code=res.status_code,
                            url=url,
                            method=method,
                            error_message=f"HTTP {res.status_code} 错误，已达到最大重试次数 {max_retries}",
                            retry_count=attempt + 1,
                        )
                        raise httpx.HTTPStatusError(
                            f"HTTP {res.status_code} 错误，已达到最大重试次数",
                            request=res.request,
                            response=res,
                        )

                return res

            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.HTTPError,
            ) as e:
                # 检查是否是DNS解析错误
                if "Failed to resolve" in str(
                    e
                ) or "Temporary failure in name resolution" in str(e):
                    dns_error_occurred = True

                if attempt < max_retries:
                    delay = compute_backoff_delay(attempt)
                    logger.error(
                        f"请求异常: {str(e)}，第 {attempt + 1}/{max_retries} 次重试，{delay}秒后重试"
                    )
                    time.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"请求异常: {str(e)}，已达到最大重试次数 {max_retries}"
                    )

                    # 如果配置了代理且重试失败，尝试直连
                    if self.http_proxy:
                        logger.warning("⚠️  代理请求失败，尝试抛弃代理直连...")

                        # 尝试直连（不使用代理）
                        try:
                            direct_result = self._try_direct_connection(
                                method, url, **kwargs
                            )
                            if direct_result:
                                # 标记代理已失败，后续请求直接使用直连
                                self._proxy_failed = True
                                logger.info("✅ 直连成功！已成功绕过代理问题")
                                return direct_result
                        except Exception as direct_error:
                            logger.error(f"❌ 直连也失败了: {str(direct_error)}")

                    # 如果是DNS错误，进行网络诊断
                    if dns_error_occurred:
                        logger.warning("⚠️  检测到DNS解析问题，开始网络诊断...")
                        self._diagnose_network_issue(url)

                    raise e

    def _check_auth_error(self, res):
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
