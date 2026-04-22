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


def test_handle_multi_accounts_skips_when_media_server_username_parses_empty():
    """media_server_username 仅逗号/空格时跳过该账号，且不新建段。"""
    parser = ConfigParser()
    parser.add_section("bangumi-legacy")
    parser.set("bangumi-legacy", "username", "legacy")
    parser.set("bangumi-legacy", "access_token", "tok")

    mock_cm = MagicMock()
    mock_cm.get_config_parser.return_value = parser

    accounts = {
        "bad": {
            "username": "bgmuser",
            "access_token": "plain-multi-token",
            "media_server_username": ", , ",
            "private": False,
        }
    }

    with patch.object(config_api, "config_manager", mock_cm):
        config_api._handle_multi_accounts_config(accounts)

    assert not parser.has_section("bangumi-legacy")
    assert not any(name.startswith("bangumi-") for name in parser.sections())


def test_handle_multi_accounts_skips_empty_but_adds_valid_account():
    """多条账号中仅媒体用户名为空的跳过，其余正常写入。"""
    parser = ConfigParser()
    mock_cm = MagicMock()
    mock_cm.get_config_parser.return_value = parser

    accounts = {
        "bad": {
            "username": "skip_u",
            "access_token": "skip_t",
            "media_server_username": "   ",
            "private": False,
        },
        "good": {
            "username": "keep_u",
            "access_token": "keep_t",
            "media_server_username": "plex_only",
            "private": True,
        },
    }

    with (
        patch.object(config_api, "config_manager", mock_cm),
        patch(
            "app.core.config_secret_crypto.encrypt_if_sensitive",
            side_effect=lambda _sec, _k, v: v,
        ),
    ):
        config_api._handle_multi_accounts_config(accounts)

    assert parser.has_section("bangumi-keep_u")
    assert parser.get("bangumi-keep_u", "media_server_username") == "plex_only"
    assert parser.get("bangumi-keep_u", "private") == "true"
    assert not parser.has_section("bangumi-skip_u")
