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

    # 迁移后应有首选账号
    assert db.get_primary_bangumi_account() is not None

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
        "is_primary": False,
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
    """多账号下空 user_name 不回退首选账号，避免数据串号。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(temp_dir, _two_accounts_one_media_user())
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    assert accounts_mod.get_bangumi_sections_for_user("") == []


def test_bangumi_sections_for_user_empty_username_single_falls_back_to_primary(
    temp_dir, reset_singletons, monkeypatch
):
    """单账号下空 user_name 仍回退首选账号（只有一个账号，无串号风险）。"""
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


def test_accounts_are_enabled_by_default(temp_dir, reset_singletons, monkeypatch):
    """有效账号默认为启用状态（参与任务同步）。"""
    db = _accounts_db(temp_dir, _two_accounts_one_media_user())

    assert [a["enabled"] for a in db.list_bangumi_accounts()] == [True, True]


def test_disabled_account_excluded_from_sync_sections(
    temp_dir, reset_singletons, monkeypatch
):
    """停用的账号不出现在同步解析的段名列表中，但仍保留在账号列表里。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(temp_dir, _two_accounts_one_media_user())
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    assert accounts_mod.get_bangumi_sections_for_user("Elegy233") == [
        "bangumi",
        "bangumi-944646",
    ]

    db.set_enabled_bangumi_account("bangumi", False)
    assert accounts_mod.get_bangumi_sections_for_user("Elegy233") == ["bangumi-944646"]
    # 停用不改变账号列表，界面仍可见以便重新启用
    assert {a["section_name"] for a in db.list_bangumi_accounts()} == {
        "bangumi",
        "bangumi-944646",
    }


def test_disabled_primary_hands_over_to_next_account(
    temp_dir, reset_singletons, monkeypatch
):
    """首选账号被停用时，首选顺延到下一个启用账号。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(temp_dir, _two_accounts_one_media_user())
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    assert accounts_mod.get_bangumi_config_for_user("Elegy233")["username"] == "u1"
    db.set_enabled_bangumi_account("bangumi", False)
    assert accounts_mod.get_bangumi_config_for_user("Elegy233")["username"] == "u2"


def test_reenabled_account_restores_sync(temp_dir, reset_singletons, monkeypatch):
    """重新启用后账号恢复参与同步。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(temp_dir, _two_accounts_one_media_user())
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    db.set_enabled_bangumi_account("bangumi", False)
    assert accounts_mod.get_bangumi_sections_for_user("Elegy233") == ["bangumi-944646"]
    db.set_enabled_bangumi_account("bangumi", True)
    assert accounts_mod.get_bangumi_sections_for_user("Elegy233") == [
        "bangumi",
        "bangumi-944646",
    ]


def test_all_accounts_disabled_returns_no_sections(
    temp_dir, reset_singletons, monkeypatch
):
    """全部账号停用时，同步解析返回空列表。"""
    import app.core.accounts as accounts_mod

    db = _accounts_db(temp_dir, _two_accounts_one_media_user())
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    for section in ("bangumi", "bangumi-944646"):
        db.set_enabled_bangumi_account(section, False)

    assert accounts_mod.get_bangumi_sections_for_user("Elegy233") == []
    assert accounts_mod.get_bangumi_config_for_user("Elegy233") is None


def test_old_db_is_active_renamed_to_is_primary(temp_dir, reset_singletons):
    """旧库仅含 is_active 列时，迁移应补齐 is_primary、拷贝数据并删除旧列。"""
    import sqlite3

    from app.core.database import DatabaseManager

    db_path = str(temp_dir / "old_accounts.db")
    # ① 先以新 schema 建库，触发完整迁移
    db1 = DatabaseManager(db_path)
    db1.close()

    # ② 手动还原为「旧库」状态：删 is_primary、加回 is_active、重建旧索引、写入数据
    raw = sqlite3.connect(db_path)
    raw.execute("DROP INDEX IF EXISTS idx_bangumi_accounts_primary")
    raw.execute("ALTER TABLE bangumi_accounts DROP COLUMN is_primary")
    raw.execute(
        "ALTER TABLE bangumi_accounts ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 0"
    )
    raw.execute(
        "CREATE INDEX idx_bangumi_accounts_active ON bangumi_accounts(is_active)"
    )
    raw.execute(
        "INSERT INTO bangumi_accounts "
        "(section_name, username, auth_method, is_active, enabled, created_at, updated_at) "
        "VALUES ('bangumi', 'u1', 'manual', 1, 1, 0, 0)"
    )
    raw.execute(
        "INSERT INTO bangumi_accounts "
        "(section_name, username, auth_method, is_active, enabled, created_at, updated_at) "
        "VALUES ('bangumi-b', 'u2', 'manual', 0, 1, 0, 0)"
    )
    raw.commit()
    raw.close()

    # ③ 用全新实例重新触发迁移
    db2 = DatabaseManager(db_path)
    try:
        accs = {a["section_name"]: a for a in db2.list_bangumi_accounts()}
        assert set(accs) == {"bangumi", "bangumi-b"}
        # 原 is_active=1 的账号现为首选
        assert db2.get_primary_bangumi_account()["section_name"] == "bangumi"
        assert accs["bangumi"]["is_primary"] is True
        assert accs["bangumi-b"]["is_primary"] is False
        # 新列存在、旧列已删除
        cur = db2._connection._get_connection().execute(
            "PRAGMA table_info(bangumi_accounts)"
        )
        cols = {row[1] for row in cur.fetchall()}
        assert "is_primary" in cols
        assert "is_active" not in cols
    finally:
        db2.close()


