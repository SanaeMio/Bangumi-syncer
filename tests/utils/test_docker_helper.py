"""
DockerProxyHelper tests - Simplified version
"""

import socket
import subprocess
from unittest.mock import MagicMock, mock_open, patch


class TestDockerProxyHelperSimple:
    """Test DockerProxyHelper with simplified tests"""

    def test_docker_proxy_helper_init(self):
        """Test Docker proxy helper initialization"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()
        assert hasattr(helper, "is_docker")
        assert hasattr(helper, "network_mode")

    def test_detect_docker_environment_env(self):
        """Test Docker detection via env variable"""
        with patch("app.utils.docker_helper.os.environ.get") as mock_get:
            mock_get.return_value = "true"

            from app.utils.docker_helper import DockerProxyHelper

            helper = DockerProxyHelper()
            result = helper._detect_docker_environment()
            assert result is True

    def test_detect_docker_environment_not_docker(self):
        """Test Docker detection returns False"""
        with (
            patch("app.utils.docker_helper.os.environ.get", return_value=None),
            patch("app.utils.docker_helper.os.path.exists", return_value=False),
            patch("builtins.open", MagicMock(side_effect=FileNotFoundError())),
        ):
            from app.utils.docker_helper import DockerProxyHelper

            helper = DockerProxyHelper()
            result = helper._detect_docker_environment()
            assert result is False

    def test_detect_network_mode_native(self):
        """Test network mode for native"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()
        helper.is_docker = False

        result = helper._detect_network_mode()
        assert result == "native"

    def test_get_proxy_suggestions_non_docker(self):
        """Test proxy suggestions for non-Docker"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()
        helper.is_docker = False

        suggestions = helper.get_proxy_suggestions(port=7890)
        assert len(suggestions) > 0
        assert any("127.0.0.1:7890" in s["address"] for s in suggestions)

    def test_get_proxy_suggestions_host_mode(self):
        """Test proxy suggestions for host mode"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()
        helper.is_docker = True
        helper.network_mode = "host"

        suggestions = helper.get_proxy_suggestions(port=7890)
        assert len(suggestions) > 0

    def test_get_proxy_suggestions_bridge_mode(self):
        """Test proxy suggestions for bridge mode"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()
        helper.is_docker = True
        helper.network_mode = "bridge"

        with patch.object(helper, "_get_host_ip", return_value=None):
            suggestions = helper.get_proxy_suggestions(port=7890)
            assert len(suggestions) > 0

    def test_get_host_ip(self):
        """Test getting host IP"""
        with patch("app.utils.docker_helper.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "default via 192.168.1.1 dev eth0"
            mock_run.return_value = mock_result

            from app.utils.docker_helper import DockerProxyHelper

            helper = DockerProxyHelper()
            result = helper._get_host_ip()
            assert result == "192.168.1.1"

    def test_get_synology_host_ip_from_env(self):
        """Test getting Synology IP from env"""
        with patch("app.utils.docker_helper.os.environ.get") as mock_get:
            mock_get.return_value = "192.168.1.100"

            from app.utils.docker_helper import DockerProxyHelper

            helper = DockerProxyHelper()
            result = helper._get_synology_host_ip()
            assert result == "192.168.1.100"

    def test_test_proxy_connectivity_success(self):
        """Test proxy connectivity success"""

        with patch("app.utils.docker_helper.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "application/json"}
            mock_response.json.return_value = {"origin": "1.2.3.4"}
            mock_get.return_value = mock_response

            from app.utils.docker_helper import DockerProxyHelper

            helper = DockerProxyHelper()

            with patch.object(
                helper, "_test_basic_connectivity", return_value={"success": True}
            ):
                result = helper.test_proxy_connectivity("http://127.0.0.1:7890")
                assert result["success"] is True

    def test_test_proxy_connectivity_timeout(self):
        """Test proxy connectivity timeout"""
        import requests

        with patch("app.utils.docker_helper.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectTimeout()

            from app.utils.docker_helper import DockerProxyHelper

            helper = DockerProxyHelper()

            with patch.object(
                helper, "_test_basic_connectivity", return_value={"success": True}
            ):
                result = helper.test_proxy_connectivity("http://127.0.0.1:7890")
                assert result["success"] is False
                assert "超时" in result["error"]

    def test_get_environment_info(self):
        """Test getting environment info"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()
        helper.is_docker = False

        with (
            patch.object(helper, "_get_host_ip", return_value=None),
            patch.object(helper, "_get_network_diagnosis", return_value={}),
        ):
            info = helper.get_environment_info()
            assert "is_docker" in info
            assert "network_mode" in info

    def test_detect_docker_via_dockerenv_file(self):
        with (
            patch("app.utils.docker_helper.os.environ.get", return_value=None),
            patch("app.utils.docker_helper.os.path.exists") as ex,
        ):
            ex.side_effect = lambda p: p == "/.dockerenv"
            from app.utils.docker_helper import DockerProxyHelper

            h = DockerProxyHelper()
            assert h._detect_docker_environment() is True

    def test_detect_network_mode_bridge_via_default_route(self):
        with patch("app.utils.docker_helper.subprocess.run") as run:
            run.return_value = MagicMock(
                returncode=0, stdout="default via 172.17.0.1 dev eth0\n"
            )
            from app.utils.docker_helper import DockerProxyHelper

            h = DockerProxyHelper()
            h.is_docker = True
            assert h._detect_network_mode() == "bridge"

    def test_detect_network_mode_host_hint(self):
        with patch("app.utils.docker_helper.subprocess.run") as run:
            run.return_value = MagicMock(
                returncode=0, stdout="default via 127.0.0.1 dev lo\n"
            )
            from app.utils.docker_helper import DockerProxyHelper

            h = DockerProxyHelper()
            h.is_docker = True
            assert h._detect_network_mode() == "host"

    def test_get_proxy_suggestions_bridge_with_realistic_host_ip(self):
        from app.utils.docker_helper import DockerProxyHelper

        h = DockerProxyHelper()
        h.is_docker = True
        h.network_mode = "bridge"
        with patch.object(h, "_get_host_ip", return_value="192.168.88.5"):
            sug = h.get_proxy_suggestions(port=7890)
        addrs = [s["address"] for s in sug]
        assert any("192.168.88.5" in a for a in addrs)

    def test_get_proxy_suggestions_non_docker_uncommon_port_adds_extra(self):
        from app.utils.docker_helper import DockerProxyHelper

        h = DockerProxyHelper()
        h.is_docker = False
        sug = h.get_proxy_suggestions(port=9999)
        assert len(sug) >= 3

    def test_test_basic_connectivity_invalid_url(self):
        from app.utils.docker_helper import DockerProxyHelper

        h = DockerProxyHelper()
        r = h._test_basic_connectivity("not-a-url")
        assert r["success"] is False
        assert "解析" in (r.get("error") or "")

    def test_test_basic_connectivity_tcp_fail(self):
        from app.utils.docker_helper import DockerProxyHelper

        h = DockerProxyHelper()
        with patch("socket.socket") as sock_cls:
            inst = MagicMock()
            inst.connect_ex.return_value = 1
            sock_cls.return_value = inst
            r = h._test_basic_connectivity("http://127.0.0.1:9")
        assert r["success"] is False

    def test_test_proxy_connectivity_connection_error(self):
        import requests

        from app.utils.docker_helper import DockerProxyHelper

        with patch(
            "app.utils.docker_helper.requests.get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            h = DockerProxyHelper()
            with patch.object(
                h, "_test_basic_connectivity", return_value={"success": True}
            ):
                r = h.test_proxy_connectivity("http://127.0.0.1:7890")
        assert r["success"] is False
        assert "连接错误" in (r.get("error") or "")

    def test_test_proxy_connectivity_generic_error(self):
        from app.utils.docker_helper import DockerProxyHelper

        with patch(
            "app.utils.docker_helper.requests.get", side_effect=RuntimeError("x")
        ):
            h = DockerProxyHelper()
            with patch.object(
                h, "_test_basic_connectivity", return_value={"success": True}
            ):
                r = h.test_proxy_connectivity("http://127.0.0.1:7890")
        assert r["success"] is False


class TestDockerProxyHelperNetworkDiagnosis:
    """测试网络诊断功能"""

    def test_get_network_diagnosis_success(self):
        """测试成功获取网络诊断信息"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with (
            patch("socket.socket") as mock_socket_cls,
            patch("subprocess.run") as mock_run,
            patch("builtins.open", MagicMock(readable=lambda: True)),
        ):
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("172.17.0.2", 12345)
            mock_socket_cls.return_value = mock_sock

            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="default via 172.17.0.1 dev eth0\n172.17.0.0/16 dev eth0",
            )

            with patch("builtins.open", mock_open(read_data="nameserver 8.8.8.8\n")):
                diagnosis = helper._get_network_diagnosis()
                assert diagnosis["container_ip"] == "172.17.0.2"
                assert diagnosis["gateway"] == "172.17.0.1"

    def test_get_network_diagnosis_socket_error(self):
        """测试socket错误时的诊断"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with patch("socket.socket") as mock_socket_cls:
            mock_socket_cls.side_effect = Exception("Socket error")
            diagnosis = helper._get_network_diagnosis()
            assert diagnosis["container_ip"] is None

    def test_get_network_diagnosis_subprocess_timeout(self):
        """测试subprocess超时时的诊断"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with (
            patch("socket.socket") as mock_socket_cls,
            patch("subprocess.run") as mock_run,
        ):
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("172.17.0.2", 12345)
            mock_socket_cls.return_value = mock_sock

            mock_run.side_effect = subprocess.TimeoutExpired(cmd="ip route", timeout=5)
            diagnosis = helper._get_network_diagnosis()
            assert diagnosis["routes"] == []


class TestDockerProxyHelperHostConnectivity:
    """测试主机连通性测试"""

    def test_test_host_connectivity_success(self):
        """测试主机连通性成功"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with (
            patch.object(helper, "_test_tcp_connection") as mock_tcp,
            patch("subprocess.run") as mock_run,
        ):
            mock_tcp.return_value = {
                "success": True,
                "error": None,
                "host": "example.com",
                "port": 80,
            }
            mock_run.return_value = MagicMock(returncode=0, stdout="PING success")

            result = helper.test_host_connectivity("example.com", 80)
            assert result["success"] is True
            assert result["response_time"] is not None

    def test_test_host_connectivity_tcp_fail(self):
        """测试TCP连接失败"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with patch.object(helper, "_test_tcp_connection") as mock_tcp:
            mock_tcp.return_value = {"success": False, "error": "Connection refused"}

            result = helper.test_host_connectivity("example.com", 80)
            assert result["success"] is False
            assert "Connection refused" in result["error"]

    def test_test_host_connectivity_exception(self):
        """测试异常情况"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with patch.object(
            helper, "_test_tcp_connection", side_effect=Exception("Test error")
        ):
            result = helper.test_host_connectivity("example.com", 80)
            assert result["success"] is False
            assert "Test error" in result["error"]


