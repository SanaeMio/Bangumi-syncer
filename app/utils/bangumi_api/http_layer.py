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

# 上游网关以 2xx 返回错误页（HTML 等非 JSON）时，其语义等价于 502 Bad Gateway。
# 据此构造等价状态码，让既有的服务端不可用降级链路（5xx 标记不可达并入队待补发）
# 正常接管，避免错误页被当作成功。
_UPSTREAM_GATEWAY_ERROR_STATUS = 502


class HttpLayerMixin:
    """HTTP 请求层相关方法（供 BangumiApi 组合）"""

    def _try_direct_connection(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response | None:
        """尝试直连（不使用代理）"""
        logger.debug(f"🔄 尝试直连: {url}")

        # 创建一个临时的 SyncHttpClient，不使用代理
        temp_session = (
            SyncHttpClient(
                label="Bangumi-直连",
                verify=self.ssl_verify,
                ech=getattr(self, "ech_mode", "off"),
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

        logger.debug(f"🔍 开始网络诊断 - 目标: {hostname}:{port}")

        # 1. DNS解析测试
        try:
            ip_list = socket.getaddrinfo(
                hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
            ips = [ip[4][0] for ip in ip_list]
            logger.debug(f"✅ DNS解析成功: {hostname} -> {', '.join(set(ips))}")
        except socket.gaierror as e:
            logger.error(f"❌ DNS解析失败: {e}")
            logger.debug("💡 建议检查:")
            logger.debug("   1. 网络连接是否正常")
            logger.debug("   2. DNS设置是否正确 (可尝试8.8.8.8或114.114.114.114)")
            logger.debug("   3. 是否需要配置代理")
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
                logger.debug(f"✅ TCP连接成功: {ips[0]}:{port}")
            else:
                logger.error(f"❌ TCP连接失败: {ips[0]}:{port} (错误码: {result})")
                logger.debug("💡 建议检查:")
                logger.debug("   1. 防火墙设置")
                logger.debug("   2. 网络代理配置")
                logger.debug("   3. 是否需要VPN或其他网络工具")
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
        - 重试耗尽后的状态码通知 + API 不可达标记
        """
        kwargs.setdefault("timeout", 15)
        # httpx.Client 在构造时已设置 verify 和 proxy，移除 per-request 残留
        kwargs.pop("verify", None)
        kwargs.pop("proxies", None)

        # 如果之前代理已经失败过，直接使用直连
        if self.http_proxy and self._proxy_failed:
            logger.debug("💡 检测到代理之前已失败，本次请求直接使用直连")
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
                        logger.debug("✅ 直连成功！已成功绕过代理问题")
                        return direct_result
                except (httpx.HTTPError, ValueError) as direct_error:
                    logger.error(f"❌ 直连也失败了: {str(direct_error)}")

            # 如果是DNS错误，进行网络诊断
            if dns_error:
                logger.warning("⚠️  检测到DNS解析问题，开始网络诊断...")
                self._diagnose_network_issue(url)

            # 标记 API 不可达，TTL 内后续请求直接走降级：
            # - 写降级（入 pending_sync_queue 待补发）由 [bangumi-replay] enabled 控制
            # - 读降级（走 archive 本地数据集短路）由 [bangumi-archive] enabled 控制
            # 标记本身始终生效，与 archive 是否启用无关
            self.mark_api_unreachable()

            raise e

        # 重试耗尽后仍返回重试状态码（429/500/502/503/504）
        if res.status_code in RETRY_STATUS_CODES:
            from ...services.notification_service import notification_service

            # 重试耗尽触发 api_retry_failed，便于用户精确订阅"重试失败"事件
            notification_service.notify(
                "api_retry_failed",
                status_code=res.status_code,
                url=url,
                method=method,
                error_message=f"HTTP {res.status_code} 重试 {session._max_retries} 次后仍失败",
                retry_count=session._max_retries,
            )
            # 服务端 5xx/429 持续不可用，同样标记不可达
            self.mark_api_unreachable()

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
            from ...services.notification_service import notification_service

            notification_service.notify(
                "api_auth_error",
                user_name=self.username,
                status_code=res.status_code,
                error_message=error_msg,
            )
            # 同时触发 token 过期事件，便于用户精细订阅该场景
            notification_service.notify(
                "bangumi_token_expired",
                user_name=self.username,
                status_code=res.status_code,
                error_message=error_msg,
            )

            raise ValueError(error_msg)
        return res

    def _check_upstream_error_page(self, res: httpx.Response) -> httpx.Response:
        """识别上游网关以 2xx 返回错误页（响应体非 JSON）的情形

        Bangumi API 各端点均返回 JSON。上游网关故障时会以 HTTP 200 返回错误页
        （如 ``502 Bad Gateway`` 的 HTML），此时状态码不属于重试状态码，
        若只按状态码判定会被当作成功——收藏查询拿不到数据、标记请求「成功」，
        最终同步记录却是成功，实际并未标记。故对带响应体的 2xx 响应校验内容
        类型，非 JSON 即视为网关故障：标记 API 不可达，并以等价的 502 状态码
        抛出，交由既有的服务端不可用降级链路处理。
        """
        if res.status_code >= 400:
            return res
        # 无响应体的成功状态（204/205）无需校验
        if res.status_code in (204, 205):
            return res

        content_type = res.headers.get("content-type")
        # 内容类型缺失或无法判定时跳过校验，避免误判合法响应
        if not isinstance(content_type, str) or not content_type:
            return res
        value = content_type.lower()
        if "json" in value:
            return res
        # 仅对明确声明为文本类型（错误页以 text/html 居多）判定为网关故障
        if not value.startswith("text/"):
            return res
        if not res.content:
            return res

        logger.error(
            f"上游网关返回错误页: HTTP {res.status_code} 但响应体非 JSON "
            f"(content-type={content_type or '未知'})，按网关故障处理"
        )
        self.mark_api_unreachable()
        raise httpx.HTTPStatusError(
            f"上游网关返回错误页（HTTP {res.status_code}，响应体非 JSON）",
            request=res.request,
            response=httpx.Response(
                _UPSTREAM_GATEWAY_ERROR_STATUS,
                request=res.request,
                headers=res.headers,
                content=res.content,
            ),
        )

    def _validate_response(self, res: httpx.Response) -> httpx.Response:
        """统一响应校验：先认证错误，再识别上游网关错误页"""
        return self._check_upstream_error_page(self._check_auth_error(res))
