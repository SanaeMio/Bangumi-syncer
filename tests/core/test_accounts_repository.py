"""
BangumiAccountRepository / OAuthStateRepository 测试。
"""

from unittest.mock import patch

import pytest


@pytest.fixture
def db(temp_dir, reset_singletons):
    with patch("app.core.database.logger"):
        from app.core.database import DatabaseManager

        db_path = temp_dir / "accounts.db"
        yield DatabaseManager(str(db_path))


def _sample_account(section="bangumi-alpha", username="user_a"):
    return {
        "section_name": section,
        "username": username,
        "media_server_usernames": ["plex-a", "emby-a"],
        "auth_method": "manual",
        "access_token": "AT",
        "refresh_token": "RT",
        "token_type": "Bearer",
        "expires_at": 9999999999,
        "bangumi_user_id": "123",
        "nickname": "小A",
        "avatar": "http://x/y.png",
        "is_active": False,
    }


class TestBangumiAccountRepository:
    def test_save_and_get(self, db):
        acc = _sample_account()
        assert db.save_bangumi_account(acc) is True
        got = db.get_bangumi_account("bangumi-alpha")
        assert got is not None
        assert got["username"] == "user_a"
        assert got["media_server_usernames"] == ["plex-a", "emby-a"]
        assert got["access_token"] == "AT"
        assert got["is_active"] is False

    def test_upsert_by_section(self, db):
        db.save_bangumi_account(_sample_account())
        updated = _sample_account()
        updated["username"] = "user_a2"
        updated["access_token"] = "NEW"
        assert db.save_bangumi_account(updated) is True
        # 仍只一条记录
        assert db.count_bangumi_accounts() == 1
        got = db.get_bangumi_account("bangumi-alpha")
        assert got["username"] == "user_a2"
        assert got["access_token"] == "NEW"

    def test_list_accounts(self, db):
        db.save_bangumi_account(_sample_account("bangumi-a", "ua"))
        db.save_bangumi_account(_sample_account("bangumi-b", "ub"))
        assert db.count_bangumi_accounts() == 2
        accounts = db.list_bangumi_accounts()
        assert [a["section_name"] for a in accounts] == ["bangumi-a", "bangumi-b"]

    def test_active_defaults_to_first(self, db):
        db.save_bangumi_account(_sample_account("bangumi-a", "ua"))
        db.save_bangumi_account(_sample_account("bangumi-b", "ub"))
        active = db.get_active_bangumi_account()
        assert active["section_name"] == "bangumi-a"

    def test_set_active(self, db):
        db.save_bangumi_account(_sample_account("bangumi-a", "ua"))
        db.save_bangumi_account(_sample_account("bangumi-b", "ub"))
        db.set_active_bangumi_account("bangumi-b")
        active = db.get_active_bangumi_account()
        assert active["section_name"] == "bangumi-b"
        # 其余应非激活
        others = [
            a for a in db.list_bangumi_accounts() if a["section_name"] != "bangumi-b"
        ]
        assert all(not a["is_active"] for a in others)

    def test_update_token(self, db):
        db.save_bangumi_account(_sample_account())
        ok = db.update_bangumi_account_token(
            "bangumi-alpha",
            {
                "access_token": "AT2",
                "refresh_token": "RT2",
                "token_type": "Bearer",
                "expires_at": 1234567890,
                "bangumi_user_id": "456",
                "nickname": "小A2",
                "avatar": "http://z/w.png",
            },
        )
        assert ok is True
        got = db.get_bangumi_account("bangumi-alpha")
        assert got["access_token"] == "AT2"
        assert got["refresh_token"] == "RT2"
        assert got["auth_method"] == "oauth"
        assert got["bangumi_user_id"] == "456"

    def test_update_token_unknown_section(self, db):
        assert db.update_bangumi_account_token("nope", {"access_token": "x"}) is False

    def test_delete_account(self, db):
        db.save_bangumi_account(_sample_account())
        assert db.delete_bangumi_account("bangumi-alpha") is True
        assert db.count_bangumi_accounts() == 0
        assert db.get_bangumi_account("bangumi-alpha") is None

    def test_media_server_usernames_csv_normalization(self, db):
        acc = _sample_account()
        acc["media_server_usernames"] = "plex-x, emby-x"
        db.save_bangumi_account(acc)
        got = db.get_bangumi_account("bangumi-alpha")
        assert got["media_server_usernames"] == ["plex-x", "emby-x"]


class TestOAuthStateRepository:
    def test_save_and_get(self, db):
        assert db.save_oauth_state("st1", "bangumi-alpha", 9999999999) is True
        rec = db.get_oauth_state("st1")
        assert rec is not None
        assert rec["section_name"] == "bangumi-alpha"

    def test_expired_state_returns_none(self, db):
        assert db.save_oauth_state("st2", "bangumi-alpha", 100) is True
        rec = db.get_oauth_state("st2")
        assert rec is None

    def test_delete_state(self, db):
        db.save_oauth_state("st3", "bangumi-alpha", 9999999999)
        assert db.delete_oauth_state("st3") is True
        assert db.get_oauth_state("st3") is None

    def test_cleanup_expired(self, db):
        db.save_oauth_state("live", "bangumi-alpha", 9999999999)
        db.save_oauth_state("dead", "bangumi-alpha", 100)
        deleted = db.cleanup_oauth_states_expired()
        assert deleted >= 1
        assert db.get_oauth_state("live") is not None
        assert db.get_oauth_state("dead") is None