class TestDockerProxyHelperTcpConnection:
    """测试TCP连接功能"""

    def test_test_tcp_connection_success(self):
        """测试TCP连接成功"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0
            mock_socket_cls.return_value = mock_sock

            result = helper._test_tcp_connection("example.com", 80)
            assert result["success"] is True
            assert result["host"] == "example.com"

    def test_test_tcp_connection_fail(self):
        """测试TCP连接失败"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 111
            mock_socket_cls.return_value = mock_sock

            result = helper._test_tcp_connection("example.com", 80)
            assert result["success"] is False
            assert "TCP连接失败" in result["error"]

    def test_test_tcp_connection_timeout(self):
        """测试TCP连接超时"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.connect_ex.side_effect = socket.timeout("Timeout")
            mock_socket_cls.return_value = mock_sock

            result = helper._test_tcp_connection("example.com", 80)
            assert result["success"] is False
            assert "超时" in result["error"]

    def test_test_tcp_connection_dns_error(self):
        """测试DNS解析失败"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.connect_ex.side_effect = socket.gaierror("DNS error")
            mock_socket_cls.return_value = mock_sock

            result = helper._test_tcp_connection("nonexistent.example.com", 80)
            assert result["success"] is False
            assert "DNS" in result["error"]


class TestDockerProxyHelperGetHostIp:
    """测试获取宿主机IP"""

    def test_get_host_ip_success(self):
        """成功获取宿主机IP"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="default via 192.168.1.1 dev eth0"
            )

            result = helper._get_host_ip()
            assert result == "192.168.1.1"

    def test_get_host_ip_gateway_172(self):
        """网关为172.17.0.1时尝试获取真实IP"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with (
            patch("subprocess.run") as mock_run,
            patch.object(helper, "_get_synology_host_ip", return_value="192.168.1.100"),
        ):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="default via 172.17.0.1 dev eth0"
            )

            result = helper._get_host_ip()
            assert result == "192.168.1.100"

    def test_get_host_ip_timeout(self):
        """subprocess超时时返回None"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="ip route", timeout=5)

            result = helper._get_host_ip()
            assert result is None

    def test_get_host_ip_file_not_found(self):
        """命令不存在时返回None"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("Command not found")

            result = helper._get_host_ip()
            assert result is None


