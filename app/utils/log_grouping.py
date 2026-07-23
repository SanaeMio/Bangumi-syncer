"""
同步日志分组解析：按 [run:xxx] 聚合，历史日志启发式回退。
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any

RUN_ID_RE = re.compile(r"\[run:([^\]]+)\]")
TIMESTAMP_RE = re.compile(r"^\[(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]")
LEVEL_RE = re.compile(r"\[(DEBUG|INFO ?|WARN(?:ING)? ?|ERROR)\]")

SYNC_START_RE = re.compile(
    r"接收到同步请求|同步开始:|同步任务 .+ 已提交到异步队列|"
    r"(?:Plex|Emby|Jellyfin)同步任务 .+ 已提交到异步队列|"
    r"重试同步记录 \d+:"
)
SYNC_END_RE = re.compile(
    r"同步结束:|bgm:.*(已标记|跳过|归档|已在看)|"
    r"异步(?:Plex|Emby|Jellyfin)?同步任务 .+ 失败|自定义同步处理出错"
)
LOGIN_ORPHAN_RE = re.compile(
    r"登录成功|登录失败|被锁定|登出|清理过期会话|密码更新完成|多账号配置处理完成"
)

_TITLE_QUOTED_RE = re.compile(r"title='([^']*)'")
_SE_EP_RE = re.compile(r"\bS(\d+)E(\d+)\b")
_SYNC_START_TITLE_RE = re.compile(r"同步开始: (.+) S(\d+)E(\d+)")
_RETRY_TITLE_RE = re.compile(r"重试同步记录 \d+: (.+?) S(\d+)E(\d+)")
_SOURCE_QUOTED_RE = re.compile(r"source='([^']+)'")
_SOURCE_PAREN_RE = re.compile(r"\(([^)]+)\)\s*$")


def _extract_timestamp(line: str) -> str | None:
    m = TIMESTAMP_RE.match(line)
    return m.group(1) if m else None


def _extract_level(line: str) -> str | None:
    m = LEVEL_RE.search(line)
    if not m:
        return None
    raw = m.group(1).strip()
    if raw == "WARN":
        return "WARNING"
    return raw


def _is_login_orphan(line: str) -> bool:
    return bool(LOGIN_ORPHAN_RE.search(line))


def _parse_timestamp_dt(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y/%m/%d %H:%M:%S.%f")
    except ValueError:
        return None


def _duration_ms(timestamps: list[str]) -> int | None:
    if not timestamps:
        return None
    start = _parse_timestamp_dt(timestamps[0])
    end = _parse_timestamp_dt(timestamps[-1])
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _parse_title_info(lines: list[str]) -> dict[str, Any]:
    title = ""
    season = 0
    episode = 0
    source = ""
    for line in lines:
        m = _SYNC_START_TITLE_RE.search(line)
        if m:
            title = m.group(1).strip()
            season = int(m.group(2))
            episode = int(m.group(3))
        m2 = _RETRY_TITLE_RE.search(line)
        if m2:
            title = m2.group(1).strip()
            season = int(m2.group(2))
            episode = int(m2.group(3))
        for tm in _TITLE_QUOTED_RE.finditer(line):
            candidate = tm.group(1).strip()
            if candidate and (not title or len(candidate) > len(title)):
                title = candidate
        sm = _SE_EP_RE.search(line)
        if sm and not season:
            season = int(sm.group(1))
            episode = int(sm.group(2))
        sq = _SOURCE_QUOTED_RE.search(line)
        if sq:
            source = sq.group(1)
        if "同步开始:" in line or "重试同步记录" in line:
            sp = _SOURCE_PAREN_RE.search(line)
            if sp:
                source = sp.group(1).strip()
    return {"title": title, "season": season, "episode": episode, "source": source}


def _looks_like_sync_group(lines: list[str], info: dict[str, Any]) -> bool:
    """可识别为一次同步的组；否则归入其他系统日志。"""
    if info.get("title"):
        return True
    if any(SYNC_START_RE.search(line) for line in lines):
        return True
    if any(
        keyword in line
        for line in lines
        for keyword in ("接收到同步请求", "同步结束:", "bgm:", "重试同步记录")
    ):
        return True
    return False


def _infer_status(lines: list[str]) -> str:
    for line in reversed(lines):
        if "同步结束:" in line:
            if "status=success" in line:
                return "success"
            if "status=ignored" in line:
                return "ignored"
            if "status=error" in line:
                return "error"
        if "ERROR" in line or "失败" in line or "自定义同步处理出错" in line:
            return "error"
        if "bgm:" in line and ("已标记" in line or "已在看" in line or "归档" in line):
            return "success"
        if "跳过" in line and "bgm:" in line:
            return "ignored"
    return "unknown"


def _count_levels(lines: list[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for line in lines:
        level = _extract_level(line)
        if level:
            counts[level] += 1
    return dict(counts)


def _build_group(
    run_id: str,
    lines: list[str],
    *,
    ambiguous: bool = False,
    truncated: bool = False,
) -> dict[str, Any]:
    info = _parse_title_info(lines)
    timestamps = [t for line in lines if (t := _extract_timestamp(line))]
    duration = _duration_ms(timestamps)
    return {
        "run_id": run_id,
        "title": info["title"],
        "season": info["season"],
        "episode": info["episode"],
        "source": info["source"],
        "status": _infer_status(lines),
        "start_time": timestamps[0] if timestamps else "",
        "end_time": timestamps[-1] if timestamps else "",
        "duration_ms": duration,
        "line_count": len(lines),
        "level_counts": _count_levels(lines),
        "ambiguous": ambiguous,
        "truncated": truncated,
        "lines": lines,
    }


def _heuristic_group_orphans(
    orphan_lines: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """无 run_id 行的启发式分组；登录相关行始终保留为 orphan。"""
    remaining: list[str] = []
    login_orphans: list[str] = []
    for line in orphan_lines:
        if _is_login_orphan(line):
            login_orphans.append(line)
        else:
            remaining.append(line)

    groups: list[dict[str, Any]] = []
    current_lines: list[str] = []
    open_groups = 0
    ambiguous = False

    for line in remaining:
        is_start = bool(SYNC_START_RE.search(line))
        is_end = bool(SYNC_END_RE.search(line))

        if is_start:
            if current_lines:
                groups.append(
                    _build_group(
                        f"heuristic_{len(groups)}",
                        current_lines,
                        ambiguous=open_groups > 0,
                    )
                )
                if open_groups > 0:
                    ambiguous = True
            current_lines = [line]
            open_groups += 1
            if is_end:
                groups.append(
                    _build_group(
                        f"heuristic_{len(groups)}",
                        current_lines,
                        ambiguous=open_groups > 1,
                    )
                )
                current_lines = []
                open_groups = max(0, open_groups - 1)
        elif current_lines:
            current_lines.append(line)
            if is_end:
                groups.append(
                    _build_group(
                        f"heuristic_{len(groups)}",
                        current_lines,
                        ambiguous=open_groups > 1,
                    )
                )
                current_lines = []
                open_groups = max(0, open_groups - 1)
        else:
            login_orphans.append(line)

    if current_lines:
        groups.append(
            _build_group(
                f"heuristic_{len(groups)}",
                current_lines,
                ambiguous=True,
            )
        )

    if ambiguous:
        for g in groups:
            if g["run_id"].startswith("heuristic_"):
                g["ambiguous"] = True

    return groups, login_orphans


def group_log_lines(
    lines: list[str],
    *,
    truncated_run_ids: set[str] | None = None,
) -> dict[str, Any]:
    """
    将日志行按 run_id 分组。

    返回 {"groups": [...], "orphans": [...]}。
    """
    truncated_run_ids = truncated_run_ids or set()
    by_run: dict[str, list[str]] = defaultdict(list)
    no_run_lines: list[str] = []

    for line in lines:
        stripped = line.rstrip("\n")
        if not stripped:
            continue
        m = RUN_ID_RE.search(stripped)
        if m:
            by_run[m.group(1).strip()].append(stripped)
        else:
            no_run_lines.append(stripped)

    groups: list[dict[str, Any]] = []
    for run_id, run_lines in by_run.items():
        groups.append(
            _build_group(
                run_id,
                run_lines,
                truncated=run_id in truncated_run_ids,
            )
        )

    heuristic_groups, orphans = _heuristic_group_orphans(no_run_lines)
    groups.extend(heuristic_groups)

    recognized: list[dict[str, Any]] = []
    for group in groups:
        info = {
            "title": group.get("title"),
            "season": group.get("season"),
            "episode": group.get("episode"),
        }
        if _looks_like_sync_group(group.get("lines", []), info):
            recognized.append(group)
        else:
            orphans.extend(group.get("lines", []))

    recognized.sort(key=lambda g: g.get("start_time") or "")

    return {"groups": recognized, "orphans": orphans}
