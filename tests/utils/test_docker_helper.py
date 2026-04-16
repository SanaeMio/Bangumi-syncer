"""
DockerProxyHelper tests - Simplified version
"""

from unittest.mock import MagicMock, patch


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
            run.return_value = MagicMock(returncode=0, stdout="default via 172.17.0.1 dev eth0\n")
            from app.utils.docker_helper import DockerProxyHelper

            h = DockerProxyHelper()
            h.is_docker = True
            assert h._detect_network_mode() == "bridge"

    def test_detect_network_mode_host_hint(self):
        with patch("app.utils.docker_helper.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="default via 127.0.0.1 dev lo\n")
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

        with patch("app.utils.docker_helper.requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
            h = DockerProxyHelper()
            with patch.object(
                h, "_test_basic_connectivity", return_value={"success": True}
            ):
                r = h.test_proxy_connectivity("http://127.0.0.1:7890")
        assert r["success"] is False
        assert "连接错误" in (r.get("error") or "")

    def test_test_proxy_connectivity_generic_error(self):
        from app.utils.docker_helper import DockerProxyHelper

        with patch("app.utils.docker_helper.requests.get", side_effect=RuntimeError("x")):
            h = DockerProxyHelper()
            with patch.object(
                h, "_test_basic_connectivity", return_value={"success": True}
            ):
                r = h.test_proxy_connectivity("http://127.0.0.1:7890")
        assert r["success"] is False
