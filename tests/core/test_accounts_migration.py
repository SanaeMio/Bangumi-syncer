"""
INI → 数据库迁移测试（migrate_ini_accounts_to_db）。
"""

from unittest.mock import patch

import pytest


@pytest.fixture
def ini_config(temp_dir, reset_singletons, monkeypatch):
    """用临时 INI 构造一个 ConfigManager（模拟旧版单/多用户配置）。"""
    ini = temp_dir / "config.ini"
    ini.write_text(
        "[bangumi]\n"
        "username = single_user\n"
        "access_token = AT0\n"
        "media_server_username = plex1\n"
        "\n"
        "[bangumi-foo]\n"
        "username = foo\n"
        "access_token = AT1\n"
        "media_server_username = plex2,emby2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(ini))
    from app.core.config import ConfigManager

    cm = ConfigManager()
    yield cm


def test_migrate_ini_accounts_to_db(
    ini_config, temp_dir, reset_singletons, monkeypatch
):
    import app.core.accounts as accounts_mod
    from app.core.database import DatabaseManager

    db = DatabaseManager(str(temp_dir / "acc.db"))
    monkeypatch.setattr(accounts_mod, "database_manager", db)
    monkeypatch.setattr(accounts_mod, "config_manager", ini_config)

    with patch("app.core.database.logger"):
        n = accounts_mod.migrate_ini_accounts_to_db()

    assert n == 2
    accs = db.list_bangumi_accounts()
    assert {a["section_name"] for a in accs} == {"bangumi", "bangumi-foo"}

    single = db.get_bangumi_account("bangumi")
    assert single["username"] == "single_user"
    assert single["access_token"] == "AT0"
    assert single["media_server_usernames"] == ["plex1"]

    foo = db.get_bangumi_account("bangumi-foo")
    assert foo["username"] == "foo"
    # 逗号分隔的 media_server_username 应规范化为列表
    assert foo["media_server_usernames"] == ["plex2", "emby2"]

    # 迁移后应有激活账号
    assert db.get_active_bangumi_account() is not None

    # 迁移成功后 INI 中对应账号段应被清理
    parser = ini_config.get_config_parser()
    assert not parser.has_section("bangumi")
    assert not parser.has_section("bangumi-foo")


def test_migrate_is_idempotent(ini_config, temp_dir, reset_singletons, monkeypatch):
    import app.core.accounts as accounts_mod
    from app.core.database import DatabaseManager

    db = DatabaseManager(str(temp_dir / "acc.db"))
    monkeypatch.setattr(accounts_mod, "database_manager", db)
    monkeypatch.setattr(accounts_mod, "config_manager", ini_config)

    with patch("app.core.database.logger"):
        accounts_mod.migrate_ini_accounts_to_db()
        n2 = accounts_mod.migrate_ini_accounts_to_db()

    # 第二次不应再迁移（DB 中已存在）
    assert n2 == 0
    assert db.count_bangumi_accounts() == 2
    # INI 中 bangumi 段在首次迁移后已清理，第二次无 INI 段可读
    parser = ini_config.get_config_parser()
    assert not parser.has_section("bangumi")
    assert not parser.has_section("bangumi-foo")


def test_migrate_preserves_non_account_sections(
    temp_dir, reset_singletons, monkeypatch
):
    """系统功能段（如 bangumi-data）不应被误清理。"""
    ini = temp_dir / "config.ini"
    ini.write_text(
        "[bangumi]\n"
        "username = u\n"
        "access_token = AT\n"
        "\n"
        "[bangumi-data]\n"
        "archive_path = /some/path\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(ini))
    from app.core.config import ConfigManager

    cm = ConfigManager()
    import app.core.accounts as accounts_mod
    from app.core.database import DatabaseManager

    db = DatabaseManager(str(temp_dir / "acc.db"))
    monkeypatch.setattr(accounts_mod, "database_manager", db)
    monkeypatch.setattr(accounts_mod, "config_manager", cm)

    with patch("app.core.database.logger"):
        n = accounts_mod.migrate_ini_accounts_to_db()

    assert n == 1
    parser = cm.get_config_parser()
    # bangumi 账号段已清理
    assert not parser.has_section("bangumi")
    # bangumi-data 系统功能段保留
    assert parser.has_section("bangumi-data")


def test_migrate_skips_when_db_already_has_section(
    ini_config, temp_dir, reset_singletons, monkeypatch
):
    import app.core.accounts as accounts_mod
    from app.core.database import DatabaseManager

    db = DatabaseManager(str(temp_dir / "acc.db"))
    # 预置一个同 section 但不同内容的账号，迁移不应覆盖
    db.save_bangumi_account(
        {
            "section_name": "bangumi-foo",
            "username": "prefilled",
            "media_server_usernames": [],
            "auth_method": "oauth",
        }
    )
    monkeypatch.setattr(accounts_mod, "database_manager", db)
    monkeypatch.setattr(accounts_mod, "config_manager", ini_config)

    with patch("app.core.database.logger"):
        n = accounts_mod.migrate_ini_accounts_to_db()
    # 仅 bangumi 被迁移，bangumi-foo 已存在故跳过
    assert n == 1
    assert db.get_bangumi_account("bangumi-foo")["username"] == "prefilled"


def test_migrate_numeric_username_from_ini(temp_dir, reset_singletons, monkeypatch):
    """纯数字 username（get() 会转为 int）应能正常迁移。"""
    ini = temp_dir / "config.ini"
    ini.write_text(
        "[bangumi-321246]\n"
        "username = 321246\n"
        "access_token = AT_NUM\n"
        "media_server_username = 999\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(ini))
    from app.core.config import ConfigManager

    cm = ConfigManager()
    import app.core.accounts as accounts_mod
    from app.core.database import DatabaseManager

    db = DatabaseManager(str(temp_dir / "acc.db"))
    monkeypatch.setattr(accounts_mod, "database_manager", db)
    monkeypatch.setattr(accounts_mod, "config_manager", cm)

    with patch("app.core.database.logger"):
        n = accounts_mod.migrate_ini_accounts_to_db()

    assert n == 1
    acc = db.get_bangumi_account("bangumi-321246")
    assert acc is not None
    assert acc["username"] == "321246"
    assert isinstance(acc["username"], str)
    assert acc["access_token"] == "AT_NUM"
    assert acc["media_server_usernames"] == ["999"]
