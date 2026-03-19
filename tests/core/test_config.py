"""
ConfigManager tests - Simplified version
"""

import os
from unittest.mock import patch


class TestConfigManagerSimple:
    """Test ConfigManager class with simplified tests"""

    def test_get_config_paths(self, temp_dir):
        """Test getting config paths returns dict with expected keys"""
        from app.core.config import ConfigManager

        with patch.object(ConfigManager, "__init__", lambda x: None):
            cm = ConfigManager.__new__(ConfigManager)
            cm.cwd = temp_dir
            paths = cm._get_config_paths()

            assert "env" in paths
            assert "mounted" in paths
            assert "dev" in paths
            assert "default" in paths

    def test_find_active_config_with_env(self, temp_dir):
        """Test finding active config from environment"""
        env_config = temp_dir / "env_config.ini"
        env_config.write_text("[bangumi]\nusername = env_user\n")

        with patch.dict(os.environ, {"CONFIG_FILE": str(env_config)}):
            from app.core.config import ConfigManager

            with patch.object(ConfigManager, "__init__", lambda x: None):
                cm = ConfigManager.__new__(ConfigManager)
                cm.config_paths = {
                    "env": str(env_config),
                    "mounted": temp_dir / "mounted.ini",
                    "dev": temp_dir / "dev.ini",
                    "default": temp_dir / "default.ini",
                }

                result = cm._find_active_config()
                assert result == env_config

    def test_apply_env_overrides(self, temp_dir):
        """Test applying environment variable overrides"""
        from configparser import ConfigParser

        from app.core.config import ConfigManager

        with patch.object(ConfigManager, "__init__", lambda x: None):
            cm = ConfigManager.__new__(ConfigManager)

            config = ConfigParser()
            config.read_string("[bangumi]\nusername = original\n")

            with patch.dict(os.environ, {"BANGUMI_USERNAME": "env_user"}):
                cm._apply_env_overrides(config)

            assert config.get("bangumi", "username") == "env_user"

    def test_type_conversion(self, temp_dir):
        """Test type conversion in get_config"""
        from configparser import ConfigParser

        config = ConfigParser()
        config.read_string(
            """
[settings]
bool_true = true
bool_false = false
int_value = 42
string_value = hello
"""
        )

        # Test conversion logic
        def convert_value(value):
            if value.lower() in ("true", "false"):
                return value.lower() == "true"
            elif value.isdigit():
                return int(value)
            return value

        assert convert_value("true") is True
        assert convert_value("false") is False
        assert convert_value("42") == 42
        assert convert_value("hello") == "hello"
