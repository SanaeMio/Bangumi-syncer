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


# ── 多账号映射：同一媒体服务器用户名 → 多个 Bangumi 账号 ──────────────


def _make_account(section, username, media_usernames, token="AT"):
    return {
        "section_name": section,
        "username": username,
        "media_server_usernames": list(media_usernames),
        "auth_method": "manual",
        "access_token": token,
        "is_active": False,
    }


def _accounts_db(temp_dir, accounts):
    """构造含指定账号的临时 DB，供映射类函数直接读取。"""
    from app.core.database import DatabaseManager

    db = DatabaseManager(str(temp_dir / "mapping.db"))
    for acc in accounts:
        db.save_bangumi_account(acc)
    return db


def _two_accounts_one_media_user():
    """一人两号场景：两个 Bangumi 账号声明同一个媒体服务器用户名。"""
    return [
        _make_account("bangumi", "u1", ["Elegy233"]),
        _make_account("bangumi-944646", "u2", ["Elegy233"]),
    ]


def test_user_account_mappings_keeps_all_accounts_for_same_media_user(
    temp_dir, reset_singletons, monkeypatch
):
    """同一媒体服务器用户名被多个账号声明时，全部账号都参与同步。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(temp_dir, _two_accounts_one_media_user())
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    assert accounts_mod.get_user_account_mappings() == {
        "Elegy233": ["bangumi", "bangumi-944646"]
    }


def test_user_mappings_returns_first_declared_account(
    temp_dir, reset_singletons, monkeypatch
):
    """只需单一账号的调用方（追番日历、补发鉴权）取首选账号。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(temp_dir, _two_accounts_one_media_user())
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    assert accounts_mod.get_user_mappings() == {"Elegy233": "bangumi"}


def test_bangumi_configs_for_user_returns_all_accounts(
    temp_dir, reset_singletons, monkeypatch
):
    """按媒体服务器用户名取回全部账号配置（按登记顺序）。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(temp_dir, _two_accounts_one_media_user())
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    cfgs = accounts_mod.get_bangumi_configs_for_user("Elegy233")
    assert [c["username"] for c in cfgs] == ["u1", "u2"]


def test_bangumi_configs_for_user_unknown_returns_empty(
    temp_dir, reset_singletons, monkeypatch
):
    """未绑定任何账号的媒体服务器用户名返回空列表。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(temp_dir, _two_accounts_one_media_user())
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    assert accounts_mod.get_bangumi_configs_for_user("stranger") == []
    assert accounts_mod.get_bangumi_sections_for_user("stranger") == []


def test_bangumi_sections_for_user_skips_incomplete_account(
    temp_dir, reset_singletons, monkeypatch
):
    """配置不完整（无 access_token）的账号不参与同步。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(
        temp_dir,
        [
            _make_account("bangumi", "u1", ["Elegy233"]),
            _make_account("bangumi-broken", "u2", ["Elegy233"], token=""),
        ],
    )
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    assert [
        c["username"] for c in accounts_mod.get_bangumi_configs_for_user("Elegy233")
    ] == ["u1"]


def test_bangumi_sections_for_user_empty_username_multi_returns_empty(
    temp_dir, reset_singletons, monkeypatch
):
    """多账号下空 user_name 不回退激活账号，避免数据串号。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(temp_dir, _two_accounts_one_media_user())
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    assert accounts_mod.get_bangumi_sections_for_user("") == []


def test_bangumi_sections_for_user_empty_username_single_falls_back_to_active(
    temp_dir, reset_singletons, monkeypatch
):
    """单账号下空 user_name 仍回退激活账号（只有一个账号，无串号风险）。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(temp_dir, [_make_account("bangumi", "u1", ["Elegy233"])])
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    assert accounts_mod.get_bangumi_sections_for_user("") == ["bangumi"]


def test_upstream_one_to_one_mapping_drops_second_account(
    temp_dir, reset_singletons, monkeypatch
):
    """对照（实现前）：一对一映射把重复用户名折叠成单个账号，第二个账号被丢弃。

    复刻旧逻辑：后者覆盖前者。这正是玉响/一人多号场景下第二个 Bangumi 账号
    收不到同步的原因；``get_user_account_mappings`` 保留全部账号。
    """
    db = _accounts_db(temp_dir, _two_accounts_one_media_user())

    collapsed: dict[str, str] = {}
    for acc in db.list_bangumi_accounts():
        for name in acc.get("media_server_usernames") or []:
            collapsed[name] = acc["section_name"]  # 后者覆盖前者

    assert collapsed == {"Elegy233": "bangumi-944646"}
    # 旧逻辑只保留一个账号，第二个 Bangumi 账号（bangumi）被静默丢弃
    assert len([s for s in collapsed.values()]) == 1


def test_distinct_media_users_keep_one_to_one_routing(
    temp_dir, reset_singletons, monkeypatch
):
    """多账号但媒体服务器用户名互不重叠（不同任务）时，路由与变更前一致。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(
        temp_dir,
        [
            _make_account("bangumi", "u1", ["alice"]),
            _make_account("bangumi-944646", "u2", ["bob"]),
            _make_account("bangumi-friend", "u3", ["carol"]),
        ],
    )
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    assert accounts_mod.get_user_mappings() == {
        "alice": "bangumi",
        "bob": "bangumi-944646",
        "carol": "bangumi-friend",
    }
    # 一对多映射退化为单元素列表，分发阶段不会命中其余账号
    assert accounts_mod.get_user_account_mappings() == {
        "alice": ["bangumi"],
        "bob": ["bangumi-944646"],
        "carol": ["bangumi-friend"],
    }
    for name, expected in (("alice", "u1"), ("bob", "u2"), ("carol", "u3")):
        assert accounts_mod.get_bangumi_config_for_user(name)["username"] == expected


def test_one_account_with_multiple_media_users(temp_dir, reset_singletons, monkeypatch):
    """一个账号声明多个媒体服务器用户名时，每个用户名都路由到该账号。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(
        temp_dir,
        [
            _make_account("bangumi", "u1", ["alice"]),
            _make_account("bangumi-944646", "u2", ["bob", "dave"]),
        ],
    )
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    assert accounts_mod.get_user_mappings() == {
        "alice": "bangumi",
        "bob": "bangumi-944646",
        "dave": "bangumi-944646",
    }
    assert accounts_mod.get_user_account_mappings() == {
        "alice": ["bangumi"],
        "bob": ["bangumi-944646"],
        "dave": ["bangumi-944646"],
    }
    assert accounts_mod.get_bangumi_config_for_user("dave")["username"] == "u2"
