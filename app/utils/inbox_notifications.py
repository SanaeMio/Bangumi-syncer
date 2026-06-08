"""收件箱通知聚合辅助。"""

from __future__ import annotations

import re
from typing import Any

_SYNC_FAIL_RE = re.compile(r"^同步失败：(.+?) (S\d+E\d+|剧场版)$")


def notification_group_key(title: str) -> str:
    """同一番剧的多条同步失败通知归为同一组。"""
    t = (title or "").strip()
    match = _SYNC_FAIL_RE.match(t)
    if match:
        return match.group(1).strip()
    return t


def aggregated_notification_title(latest_title: str, count: int) -> str:
    if count <= 1:
        return latest_title
    show = notification_group_key(latest_title)
    return f"同步失败：{show}（{count} 条）"


def aggregate_notification_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将通知行按番剧聚合（rows 须已按 id 降序）。"""
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        key = notification_group_key(str(row.get("title") or ""))
        nid = int(row["id"])
        if key not in groups:
            groups[key] = {
                "ids": [nid],
                "latest": row,
                "count": 1,
                "unread": row.get("read_at") is None,
            }
            order.append(key)
        else:
            group = groups[key]
            group["count"] += 1
            group["ids"].append(nid)
            if row.get("read_at") is None:
                group["unread"] = True
    out: list[dict[str, Any]] = []
    for key in order:
        group = groups[key]
        latest = group["latest"]
        out.append(
            {
                "id": group["ids"][0],
                "type": str(latest.get("type") or "sync_failed"),
                "title": aggregated_notification_title(
                    str(latest.get("title") or ""), group["count"]
                ),
                "body": str(latest.get("body") or ""),
                "ref_id": latest.get("ref_id"),
                "created_at": str(latest.get("created_at") or ""),
                "read": not group["unread"],
                "count": group["count"],
                "notification_ids": group["ids"],
            }
        )
    return out
