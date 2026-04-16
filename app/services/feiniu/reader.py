"""
只读读取飞牛影视 trimmedia.db（item_user_play + item）。
逻辑参考社区常见挂载方式，表结构随飞牛版本可能变化，故做列探测与降级。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ...core.logging import logger
from .models import FeiniuUser, FeiniuWatchRecord

_EPISODE_TITLE_RE = re.compile(r"(?:第|E|ep)\s*0*(\d+)(?:集|话|話)?", re.IGNORECASE)


def _sqlite_ro_uri(db_path: str) -> str:
    p = Path(db_path).expanduser().resolve()
    base = p.as_uri()
    return f"{base}?mode=ro"


def _table_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {str(row[1]).lower() for row in cursor.fetchall()}


def _pick_episode_sql_column(cols: set[str]) -> str:
    for name in ("episode_number", "index_number", "episode_index", "sort_index"):
        if name in cols:
            return f"i.{name}"
    return "NULL"


def _pick_season_value(row: sqlite3.Row, cols: set[str]) -> int:
    for key in ("season_number", "parent_index_number", "season_index"):
        if key in cols:
            try:
                v = row[key]
                if v is not None:
                    return max(1, int(v))
            except (TypeError, ValueError):
                pass
    return 1


def list_feiniu_users(db_path: str) -> list[FeiniuUser]:
    if not db_path or not Path(db_path).is_file():
        return []
    try:
        conn = sqlite3.connect(_sqlite_ro_uri(db_path), uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT guid, username FROM user
            WHERE status = 1 AND guid != 'default-user-template'
            ORDER BY username
            """
        )
        users = [
            FeiniuUser(
                guid=str(r["guid"]), username=str(r["username"] or r["guid"][:8])
            )
            for r in cur.fetchall()
        ]
        conn.close()
        return users
    except Exception as e:
        logger.warning(f"读取飞牛用户列表失败: {e}")
        return []


def _time_range_cutoff_ms(time_range: str) -> int | None:
    now = datetime.now()
    if time_range == "1day":
        delta = timedelta(days=1)
    elif time_range == "1week":
        delta = timedelta(days=7)
    elif time_range == "1month":
        delta = timedelta(days=30)
    else:
        return None
    return int((now - delta).timestamp() * 1000)


def _normalize_to_epoch_seconds(ts: Any) -> float:
    """飞牛时间戳多为毫秒；小数值按秒处理。"""
    if ts is None:
        return 0.0
    try:
        v = float(ts)
    except (TypeError, ValueError):
        return 0.0
    if v > 1_000_000_000_000:
        return v / 1000.0
    return v


def _real_series_title(row: sqlite3.Row, item_guid: str) -> str:
    base = (row["media_title"] or row["media_original_title"] or "") or ""
    p1 = row["p1_title"] or ""
    p2 = row["p2_title"] or ""
    real = base
    if p2 and p1:
        real = p1 if p2.lower() in p1.lower() else f"{p2} {p1}"
    elif p2:
        real = p2
    elif p1:
        real = p1
    if not real:
        real = f"视频-{str(item_guid)[:8]}"
    return str(real)


