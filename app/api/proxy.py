"""
代理配置API
"""

import platform
import socket
import subprocess
import time
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..core.config import config_manager
from ..core.logging import logger
from ..utils.docker_helper import docker_helper
from ..utils.http_base import SyncHttpClient
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api")


class ProxyTestRequest(BaseModel):
    proxy_url: str


@router.get("/proxy/suggestions")
async def get_proxy_suggestions(
    request: Request,
    port: Optional[int] = 7890,
    current_user: dict = Depends(get_current_user_flexible),
) -> dict[str, Any]:
    """获取代理配置建议"""
    try:
        # 获取环境信息
        env_info = docker_helper.get_environment_info()

        # 获取代理建议
        suggestions = docker_helper.get_proxy_suggestions(port)

        return {
            "status": "success",
            "data": {"environment": env_info, "suggestions": suggestions},
        }
    except Exception as e:
        logger.error(f"获取代理建议失败: {e}")
        return {"status": "error", "message": f"获取代理建议失败: {str(e)}"}


@router.post("/proxy/test")
async def test_proxy_connectivity(
    request: ProxyTestRequest, current_user: dict = Depends(get_current_user_flexible)
):
    """测试代理连通性"""
    try:
        # 测试代理连通性
        result = docker_helper.test_proxy_connectivity(request.proxy_url)

        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"代理测试失败: {e}")
        return {"status": "error", "message": f"代理测试失败: {str(e)}"}


@router.get("/proxy/environment")
async def get_environment_info(
    request: Request, current_user: dict = Depends(get_current_user_flexible)
):
    """获取环境信息"""
    try:
        env_info = docker_helper.get_environment_info()

        return {"status": "success", "data": env_info}
    except Exception as e:
        logger.error(f"获取环境信息失败: {e}")
        return {"status": "error", "message": f"获取环境信息失败: {str(e)}"}


class HostConnectivityRequest(BaseModel):
    host: str
    port: int = 80
    timeout: int = 5


class NetworkDiagnosisRequest(BaseModel):
    url: str


def get_system_dns_servers() -> list[str]:
    """获取系统DNS服务器配置"""
    is_docker = _detect_docker_environment()
    system = platform.system().lower()

    try:
        if system == "windows":
            dns_servers = _get_windows_dns_servers()
        elif system == "linux":
            dns_servers = _get_linux_dns_servers(is_docker)
        else:
            dns_servers = []

        if not dns_servers:
            dns_servers = _get_fallback_dns_servers()
    except Exception as e:
        logger.warning(f"获取DNS服务器配置失败: {e}")
        dns_servers = []

    if not dns_servers:
        dns_servers.append("无法获取DNS服务器信息")

    return dns_servers


def _detect_docker_environment() -> bool:
    """检测是否在 Docker 容器中运行"""
    try:
        with open("/proc/1/cgroup") as f:
            content = f.read()
            if "docker" in content or "containerd" in content:
                return True
    except FileNotFoundError:
        try:
            import os

            if os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER"):
                return True
        except Exception:
            pass
    except Exception:
        pass
    return False