def test_fresh_install_schema_has_branch_columns(temp_dir, reset_singletons):
    """全新安装（未经旧库迁移）的数据库应直接具备首选 / 启用列与合理默认值。

    对照（实现前）：本分支在 ``bangumi_accounts`` 新增 ``is_primary`` / ``enabled``、
    ``sync_records`` 新增 ``account_results``。全新库由 CREATE TABLE 直接建出这些列，
    不应依赖迁移函数补齐；默认值须为 ``is_primary=0``、``enabled=1``、``account_results=''``。
    """
    from app.core.database import DatabaseManager

    db_path = str(temp_dir / "fresh.db")
    db = DatabaseManager(db_path)
    try:
        conn = db._connection._get_connection()
        acc_cols = {
            row[1]: (row[3], row[4])
            for row in conn.execute("PRAGMA table_info(bangumi_accounts)").fetchall()
        }
        assert "is_primary" in acc_cols
        assert "enabled" in acc_cols
        # PRAGMA table_info: 列序号 3 = notnull，列序号 4 = dflt_value
        assert acc_cols["is_primary"][0] == 1 and acc_cols["is_primary"][1] == "0"
        assert acc_cols["enabled"][0] == 1 and acc_cols["enabled"][1] == "1"

        rec_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(sync_records)").fetchall()
        }
        assert "account_results" in rec_cols
    finally:
        db.close()


def test_fresh_account_inherits_branch_defaults(
    temp_dir, reset_singletons, monkeypatch
):
    """全新库写入的账号应默认启用、非首选，且同步记录 account_results 默认为空。"""
    import app.core.accounts as accounts_mod
    from app.core.database import DatabaseManager

    db = DatabaseManager(str(temp_dir / "fresh_acc.db"))
    try:
        monkeypatch.setattr(accounts_mod, "database_manager", db)
        db.save_bangumi_account(
            {
                "section_name": "bangumi",
                "username": "u1",
                "media_server_usernames": ["Elegy233"],
                "auth_method": "manual",
                "access_token": "AT",
            }
        )
        acc = db.get_bangumi_account("bangumi")
        assert acc["enabled"] is True
        assert acc["is_primary"] is False
    finally:
        db.close()


def test_disabled_primary_keeps_primary_identity(
    temp_dir, reset_singletons, monkeypatch
):
    """停用首选账号不清除其首选身份，重新启用后首选身份随之恢复。

    停用只决定账号是否参与同步（写入路径经 ``get_bangumi_sections_for_user``
    剔除），``is_primary`` 标记本身保留：停用期间同步路由顺延到下一个启用
    账号，重新启用后仍是首选，无需重新设置。
    """
    import app.core.accounts as accounts_mod

    db = _accounts_db(temp_dir, _two_accounts_one_media_user())
    monkeypatch.setattr(accounts_mod, "database_manager", db)

    db.set_primary_bangumi_account("bangumi")
    assert db.get_primary_bangumi_account()["section_name"] == "bangumi"

    db.set_enabled_bangumi_account("bangumi", False)
    # 首选身份保留，不因停用而转交给其余账号
    assert db.get_primary_bangumi_account()["section_name"] == "bangumi"
    # 同步路由顺延到下一个启用账号
    assert accounts_mod.get_bangumi_config_for_user("Elegy233")["username"] == "u2"

    db.set_enabled_bangumi_account("bangumi", True)
    assert db.get_primary_bangumi_account()["section_name"] == "bangumi"
    assert accounts_mod.get_bangumi_config_for_user("Elegy233")["username"] == "u1"