def fetch_completed_watch_records(
    db_path: str,
    *,
    user_guid: str = "all",
    time_range: str = "all",
    limit: int = 100,
    min_percent: int = 80,
    min_update_time_ms: int | None = None,
) -> list[FeiniuWatchRecord]:
    """
    返回按 update_time 倒序的观看行，且满足：
    - visible = 1
    - watched = 1 或 播放进度 >= min_percent
    """
    if not db_path or not Path(db_path).is_file():
        return []

    try:
        conn = sqlite3.connect(_sqlite_ro_uri(db_path), uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        item_cols = _table_columns(cur, "item")
        ep_col = _pick_episode_sql_column(item_cols)
        season_fragments: list[str] = []
        for c in ("season_number", "parent_index_number", "season_index"):
            if c in item_cols:
                season_fragments.append(f"i.{c} AS {c}")
        season_sql = (
            (",\n               " + ",\n               ".join(season_fragments))
            if season_fragments
            else ""
        )

        query = f"""
        SELECT p.item_guid, p.user_guid, p.watched, p.ts, p.create_time, p.update_time,
               i.title AS media_title, i.original_title AS media_original_title,
               i.runtime AS runtime_mins,
               p1.title AS p1_title, p2.title AS p2_title,
               p1.runtime AS p1_runtime, p2.runtime AS p2_runtime,
               {ep_col} AS ep_number,
               COALESCE(NULLIF(TRIM(u.username), ''), substr(p.user_guid, 1, 8)) AS feiniu_username
               {season_sql}
        FROM item_user_play p
        LEFT JOIN item i ON p.item_guid = i.guid
        LEFT JOIN item p1 ON i.parent_guid = p1.guid
        LEFT JOIN item p2 ON p1.parent_guid = p2.guid
        LEFT JOIN user u ON p.user_guid = u.guid
        WHERE p.visible = 1
        """
        params: list[Any] = []

        if user_guid and user_guid != "all":
            query += " AND p.user_guid = ?"
            params.append(user_guid)

        cutoffs: list[int] = []
        tr_cutoff = _time_range_cutoff_ms(time_range)
        if tr_cutoff is not None:
            cutoffs.append(int(tr_cutoff))
        if min_update_time_ms is not None:
            cutoffs.append(int(min_update_time_ms))
        if cutoffs:
            query += " AND p.update_time >= ?"
            params.append(max(cutoffs))

        query += " ORDER BY p.update_time DESC LIMIT ?"
        params.append(limit)

        cur.execute(query, params)
        rows_out: list[FeiniuWatchRecord] = []

        for r in cur.fetchall():
            ts_raw = r["update_time"] or r["create_time"] or 0
            wall_sec = _normalize_to_epoch_seconds(ts_raw)
            play_sec = int(r["ts"] or 0)
            watched = int(r["watched"] or 0) == 1

            runtime_mins = r["runtime_mins"]
            if not runtime_mins:
                runtime_mins = r["p1_runtime"]
            if not runtime_mins:
                runtime_mins = r["p2_runtime"]
            runtime_mins = int(runtime_mins or 24)
            total_sec = max(runtime_mins * 60, 1)

            if watched:
                percent = 100.0
            else:
                percent = round(max(0.0, min(100.0, (play_sec / total_sec) * 100.0)), 1)

            finished = watched or percent >= float(min_percent)
            if not finished:
                continue

            base_name = (r["media_title"] or r["media_original_title"] or "") or ""
            ep_num = 1
            en = r["ep_number"]
            if en is not None:
                try:
                    ep_num = max(1, int(en))
                except (TypeError, ValueError):
                    ep_num = 1
            else:
                m = _EPISODE_TITLE_RE.search(base_name)
                if m:
                    ep_num = max(1, int(m.group(1)))

            real_name = _real_series_title(r, str(r["item_guid"]))
            season = _pick_season_value(r, item_cols)

            if wall_sec > 0:
                release_date = datetime.fromtimestamp(wall_sec).strftime("%Y-%m-%d")
            else:
                release_date = datetime.now().strftime("%Y-%m-%d")

            ug = str(r["user_guid"])
            uname = str(r["feiniu_username"] or (ug[:8] if ug else "unknown"))

            ori = r["media_original_title"]
            ori_s = str(ori) if ori else None

            rows_out.append(
                FeiniuWatchRecord(
                    item_guid=str(r["item_guid"]),
                    user_guid=ug,
                    username=uname,
                    display_title=real_name,
                    original_title=ori_s,
                    season=season,
                    episode=ep_num,
                    release_date=release_date,
                    update_time_ms=int(ts_raw) if ts_raw else 0,
                )
            )

        conn.close()
        return rows_out
    except Exception as e:
        logger.error(f"读取飞牛观看记录失败: {e}")
        return []
