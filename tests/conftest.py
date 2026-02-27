"""
pytest configuration and shared fixtures
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_config_file(temp_dir):
    """Create a mock config file"""
    config_path = temp_dir / "config.ini"
    config_content = """[bangumi]
username = test_user
access_token = test_token
private = false

[sync]
mode = single
single_username = test_user

[dev]
script_proxy =
debug = false

[bangumi-data]
enabled = true
use_cache = true
cache_ttl_days = 7
data_url = https://unpkg.com/bangumi-data@0.3/dist/data.json

[auth]
enabled = true
username = admin
session_timeout = 3600

[notification]
enabled = false
"""
    config_path.write_text(config_content, encoding="utf-8")
    return config_path


@pytest.fixture
def mock_mapping_file(temp_dir):
    """Create a mock mapping file"""
    mapping_path = temp_dir / "bangumi_mapping.json"
    mapping_content = """{
  "_comment": "Test mapping file",
  "mappings": {
    "测试动画": "123456",
    "测试动画2": "789012"
  }
}
"""
    mapping_path.write_text(mapping_content, encoding="utf-8")
    return mapping_path


@pytest.fixture
def mock_db_file(temp_dir):
    """Create a mock database file"""
    db_path = temp_dir / "test.db"
    return str(db_path)


@pytest.fixture
def reset_singletons():
    """Reset global singleton instances"""
    # Store original modules
    original_modules = {}

    # Reset config_manager
    if "app.core.config" in sys.modules:
        original_modules["config"] = sys.modules["app.core.config"]
        del sys.modules["app.core.config"]

    # Reset database_manager
    if "app.core.database" in sys.modules:
        original_modules["database"] = sys.modules["app.core.database"]
        del sys.modules["app.core.database"]

    # Reset startup_info
    if "app.core.startup_info" in sys.modules:
        original_modules["startup_info"] = sys.modules["app.core.startup_info"]
        del sys.modules["app.core.startup_info"]

    # Reset mapping_service
    if "app.services.mapping_service" in sys.modules:
        original_modules["mapping_service"] = sys.modules[
            "app.services.mapping_service"
        ]
        del sys.modules["app.services.mapping_service"]

    # Reset docker_helper
    if "app.utils.docker_helper" in sys.modules:
        original_modules["docker_helper"] = sys.modules["app.utils.docker_helper"]
        del sys.modules["app.utils.docker_helper"]

    # Reset logging
    if "app.core.logging" in sys.modules:
        original_modules["logging"] = sys.modules["app.core.logging"]
        del sys.modules["app.core.logging"]

    yield

    # Restore original modules
    for name, module in original_modules.items():
        if name == "config":
            sys.modules["app.core.config"] = module
        elif name == "database":
            sys.modules["app.core.database"] = module
        elif name == "startup_info":
            sys.modules["app.core.startup_info"] = module
        elif name == "mapping_service":
            sys.modules["app.services.mapping_service"] = module
        elif name == "docker_helper":
            sys.modules["app.utils.docker_helper"] = module
        elif name == "logging":
            sys.modules["app.core.logging"] = module


@pytest.fixture
def mock_env():
    """Mock environment variables"""
    env_patcher = patch.dict(os.environ, {}, clear=True)
    env_patcher.start()
    yield
    env_patcher.stop()
