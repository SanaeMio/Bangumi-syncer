"""
Config models tests
"""


class TestConfigModels:
    """Test config-related Pydantic models"""

    def test_bangumi_config_defaults(self):
        """Test BangumiConfig default values"""
        from app.models.config import BangumiConfig

        config = BangumiConfig()
        assert config.username == ""
        assert config.access_token == ""
        assert config.private is False
        assert config.media_server_username == ""

    def test_bangumi_config_validation(self):
        """Test BangumiConfig validation"""
        from app.models.config import BangumiConfig

        # With values
        config = BangumiConfig(
            username="test_user", access_token="token123", private=True
        )
        assert config.username == "test_user"
        assert config.access_token == "token123"
        assert config.private is True

    def test_sync_config_has_no_blocked_keywords(self):
        """屏蔽关键词已迁出 INI/模型，改由数据库 blocked_rules 表管理

        历史 ``SyncConfig.blocked_keywords`` 与 ``title_blacklist`` 已合并为
        DB 关键词表（见 app/core/database/blocked_rules.py）。此处守护"不回流"。
        """
        from app.models.config import SyncConfig

        config = SyncConfig()
        assert not hasattr(config, "blocked_keywords")

    def test_sync_config_accepts_no_fields(self):
        """SyncConfig 目前无字段（保留类以便未来扩展同步相关配置）"""
        from app.models.config import SyncConfig

        assert SyncConfig().model_dump() == {}

    def test_dev_config_defaults(self):
        """Test DevConfig default values"""
        from app.models.config import DevConfig

        config = DevConfig()
        assert config.script_proxy == ""
        assert config.debug is False

    def test_dev_config_with_values(self):
        """Test DevConfig with values"""
        from app.models.config import DevConfig

        config = DevConfig(script_proxy="http://proxy:8080", debug=True)
        assert config.script_proxy == "http://proxy:8080"
        assert config.debug is True

    def test_bangumi_data_config_defaults(self):
        """Test BangumiDataConfig default values"""
        from app.models.config import BangumiDataConfig

        config = BangumiDataConfig()
        assert config.enabled is True
        assert config.use_cache is True
        assert config.cache_ttl_days == 7
        assert "bangumi-data" in config.data_url

    def test_bangumi_data_config_with_values(self):
        """Test BangumiDataConfig with values"""
        from app.models.config import BangumiDataConfig

        config = BangumiDataConfig(
            enabled=False,
            use_cache=False,
            cache_ttl_days=30,
            data_url="http://custom.com/data.json",
        )
        assert config.enabled is False
        assert config.use_cache is False
        assert config.cache_ttl_days == 30

    def test_auth_config_defaults(self):
        """Test AuthConfig default values"""
        from app.models.config import AuthConfig

        config = AuthConfig()
        assert config.enabled is True
        assert config.username == "admin"
        assert config.session_timeout == 3600
        assert config.https_only is False
        assert config.max_login_attempts == 5
        assert config.lockout_duration == 900

    def test_auth_config_with_values(self):
        """Test AuthConfig with values"""
        from app.models.config import AuthConfig

        config = AuthConfig(
            enabled=False,
            username="admin2",
            session_timeout=7200,
            https_only=True,
            max_login_attempts=3,
            lockout_duration=1800,
        )
        assert config.enabled is False
        assert config.username == "admin2"
        assert config.session_timeout == 7200
        assert config.https_only is True

    def test_config_data_model(self):
        """Test ConfigData model composition"""
        from app.models.config import (
            AuthConfig,
            BangumiConfig,
            BangumiDataConfig,
            ConfigData,
            DevConfig,
            SyncConfig,
        )

        config = ConfigData(
            bangumi=BangumiConfig(),
            sync=SyncConfig(),
            dev=DevConfig(),
            bangumi_data=BangumiDataConfig(),
            auth=AuthConfig(),
        )
        assert hasattr(config, "bangumi")
        assert hasattr(config, "sync")
        assert hasattr(config, "dev")
        assert hasattr(config, "bangumi_data")
        assert hasattr(config, "auth")

    def test_config_response_model(self):
        """Test ConfigResponse model"""
        from app.models.config import (
            AuthConfig,
            BangumiConfig,
            BangumiDataConfig,
            ConfigData,
            ConfigResponse,
            DevConfig,
            SyncConfig,
        )

        data = ConfigData(
            bangumi=BangumiConfig(),
            sync=SyncConfig(),
            dev=DevConfig(),
            bangumi_data=BangumiDataConfig(),
            auth=AuthConfig(),
        )
        resp = ConfigResponse(status="success", data=data)
        assert resp.status == "success"
        assert resp.data == data

    def test_config_update_request_model(self):
        """Test ConfigUpdateRequest model"""
        from app.models.config import ConfigUpdateRequest

        # Empty update
        req = ConfigUpdateRequest()
        assert req.bangumi is None

        # With partial update
        from app.models.config import BangumiConfig

        req = ConfigUpdateRequest(bangumi=BangumiConfig(username="new_user"))
        assert req.bangumi is not None
        assert req.bangumi.username == "new_user"

    def test_config_update_response_model(self):
        """Test ConfigUpdateResponse model"""
        from app.models.config import ConfigUpdateResponse

        resp = ConfigUpdateResponse(status="success", message="Config updated")
        assert resp.status == "success"
        assert resp.message == "Config updated"