class TestDockerProxyHelperSynologyHostIp:
    """测试群晖宿主机IP获取"""

    def test_get_synology_host_ip_from_socket(self):
        """通过socket获取群晖IP"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with (
            patch("os.environ.get", return_value=None),
            patch("socket.socket") as mock_socket_cls,
            patch("subprocess.run") as mock_run,
        ):
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("192.168.1.50", 12345)
            mock_socket_cls.return_value = mock_sock

            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="192.168.1.0/24 dev eth0 proto kernel scope link src 192.168.1.50",
            )

            result = helper._get_synology_host_ip()
            assert result == "192.168.1.50"

    def test_get_synology_host_ip_container_network(self):
        """容器网络时推断宿主机IP"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with (
            patch("os.environ.get", return_value=None),
            patch("socket.socket") as mock_socket_cls,
            patch("subprocess.run") as mock_run,
        ):
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("172.17.0.2", 12345)
            mock_socket_cls.return_value = mock_sock

            mock_run.return_value = MagicMock(
                returncode=0, stdout="192.168.1.0/24 dev eth0 proto kernel scope link"
            )

            result = helper._get_synology_host_ip()
            assert result is not None
            assert result.startswith("192.168.1.")

    def test_get_synology_host_ip_exception(self):
        """异常时返回None"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with patch("os.environ.get", side_effect=Exception("Error")):
            result = helper._get_synology_host_ip()
            assert result is None


class TestDockerProxyHelperGetProxySuggestionsExtended:
    """扩展的代理建议测试"""

    def test_get_proxy_suggestions_non_docker_common_port(self):
        """非Docker环境常用端口"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()
        helper.is_docker = False

        suggestions = helper.get_proxy_suggestions(port=7890)
        assert len(suggestions) > 0
        assert any("127.0.0.1:7890" in s["address"] for s in suggestions)

    def test_get_proxy_suggestions_socks_ports(self):
        """SOCKS5端口建议"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()
        helper.is_docker = False

        suggestions = helper.get_proxy_suggestions(port=7890)
        assert any("socks5://" in s["address"] for s in suggestions)

    def test_get_proxy_suggestions_sorted_by_priority(self):
        """建议按优先级排序"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()
        helper.is_docker = False

        suggestions = helper.get_proxy_suggestions(port=7890)
        priorities = [s["priority"] for s in suggestions]
        assert priorities == sorted(priorities)


