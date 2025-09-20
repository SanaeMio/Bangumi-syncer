"""
Docker环境检测和代理配置助手
"""
import os
import socket
import subprocess
import requests
from typing import List, Dict, Optional
from ..core.logging import logger


class DockerProxyHelper:
    """Docker代理配置助手"""
    
    def __init__(self):
        self.is_docker = self._detect_docker_environment()
        self.network_mode = self._detect_network_mode()
        
    def _detect_docker_environment(self) -> bool:
        """检测是否在Docker环境中运行"""
        # 方法1: 检查环境变量
        if os.environ.get('DOCKER_CONTAINER'):
            return True
            
        # 方法2: 检查/.dockerenv文件
        if os.path.exists('/.dockerenv'):
            return True
            
        # 方法3: 检查cgroup信息
        try:
            with open('/proc/1/cgroup', 'r') as f:
                content = f.read()
                if 'docker' in content or 'containerd' in content:
                    return True
        except (FileNotFoundError, PermissionError):
            pass
            
        return False
    
    def _detect_network_mode(self) -> str:
        """检测Docker网络模式"""
        if not self.is_docker:
            return 'native'
            
        try:
            # 检查网络接口
            result = subprocess.run(['ip', 'route'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                output = result.stdout
                if '172.17.0.1' in output:
                    return 'bridge'
                elif 'host' in output or '127.0.0.1' in output:
                    return 'host'
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
            
        # 默认假设为bridge模式
        return 'bridge'
    
    def get_proxy_suggestions(self, port: int = 7890) -> List[Dict[str, str]]:
        """获取代理配置建议"""
        suggestions = []
        
        if not self.is_docker:
            # 非Docker环境 - 提供常用代理配置
            
            # 主要HTTP代理（用户指定端口）
            suggestions.append({
                'address': f'http://127.0.0.1:{port}',
                'description': f'本机HTTP代理 - 端口{port}（推荐）',
                'priority': 1
            })
            
            # 如果不是常用端口，添加常用端口建议
            common_http_ports = [7890, 8080, 10809, 1087]
            if port not in common_http_ports:
                for i, common_port in enumerate(common_http_ports[:2]):  # 只显示前2个
                    suggestions.append({
                        'address': f'http://127.0.0.1:{common_port}',
                        'description': f'本机HTTP代理 - 常用端口{common_port}',
                        'priority': 2 + i
                    })
            
            # SOCKS5代理建议（常用端口）
            socks_ports = [1080, 1081, 10808]
            suggestions.append({
                'address': f'socks5://127.0.0.1:{socks_ports[0]}',
                'description': f'本机SOCKS5代理 - 端口{socks_ports[0]}',
                'priority': 10
            })
            
            # 如果用户指定的端口不是SOCKS默认端口，也添加SOCKS建议
            if port not in socks_ports:
                suggestions.append({
                    'address': f'socks5://127.0.0.1:{port}',
                    'description': f'本机SOCKS5代理 - 端口{port}',
                    'priority': 11
                })
            
            # 局域网代理（如果有其他设备）
            suggestions.append({
                'address': f'http://192.168.1.1:{port}',
                'description': f'路由器代理 - 端口{port}（需要路由器支持）',
                'priority': 20
            })
            
        elif self.network_mode == 'host':
            # Host网络模式
            suggestions.append({
                'address': f'http://127.0.0.1:{port}',
                'description': f'Docker Host模式代理 - 端口{port}',
                'priority': 1
            })
        else:
            # Bridge网络模式
            # 尝试获取宿主机IP
            host_ip = self._get_host_ip()
            
            if host_ip and host_ip not in ['172.17.0.1', '127.0.0.1']:
                # 如果检测到真实宿主机IP，优先推荐
                suggestions.append({
                    'address': f'http://{host_ip}:{port}',
                    'description': f'宿主机IP代理 - 端口{port}（群晖NAS推荐）',
                    'priority': 1
                })
                
            # 标准Docker Bridge建议
            suggestions.extend([
                {
                    'address': f'http://host.docker.internal:{port}',
                    'description': f'Docker Bridge模式 - 主机别名端口{port}',
                    'priority': 2
                },
                {
                    'address': f'http://172.17.0.1:{port}',
                    'description': f'Docker Bridge模式 - 默认网关端口{port}（可能不适用于群晖）',
                    'priority': 3
                }
            ])
            
            # 如果没有检测到宿主机IP，提供手动配置建议
            if not host_ip or host_ip in ['172.17.0.1', '127.0.0.1']:
                suggestions.extend([
                    {
                        'address': f'http://192.168.1.1:{port}',
                        'description': f'手动配置 - 路由器IP端口{port}（请根据实际网络调整）',
                        'priority': 4
                    },
                    {
                        'address': f'http://192.168.0.1:{port}',
                        'description': f'手动配置 - 备选路由器IP端口{port}（请根据实际网络调整）',
                        'priority': 5
                    }
                ])
        
        return sorted(suggestions, key=lambda x: x['priority'])
    
    def _get_host_ip(self) -> Optional[str]:
        """尝试获取宿主机IP地址"""
        try:
            # 方法1: 通过默认路由获取网关IP
            result = subprocess.run(['ip', 'route', 'show', 'default'], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if 'default via' in line:
                        parts = line.split()
                        if len(parts) >= 3:
                            gateway_ip = parts[2]
                            # 对于群晖等NAS，尝试获取真实的宿主机IP
                            if gateway_ip == '172.17.0.1':
                                # 尝试通过其他方法获取宿主机IP
                                real_host_ip = self._get_synology_host_ip()
                                if real_host_ip:
                                    return real_host_ip
                            return gateway_ip
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
            
        return None
    
    def _get_synology_host_ip(self) -> Optional[str]:
        """尝试获取群晖等NAS的真实宿主机IP"""
        try:
            # 方法1: 通过环境变量获取
            host_ip = os.environ.get('HOST_IP')
            if host_ip:
                return host_ip
                
            # 方法2: 尝试连接外部服务获取本地网络信息
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            
            # 如果获取到的是容器IP，尝试推断宿主机IP
            if local_ip.startswith('172.17.'):
                # 尝试通过网络接口获取宿主机网段
                result = subprocess.run(['ip', 'route'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        # 查找非Docker网段的路由
                        if ('192.168.' in line or '10.' in line) and 'dev' in line:
                            import re
                            ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
                            if ip_match:
                                potential_ip = ip_match.group(1)
                                # 通常宿主机IP是网段的.1地址
                                ip_parts = potential_ip.split('.')
                                if len(ip_parts) == 4:
                                    host_ip = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.1"
                                    return host_ip
            
            return local_ip if not local_ip.startswith('172.17.') else None
            
        except Exception as e:
            logger.debug(f"获取群晖宿主机IP失败: {e}")
            return None
    
    def test_proxy_connectivity(self, proxy_url: str, timeout: int = 5) -> Dict[str, any]:
        """测试代理连通性"""
        result = {
            'success': False,
            'response_time': None,
            'error': None,
            'details': {}
        }
        
        try:
            import time
            start_time = time.time()
            
            # 首先测试基础网络连通性
            basic_connectivity = self._test_basic_connectivity(proxy_url, timeout)
            result['details']['basic_connectivity'] = basic_connectivity
            
            if not basic_connectivity['success']:
                result['error'] = f'基础连通性失败: {basic_connectivity["error"]}'
                return result
            
            # 使用代理访问一个简单的HTTP服务
            proxies = {'http': proxy_url, 'https': proxy_url}
            response = requests.get('http://httpbin.org/ip', 
                                  proxies=proxies, 
                                  timeout=timeout,
                                  verify=False)  # 测试时不验证SSL
            
            if response.status_code == 200:
                result['success'] = True
                result['response_time'] = round((time.time() - start_time) * 1000, 2)
                result['details']['proxy_response'] = response.json() if response.headers.get('content-type', '').startswith('application/json') else response.text[:200]
                logger.info(f'代理连通性测试成功: {proxy_url} ({result["response_time"]}ms)')
            else:
                result['error'] = f'HTTP {response.status_code}'
                
        except requests.exceptions.ConnectTimeout:
            result['error'] = '连接超时'
        except requests.exceptions.ConnectionError as e:
            result['error'] = f'连接错误: {str(e)}'
        except Exception as e:
            result['error'] = f'测试失败: {str(e)}'
            
        if not result['success']:
            logger.debug(f'代理连通性测试失败: {proxy_url} - {result["error"]}')
            
        return result
    
    def _test_basic_connectivity(self, proxy_url: str, timeout: int = 3) -> Dict[str, any]:
        """测试基础网络连通性（不通过代理）"""
        result = {
            'success': False,
            'error': None,
            'host': None,
            'port': None
        }
        
        try:
            # 解析代理URL
            import re
            match = re.match(r'https?://([^:]+):(\d+)', proxy_url)
            if not match:
                result['error'] = '无法解析代理URL格式'
                return result
                
            host, port = match.groups()
            port = int(port)
            result['host'] = host
            result['port'] = port
            
            # 测试TCP连接
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            
            try:
                connect_result = sock.connect_ex((host, port))
                if connect_result == 0:
                    result['success'] = True
                    logger.debug(f'基础连通性测试成功: {host}:{port}')
                else:
                    result['error'] = f'TCP连接失败，错误代码: {connect_result}'
            finally:
                sock.close()
                
        except socket.timeout:
            result['error'] = 'TCP连接超时'
        except socket.gaierror as e:
            result['error'] = f'DNS解析失败: {str(e)}'
        except Exception as e:
            result['error'] = f'连接测试异常: {str(e)}'
            
        return result
    
    def get_environment_info(self) -> Dict[str, any]:
        """获取环境信息"""
        return {
            'is_docker': self.is_docker,
            'network_mode': self.network_mode,
            'docker_env_var': bool(os.environ.get('DOCKER_CONTAINER')),
            'dockerenv_file': os.path.exists('/.dockerenv'),
            'host_ip': self._get_host_ip(),
            'network_diagnosis': self._get_network_diagnosis()
        }
    
    def _get_network_diagnosis(self) -> Dict[str, any]:
        """获取网络诊断信息"""
        diagnosis = {
            'container_ip': None,
            'gateway': None,
            'routes': [],
            'dns_servers': [],
            'network_interfaces': []
        }
        
        try:
            # 获取容器IP
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            diagnosis['container_ip'] = s.getsockname()[0]
            s.close()
        except:
            pass
            
        try:
            # 获取路由信息
            result = subprocess.run(['ip', 'route'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                routes = []
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        routes.append(line.strip())
                        if 'default via' in line:
                            parts = line.split()
                            if len(parts) >= 3:
                                diagnosis['gateway'] = parts[2]
                diagnosis['routes'] = routes
        except:
            pass
            
        try:
            # 获取DNS服务器
            with open('/etc/resolv.conf', 'r') as f:
                for line in f:
                    if line.startswith('nameserver'):
                        dns_server = line.split()[1]
                        diagnosis['dns_servers'].append(dns_server)
        except:
            pass
            
        try:
            # 获取网络接口
            result = subprocess.run(['ip', 'addr', 'show'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                interfaces = []
                current_interface = None
                for line in result.stdout.split('\n'):
                    if ': ' in line and 'inet' not in line:
                        # 接口名称行
                        parts = line.split(': ')
                        if len(parts) >= 2:
                            current_interface = {
                                'name': parts[1].split('@')[0],
                                'ips': []
                            }
                            interfaces.append(current_interface)
                    elif 'inet ' in line and current_interface:
                        # IP地址行
                        import re
                        ip_match = re.search(r'inet (\d+\.\d+\.\d+\.\d+/\d+)', line)
                        if ip_match:
                            current_interface['ips'].append(ip_match.group(1))
                diagnosis['network_interfaces'] = interfaces
        except:
            pass
            
        return diagnosis
    
    def test_host_connectivity(self, host: str, port: int = 80, timeout: int = 3) -> Dict[str, any]:
        """测试到指定主机的连通性"""
        result = {
            'success': False,
            'error': None,
            'response_time': None,
            'details': {
                'ping_test': None,
                'tcp_test': None,
                'traceroute': None
            }
        }
        
        try:
            import time
            start_time = time.time()
            
            # TCP连接测试
            tcp_result = self._test_tcp_connection(host, port, timeout)
            result['details']['tcp_test'] = tcp_result
            
            if tcp_result['success']:
                result['success'] = True
                result['response_time'] = round((time.time() - start_time) * 1000, 2)
            else:
                result['error'] = tcp_result['error']
                
            # 尝试ping测试（可能需要特权）
            try:
                ping_result = subprocess.run(['ping', '-c', '1', '-W', str(timeout), host], 
                                           capture_output=True, text=True, timeout=timeout + 2)
                if ping_result.returncode == 0:
                    result['details']['ping_test'] = {'success': True, 'output': ping_result.stdout}
                else:
                    result['details']['ping_test'] = {'success': False, 'error': ping_result.stderr}
            except:
                result['details']['ping_test'] = {'success': False, 'error': 'ping命令不可用或无权限'}
                
        except Exception as e:
            result['error'] = f'连通性测试异常: {str(e)}'
            
        return result
    
    def _test_tcp_connection(self, host: str, port: int, timeout: int = 3) -> Dict[str, any]:
        """测试TCP连接"""
        result = {
            'success': False,
            'error': None,
            'host': host,
            'port': port
        }
        
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            
            try:
                connect_result = sock.connect_ex((host, port))
                if connect_result == 0:
                    result['success'] = True
                else:
                    result['error'] = f'TCP连接失败，错误代码: {connect_result}'
            finally:
                sock.close()
                
        except socket.timeout:
            result['error'] = 'TCP连接超时'
        except socket.gaierror as e:
            result['error'] = f'DNS解析失败: {str(e)}'
        except Exception as e:
            result['error'] = f'TCP连接异常: {str(e)}'
            
        return result


# 全局实例
docker_helper = DockerProxyHelper()