def _get_windows_dns_servers() -> list[str]:
    """Windows 系统：通过 ipconfig /all 或 nslookup 获取 DNS"""
    dns_servers: list[str] = []
    try:
        result = subprocess.run(
            ["ipconfig", "/all"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="gbk",  # Windows中文系统使用gbk编码
        )
        output = result.stdout
        lines = output.split("\n")
        for i, line in enumerate(lines):
            if "DNS" in line and "服务器" in line:  # 中文系统
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if next_line and ":" in next_line:
                        dns_server = next_line.split(":")[-1].strip()
                        if dns_server and "." in dns_server:
                            dns_servers.append(dns_server)
            elif "DNS Servers" in line:  # 英文系统
                dns_server = line.split(":")[-1].strip()
                if dns_server and "." in dns_server:
                    dns_servers.append(dns_server)
    except UnicodeDecodeError:
        try:
            result = subprocess.run(
                ["ipconfig", "/all"],
                capture_output=True,
                text=True,
                timeout=10,
                encoding="utf-8",
            )
        except (OSError, subprocess.SubprocessError):
            pass

    # 如果ipconfig没有获取到，尝试使用nslookup作为备用方案
    if not dns_servers:
        try:
            result = subprocess.run(
                ["nslookup", "www.baidu.com"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            output = result.stdout
            for line in output.split("\n"):
                if "Server:" in line:
                    dns_server = line.split("Server:")[-1].strip()
                    if dns_server and dns_server != "localhost" and "." in dns_server:
                        dns_servers.append(dns_server)
        except (OSError, subprocess.SubprocessError):
            pass

    return dns_servers


def _get_linux_dns_servers(is_docker: bool) -> list[str]:
    """Linux 系统：读取 /etc/resolv.conf 或 systemd-resolve 获取 DNS"""
    dns_servers: list[str] = []
    try:
        with open("/etc/resolv.conf") as f:
            resolv_content = f.read()
            for line in resolv_content.split("\n"):
                line = line.strip()
                if line.startswith("nameserver"):
                    dns_server = line.split()[1]
                    if dns_server:
                        dns_servers.append(dns_server)

        if is_docker:
            dns_servers.append("(Docker容器)")
            try:
                with open("/etc/resolv.conf") as f:
                    content = f.read()
                    if "# Generated by Docker" in content or "127.0.0.11" in content:
                        dns_servers.append("Docker内置DNS: 127.0.0.11")
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                result = subprocess.run(
                    ["cat", "/etc/resolv.conf"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if "127.0.0.11" in result.stdout:
                    dns_servers.append("注意: Docker使用内置DNS转发")
            except (OSError, subprocess.SubprocessError):
                pass
    except FileNotFoundError:
        pass

    # 尝试使用systemd-resolve（非Docker环境）
    if not is_docker:
        try:
            result = subprocess.run(
                ["systemd-resolve", "--status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.split("\n"):
                if "DNS Servers:" in line:
                    dns_server = line.split("DNS Servers:")[-1].strip()
                    if dns_server and dns_server not in dns_servers:
                        dns_servers.append(dns_server)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return dns_servers


def _get_fallback_dns_servers() -> list[str]:
    """通过 socket 解析域名来检测 DNS 是否可用"""
    try:
        socket.getaddrinfo("google.com", 80)
        return ["系统默认DNS（无法直接获取地址）"]
    except (OSError, subprocess.SubprocessError):
        return []


@router.post("/proxy/test-host")
async def test_host_connectivity(
    request: HostConnectivityRequest,
    current_user: dict = Depends(get_current_user_flexible),
):
    """测试到指定主机的连通性"""
    try:
        result = docker_helper.test_host_connectivity(
            request.host, request.port, request.timeout
        )
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"主机连通性测试失败: {e}")
        return {"status": "error", "message": f"主机连通性测试失败: {str(e)}"}


@router.post("/network/diagnose")
async def diagnose_network(
    request: NetworkDiagnosisRequest,
    current_user: dict = Depends(get_current_user_flexible),
):
    """网络连接诊断"""
    try:
        target_url = request.url
        bgm_api_proxy = config_manager.get("dev", "bgm_api_proxy", fallback="")
        if bgm_api_proxy and "api.bgm.tv" in target_url:
            target_url = bgm_api_proxy.rstrip("/")

        parsed = urlparse(target_url)
        hostname = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        env_info = docker_helper.get_environment_info()

        proxy_config = config_manager.get("dev", "script_proxy", fallback="")
        proxies = None
        if proxy_config:
            proxies = {"http": proxy_config, "https": proxy_config}

        result = {
            "url": target_url,
            "hostname": hostname,
            "port": port,
            "dns_servers": get_system_dns_servers(),
            "proxy_config": proxy_config or "未配置代理",
            "bgm_api_proxy": bgm_api_proxy or None,
            "environment": {
                "is_docker": env_info.get("is_docker", False),
                "network_mode": env_info.get("network_mode", "unknown")
                if env_info.get("is_docker")
                else "native",
            },
            "diagnosis": [],
        }

        # DNS解析测试
        ips = _diagnose_dns_resolution(hostname, port, result)
        if not ips:
            return {"status": "success", "data": result}

        # TCP连接测试（直连）
        _diagnose_tcp_connection(ips, port, result)

        # HTTP连接测试（使用配置的代理）
        _diagnose_http_connection(parsed, hostname, port, proxies, proxy_config, result)

        return {"status": "success", "data": result}

    except Exception as e:
        logger.error(f"网络诊断失败: {str(e)}")
        return {"status": "error", "message": f"网络诊断失败: {str(e)}"}


def _diagnose_dns_resolution(hostname: str, port: int, result: dict) -> list[str]:
    """DNS解析测试，返回 IP 列表（空列表表示失败）"""
    try:
        ip_list = socket.getaddrinfo(
            hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
        ips = [ip[4][0] for ip in ip_list]
        result["diagnosis"].append(
            {
                "test": "DNS解析",
                "status": "success",
                "message": f"{hostname} -> {', '.join(set(ips))}",
                "ips": list(set(ips)),
            }
        )
        return ips
    except socket.gaierror as e:
        result["diagnosis"].append(
            {
                "test": "DNS解析",
                "status": "failed",
                "message": f"DNS解析失败: {e}",
                "suggestions": [
                    "检查网络连接是否正常",
                    "尝试更换DNS服务器 (8.8.8.8或114.114.114.114)",
                    "检查是否需要配置代理",
                ],
            }
        )
        return []
    except Exception as e:
        result["diagnosis"].append(
            {"test": "DNS解析", "status": "error", "message": f"DNS解析异常: {e}"}
        )
        return []


def _diagnose_tcp_connection(ips: list[str], port: int, result: dict) -> None:
    """TCP直连测试"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        conn_result = sock.connect_ex((ips[0], port))
        sock.close()

        if conn_result == 0:
            result["diagnosis"].append(
                {
                    "test": "TCP直连",
                    "status": "success",
                    "message": f"直连成功: {ips[0]}:{port}",
                }
            )
        else:
            result["diagnosis"].append(
                {
                    "test": "TCP直连",
                    "status": "failed",
                    "message": f"直连失败: {ips[0]}:{port} (错误码: {conn_result})",
                    "suggestions": [
                        "检查防火墙设置",
                        "检查网络代理配置",
                        "可能需要VPN或其他网络工具",
                    ],
                }
            )
    except Exception as e:
        result["diagnosis"].append(
            {
                "test": "TCP直连",
                "status": "error",
                "message": f"TCP直连测试异常: {e}",
            }
        )


def _diagnose_http_connection(
    parsed, hostname: str, port: int, proxies, proxy_config: str, result: dict
) -> None:
    """HTTP连接测试（使用配置的代理）"""
    ssl_verify_raw = config_manager.get("dev", "ssl_verify", fallback=True)
    ssl_verify = (
        ssl_verify_raw
        if isinstance(ssl_verify_raw, bool)
        else str(ssl_verify_raw).strip().lower() not in ("false", "0", "no", "off", "")
    )
    try:
        test_url = f"{parsed.scheme}://{hostname}:{port}"
        start_time = time.time()

        proxy_url = proxies.get("https") or proxies.get("http") if proxies else None
        with SyncHttpClient(
            label="Diag",
            proxy=proxy_url,
            verify=ssl_verify,
            timeout=10.0,
            follow_redirects=False,
            max_retries=0,
        ) as client:
            response = client.get(test_url)

        end_time = time.time()
        response_time = int((end_time - start_time) * 1000)  # 毫秒

        result["diagnosis"].append(
            {
                "test": "HTTP连接",
                "status": "success",
                "message": f"HTTP连接成功 (状态码: {response.status_code}, 耗时: {response_time}ms)",
                "proxy_used": proxy_config if proxies else "无代理",
                "ssl_verify": ssl_verify,
            }
        )
    except httpx.HTTPError as e:
        result["diagnosis"].append(
            {
                "test": "HTTP连接",
                "status": "failed",
                "message": f"HTTP连接失败: {str(e)}",
                "proxy_used": proxy_config if proxies else "无代理",
                "ssl_verify": ssl_verify,
                "suggestions": [
                    "检查代理配置是否正确" if proxies else "尝试配置代理",
                    "检查SSL证书验证设置",
                    "确认目标服务器是否可访问",
                ],
            }
        )
    except Exception as e:
        result["diagnosis"].append(
            {
                "test": "HTTP连接",
                "status": "error",
                "message": f"HTTP连接测试异常: {e}",
            }
        )
