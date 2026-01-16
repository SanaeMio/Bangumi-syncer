"""
代理配置API
"""

import platform
import socket
import subprocess
import time
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..core.logging import logger
from .deps import get_current_user_flexible

router = APIRouter(prefix="/api")


class ProxyTestRequest(BaseModel):
    proxy_url: str


@router.get("/proxy/suggestions")
async def get_proxy_suggestions(
    request: Request,
    port: Optional[int] = 7890,
    current_user: dict = Depends(get_current_user_flexible),
):
    """获取代理配置建议"""
    try:
        # 导入docker_helper
        from ..utils.docker_helper import docker_helper

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
        # 导入docker_helper
        from ..utils.docker_helper import docker_helper

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
        # 导入docker_helper
        from ..utils.docker_helper import docker_helper

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


def get_system_dns_servers():
    """获取系统DNS服务器配置"""
    dns_servers = []
    system = platform.system().lower()
    is_docker = False

    # 检测是否在Docker容器中
    try:
        with open("/proc/1/cgroup") as f:
            content = f.read()
            if "docker" in content or "containerd" in content:
                is_docker = True
    except FileNotFoundError:
        # Windows或非Linux系统
        try:
            # 检查是否存在Docker相关的环境变量或文件
            import os

            if os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER"):
                is_docker = True
        except:
            pass
    except:
        pass

    try:
        if system == "windows":
            # Windows系统 - 使用ipconfig /all获取DNS配置
            try:
                result = subprocess.run(
                    ["ipconfig", "/all"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    encoding="gbk",  # Windows中文系统使用gbk编码
                )
                output = result.stdout
                # 从ipconfig输出中提取DNS服务器
                lines = output.split("\n")
                for i, line in enumerate(lines):
                    if "DNS" in line and "服务器" in line:  # 中文系统
                        # 查看下一行是否包含IP地址
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
                # 如果gbk解码失败，尝试utf-8
                try:
                    result = subprocess.run(
                        ["ipconfig", "/all"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        encoding="utf-8",
                    )
                    # 处理类似逻辑...
                except:
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
                            if (
                                dns_server
                                and dns_server != "localhost"
                                and "." in dns_server
                            ):
                                dns_servers.append(dns_server)
                except:
                    pass

        elif system == "linux":
            # Linux系统 - 读取/etc/resolv.conf
            try:
                with open("/etc/resolv.conf") as f:
                    resolv_content = f.read()
                    for line in resolv_content.split("\n"):
                        line = line.strip()
                        if line.startswith("nameserver"):
                            dns_server = line.split()[1]
                            if dns_server:
                                dns_servers.append(dns_server)

                # 如果在Docker中，添加额外信息
                if is_docker:
                    dns_servers.append("(Docker容器)")

                    # 尝试获取Docker的DNS配置
                    try:
                        # 检查Docker的DNS配置
                        with open("/etc/resolv.conf") as f:
                            content = f.read()
                            if (
                                "# Generated by Docker" in content
                                or "127.0.0.11" in content
                            ):
                                dns_servers.append("Docker内置DNS: 127.0.0.11")
                    except:
                        pass

                    # 尝试从宿主机获取真实DNS（如果可能）
                    try:
                        result = subprocess.run(
                            ["cat", "/etc/resolv.conf"],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        # 添加关于Docker网络的说明
                        if "127.0.0.11" in result.stdout:
                            dns_servers.append("注意: Docker使用内置DNS转发")
                    except:
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

        # 如果没有找到DNS服务器，尝试通过socket获取
        if not dns_servers:
            try:
                # 尝试解析一个域名来获取DNS信息
                socket.getaddrinfo("google.com", 80)
                # 如果成功，说明DNS工作正常，但我们无法直接获取DNS服务器地址
                dns_servers.append("系统默认DNS（无法直接获取地址）")
            except:
                pass

    except Exception as e:
        logger.warning(f"获取DNS服务器配置失败: {e}")

    # 如果仍然没有找到，提供默认信息
    if not dns_servers:
        dns_servers.append("无法获取DNS服务器信息")

    return dns_servers


@router.post("/proxy/test-host")
async def test_host_connectivity(
    request: HostConnectivityRequest,
    current_user: dict = Depends(get_current_user_flexible),
):
    """测试到指定主机的连通性"""
    try:
        from ..utils.docker_helper import docker_helper

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
        parsed = urlparse(request.url)
        hostname = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        # 检测环境信息
        from ..core.config import config_manager
        from ..utils.docker_helper import docker_helper

        env_info = docker_helper.get_environment_info()

        # 获取配置的代理
        proxy_config = config_manager.get("dev", "script_proxy", fallback="")
        proxies = None
        if proxy_config:
            proxies = {"http": proxy_config, "https": proxy_config}

        result = {
            "url": request.url,
            "hostname": hostname,
            "port": port,
            "dns_servers": get_system_dns_servers(),
            "proxy_config": proxy_config or "未配置代理",
            "environment": {
                "is_docker": env_info.get("is_docker", False),
                "network_mode": env_info.get("network_mode", "unknown")
                if env_info.get("is_docker")
                else "native",
            },
            "diagnosis": [],
        }

        # DNS解析测试
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
            return {"status": "success", "data": result}
        except Exception as e:
            result["diagnosis"].append(
                {"test": "DNS解析", "status": "error", "message": f"DNS解析异常: {e}"}
            )
            return {"status": "success", "data": result}

        # TCP连接测试（直连）
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

        # HTTP连接测试（使用配置的代理）
        try:
            import requests

            ssl_verify = config_manager.get("dev", "ssl_verify", fallback=True)

            test_url = f"{parsed.scheme}://{hostname}:{port}"
            start_time = time.time()

            response = requests.get(
                test_url,
                proxies=proxies,
                verify=ssl_verify,
                timeout=10,
                allow_redirects=False,
            )

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

        except requests.exceptions.RequestException as e:
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

        return {"status": "success", "data": result}

    except Exception as e:
        logger.error(f"网络诊断失败: {str(e)}")
        return {"status": "error", "message": f"网络诊断失败: {str(e)}"}
