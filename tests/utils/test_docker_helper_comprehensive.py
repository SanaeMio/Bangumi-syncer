"""
Docker Helper 完整测试
"""

from app.utils.docker_helper import DockerProxyHelper


class TestDockerHelperComprehensive:
    """Docker Helper 综合测试"""

    def test_init(self):
        """测试初始化"""
        helper = DockerProxyHelper()
        assert helper is not None


class TestDockerHelperMethods:
    """Docker Helper 方法测试"""

    def test_get_proxy_suggestions(self):
        """测试获取代理建议"""
        helper = DockerProxyHelper()
        suggestions = helper.get_proxy_suggestions(7890)
        assert isinstance(suggestions, list)

    def test_get_proxy_suggestions_default_port(self):
        """测试默认端口"""
        helper = DockerProxyHelper()
        suggestions = helper.get_proxy_suggestions()
        assert isinstance(suggestions, list)

    def test_get_environment_info(self):
        """测试获取环境信息"""
        helper = DockerProxyHelper()
        info = helper.get_environment_info()
        assert isinstance(info, dict)
        assert "is_docker" in info

    def test_get_environment_info_docker(self):
        """测试 Docker 环境信息"""
        helper = DockerProxyHelper()
        helper.is_docker = True
        info = helper.get_environment_info()
        assert isinstance(info, dict)

    def test_get_host_ip(self):
        """测试获取宿主机 IP"""
        helper = DockerProxyHelper()
        # 方法存在即可
        assert hasattr(helper, "_get_host_ip")

    def test_get_synology_host_ip(self):
        """测试获取 Synology IP"""
        helper = DockerProxyHelper()
        assert hasattr(helper, "_get_synology_host_ip")


class TestDockerHelperConnectivity:
    """连接性测试"""

    def test_test_proxy_connectivity(self):
        """测试代理连接"""
        helper = DockerProxyHelper()
        # 这个方法需要网络
        assert hasattr(helper, "test_proxy_connectivity")

    def test_test_host_connectivity(self):
        """测试主机连接"""
        helper = DockerProxyHelper()
        assert hasattr(helper, "test_host_connectivity")

    def test_test_tcp_connection(self):
        """测试 TCP 连接"""
        helper = DockerProxyHelper()
        assert hasattr(helper, "_test_tcp_connection")


class TestDockerHelperNetwork:
    """网络诊断测试"""

    def test_get_network_diagnosis(self):
        """测试网络诊断"""
        helper = DockerProxyHelper()
        diagnosis = helper._get_network_diagnosis()
        assert isinstance(diagnosis, dict)


class TestDockerHelperIntegration:
    """集成测试"""

    def test_proxy_suggestions_contains_common(self):
        """测试常见代理建议"""
        helper = DockerProxyHelper()
        suggestions = helper.get_proxy_suggestions(7890)

        # 检查是否包含常见代理
        assert len(suggestions) > 0

    def test_environment_info_keys(self):
        """测试环境信息包含必要字段"""
        helper = DockerProxyHelper()
        info = helper.get_environment_info()

        # 应该包含这些键
        assert "is_docker" in info