class TestDockerProxyHelperTestProxyConnectivityExtended:
    """扩展的代理连通性测试"""

    @patch("app.utils.notifier.requests.get")
    def test_test_proxy_connectivity_non_json_response(self, mock_get):
        """非JSON响应"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = "<html>OK</html>"
        mock_get.return_value = mock_response

        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with patch.object(
            helper, "_test_basic_connectivity", return_value={"success": True}
        ):
            result = helper.test_proxy_connectivity("http://127.0.0.1:7890")
            assert result["success"] is True

    def test_test_proxy_connectivity_basic_fail(self):
        """基础连通性失败"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()

        with patch.object(
            helper,
            "_test_basic_connectivity",
            return_value={"success": False, "error": "Connection refused"},
        ):
            result = helper.test_proxy_connectivity("http://127.0.0.1:7890")
            assert result["success"] is False
            assert "基础连通性失败" in result["error"]


class TestDockerProxyHelperDetectNetworkModeExtended:
    """扩展的网络模式检测测试"""

    def test_detect_network_mode_timeout(self):
        """超时时默认bridge"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()
        helper.is_docker = True

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="ip route", timeout=5)

            result = helper._detect_network_mode()
            assert result == "bridge"

    def test_detect_network_mode_command_not_found(self):
        """命令不存在时默认bridge"""
        from app.utils.docker_helper import DockerProxyHelper

        helper = DockerProxyHelper()
        helper.is_docker = True

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("Command not found")

            result = helper._detect_network_mode()
            assert result == "bridge"
