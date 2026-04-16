"""多账号配置写入时 access_token 加密。"""

from configparser import ConfigParser
from unittest.mock import MagicMock, patch

from app.api import config as config_api


def test_handle_multi_accounts_config_encrypts_access_token():
    parser = ConfigParser()
    parser.add_section("bangumi-legacy")
    parser.set("bangumi-legacy", "username", "legacy")

    mock_cm = MagicMock()
    mock_cm.get_config_parser.return_value = parser

    accounts = {
        "acc": {
            "username": "bgmuser",
            "access_token": "plain-multi-token",
            "media_server_username": "plex_u",
            "private": False,
        }
    }

    with (
        patch.object(config_api, "config_manager", mock_cm),
        patch(
            "app.core.config_secret_crypto.encrypt_if_sensitive",
            return_value="BGS1:from-test",
        ) as enc_mock,
    ):
        config_api._handle_multi_accounts_config(accounts)

    enc_mock.assert_called_once_with(
        "bangumi-bgmuser", "access_token", "plain-multi-token"
    )
    assert parser.has_section("bangumi-bgmuser")
    assert parser.get("bangumi-bgmuser", "access_token") == "BGS1:from-test"
    assert not parser.has_section("bangumi-legacy")
