"""飞牛 reader 与 trimmedia 最小 schema"""

import sqlite3
from pathlib import Path

from app.services.feiniu.reader import (
    fetch_completed_watch_records,
    list_feiniu_users,
)


def _create_trimmedia_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    c.execute(
        """CREATE TABLE user (
        guid TEXT PRIMARY KEY,
        username TEXT,
        status INTEGER
    )"""
    )
    c.execute(
        """CREATE TABLE item (
        guid TEXT PRIMARY KEY,
        title TEXT,
        original_title TEXT,
        parent_guid TEXT,
        runtime INTEGER,
        episode_number INTEGER,
        season_number INTEGER
    )"""
    )
    c.execute(
        """CREATE TABLE item_user_play (
        item_guid TEXT,
        user_guid TEXT,
        watched INTEGER,
        ts INTEGER,
        create_time INTEGER,
        update_time INTEGER,
        visible INTEGER
    )"""
    )
    c.execute("INSERT INTO user VALUES ('u1', 'fnviewer', 1)")
    c.execute("INSERT INTO item VALUES ('ep1', '第2集', 'Ep2', NULL, 24, 2, 1)")
    now_ms = 1_700_000_000_000
    c.execute(
        "INSERT INTO item_user_play VALUES ('ep1', 'u1', 1, 1440, ?, ?, 1)",
        (now_ms, now_ms),
    )
    conn.commit()
    conn.close()


def test_list_feiniu_users_empty_path():
    assert list_feiniu_users("") == []
    assert list_feiniu_users("/nonexistent/trimmedia.db") == []


def test_list_and_fetch_completed(tmp_path):
    dbf = tmp_path / "trimmedia.db"
    _create_trimmedia_db(dbf)
    users = list_feiniu_users(str(dbf))
    assert len(users) == 1
    assert users[0].guid == "u1"
    assert users[0].username == "fnviewer"

    rows = fetch_completed_watch_records(
        str(dbf),
        user_guid="all",
        time_range="all",
        limit=50,
        min_percent=80,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.item_guid == "ep1"
    assert r.user_guid == "u1"
    assert r.username == "fnviewer"
    assert r.episode == 2
    assert r.season == 1
    assert r.display_title == "第2集"


def test_fetch_respects_min_percent(tmp_path):
    dbf = tmp_path / "t2.db"
    conn = sqlite3.connect(str(dbf))
    c = conn.cursor()
    c.execute(
        "CREATE TABLE user (guid TEXT PRIMARY KEY, username TEXT, status INTEGER)"
    )
    c.execute(
        """CREATE TABLE item (
        guid TEXT PRIMARY KEY, title TEXT, original_title TEXT,
        parent_guid TEXT, runtime INTEGER, episode_number INTEGER
    )"""
    )
    c.execute(
        """CREATE TABLE item_user_play (
        item_guid TEXT, user_guid TEXT, watched INTEGER, ts INTEGER,
        create_time INTEGER, update_time INTEGER, visible INTEGER
    )"""
    )
    c.execute("INSERT INTO user VALUES ('u1', 'a', 1)")
    c.execute("INSERT INTO item VALUES ('e1', '第1集', NULL, NULL, 24, 1)")
    # 24 分钟片长 → 1440 秒；ts=800 秒约 55% 视为已看（min_percent=5）
    c.execute("INSERT INTO item_user_play VALUES ('e1', 'u1', 0, 800, 1000, 1000, 1)")
    conn.commit()
    conn.close()

    low = fetch_completed_watch_records(
        str(dbf), user_guid="all", time_range="all", limit=10, min_percent=95
    )
    assert low == []

    high = fetch_completed_watch_records(
        str(dbf), user_guid="all", time_range="all", limit=10, min_percent=5
    )
    assert len(high) == 1


def test_fetch_respects_min_update_watermark(tmp_path):
    """仅 update_time >= 水位线的记录参与（与启用起点一致）"""
    dbf = tmp_path / "wm.db"
    conn = sqlite3.connect(str(dbf))
    c = conn.cursor()
    c.execute(
        "CREATE TABLE user (guid TEXT PRIMARY KEY, username TEXT, status INTEGER)"
    )
    c.execute(
        """CREATE TABLE item (
        guid TEXT PRIMARY KEY, title TEXT, original_title TEXT,
        parent_guid TEXT, runtime INTEGER, episode_number INTEGER
    )"""
    )
    c.execute(
        """CREATE TABLE item_user_play (
        item_guid TEXT, user_guid TEXT, watched INTEGER, ts INTEGER,
        create_time INTEGER, update_time INTEGER, visible INTEGER
    )"""
    )
    c.execute("INSERT INTO user VALUES ('u1', 'a', 1)")
    c.execute("INSERT INTO item VALUES ('old', '第1集', NULL, NULL, 24, 1)")
    c.execute("INSERT INTO item VALUES ('new', '第2集', NULL, NULL, 24, 2)")
    c.execute("INSERT INTO item_user_play VALUES ('old', 'u1', 1, 1440, 100, 100, 1)")
    c.execute("INSERT INTO item_user_play VALUES ('new', 'u1', 1, 1440, 5000, 5000, 1)")
    conn.commit()
    conn.close()

    wm = 2000
    rows = fetch_completed_watch_records(
        str(dbf),
        user_guid="all",
        time_range="all",
        limit=10,
        min_percent=1,
        min_update_time_ms=wm,
    )
    assert len(rows) == 1
    assert rows[0].item_guid == "new"


def test_fetch_respects_user_guid_filter(tmp_path):
    """user_guid 非 all 时只拉指定用户"""
    dbf = tmp_path / "uf.db"
    conn = sqlite3.connect(str(dbf))
    c = conn.cursor()
    c.execute(
        "CREATE TABLE user (guid TEXT PRIMARY KEY, username TEXT, status INTEGER)"
    )
    c.execute(
        """CREATE TABLE item (
        guid TEXT PRIMARY KEY, title TEXT, original_title TEXT,
        parent_guid TEXT, runtime INTEGER, episode_number INTEGER
    )"""
    )
    c.execute(
        """CREATE TABLE item_user_play (
        item_guid TEXT, user_guid TEXT, watched INTEGER, ts INTEGER,
        create_time INTEGER, update_time INTEGER, visible INTEGER
    )"""
    )
    c.execute("INSERT INTO user VALUES ('u1', 'a', 1)")
    c.execute("INSERT INTO user VALUES ('u2', 'b', 1)")
    c.execute("INSERT INTO item VALUES ('e1', '第1集', NULL, NULL, 24, 1)")
    c.execute("INSERT INTO item VALUES ('e2', '第1集', NULL, NULL, 24, 1)")
    c.execute("INSERT INTO item_user_play VALUES ('e1', 'u1', 1, 1440, 1, 1, 1)")
    c.execute("INSERT INTO item_user_play VALUES ('e2', 'u2', 1, 1440, 2, 2, 1)")
    conn.commit()
    conn.close()

    only_u2 = fetch_completed_watch_records(
        str(dbf),
        user_guid="u2",
        time_range="all",
        limit=10,
        min_percent=1,
    )
    assert len(only_u2) == 1
    assert only_u2[0].item_guid == "e2"
    assert only_u2[0].user_guid == "u2"
