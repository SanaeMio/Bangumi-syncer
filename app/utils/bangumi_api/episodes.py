"""BangumiApi 章集解析（mixin）"""

from __future__ import annotations

import datetime
import re
from typing import Any

from ...core.config import config_manager
from ...core.logging import logger

_EPISODES_PAGE_LIMIT = 200
_LONG_SERIES_AIRDATE_MIN_TOTAL = 100

_CN_NUM = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


class EpisodesMixin:
    """章节/集数解析相关方法（供 BangumiApi 组合）"""

    @staticmethod
    def _get_episode_sync_limits() -> tuple[int, int]:
        try:
            return config_manager.get_episode_sync_limits()
        except Exception:
            return 100, 9999

    def _fetch_episodes_page(
        self,
        subject_id: int,
        _type: int = 0,
        *,
        limit: int = _EPISODES_PAGE_LIMIT,
        offset: int = 0,
    ) -> dict:
        """单次分页请求章节列表（不写入实例缓存）。"""
        res = self.get(
            "episodes",
            params={
                "subject_id": subject_id,
                "type": _type,
                "limit": limit,
                "offset": offset,
            },
        )
        try:
            payload = res.json()
            if not isinstance(payload, dict):
                logger.error(
                    f"get_episodes API返回非字典类型: {type(payload)}, 内容: {payload}"
                )
                return {"data": [], "total": 0}
            return payload
        except Exception as e:
            logger.error(f"get_episodes JSON解析失败: {e}")
            return {"data": [], "total": 0}

    def get_episodes(
        self,
        subject_id: int,
        _type: int = 0,
        fetch_all: bool = False,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        # 使用实例缓存避免内存泄漏
        cache_key = (subject_id, _type, fetch_all)
        if cache_key in self._cache["get_episodes"]:
            return self._cache["get_episodes"][cache_key]

        if not fetch_all:
            result = self._fetch_episodes_page(subject_id, _type)
        else:
            all_data: list = []
            offset = 0
            total = 0
            while True:
                page = self._fetch_episodes_page(
                    subject_id, _type, limit=_EPISODES_PAGE_LIMIT, offset=offset
                )
                batch = page.get("data") or []
                all_data.extend(batch)
                total = int(page.get("total") or len(all_data))
                if len(batch) < _EPISODES_PAGE_LIMIT or len(all_data) >= total:
                    break
                offset += _EPISODES_PAGE_LIMIT
            result = {"data": all_data, "total": total}

        self._put_cache("get_episodes", cache_key, result)
        return result

    def _find_episode_by_sort(
        self, subject_id: int, target_sort: int, _type: int = 0
    ) -> dict | None:
        """在 subject 内按 sort/ep 规则查找章节；ep>99 时优先 offset 快速路径。"""
        if target_sort > 99:
            page = self._fetch_episodes_page(
                subject_id, _type, limit=1, offset=target_sort - 1
            )
            data = page.get("data") or []
            if data and data[0].get("sort") == target_sort:
                logger.debug(
                    f"offset 快速路径命中 sort={target_sort} subject_id={subject_id}"
                )
                return data[0]

        episodes = self.get_episodes(subject_id, _type, fetch_all=target_sort > 99)
        ep_info = episodes.get("data") or []
        rows = self._match_target_ep_rows(ep_info, target_sort)
        return rows[0] if rows else None

    def _resolve_episode_by_airdate_in_subject(
        self,
        subject_id: str | int,
        release_date: str,
        max_days_diff: int = 120,
        min_total: int = _LONG_SERIES_AIRDATE_MIN_TOTAL,
    ) -> tuple[str | int, str | int] | None:
        """
        在同一 Bangumi subject 内按 airdate 与 release_date 择优（TVDB 多季 + Bangumi 单条目）。
        仅在条目章节总数达到 min_total 时启用，避免误用于普通季番。
        """
        target_day = self._parse_iso_date_ymd(release_date)
        if not target_day:
            return None

        episodes = self.get_episodes(subject_id, fetch_all=True)
        total = int(episodes.get("total") or 0)
        if total < min_total:
            return None

        ep_info = episodes.get("data") or []
        candidates: list[tuple[dict, int]] = []
        for ep in ep_info:
            if ep.get("type", 0) != 0 and "type" in ep:
                continue
            air_raw = (ep.get("airdate") or "").strip()
            ep_day = self._parse_iso_date_ymd(air_raw)
            if not ep_day:
                continue
            diff_days = abs((ep_day - target_day).days)
            if diff_days <= max_days_diff:
                candidates.append((ep, diff_days))

        if not candidates:
            return None

        best_ep, best_diff = min(candidates, key=lambda x: x[1])
        logger.debug(
            f"单条目 airdate 择优: subject_id={subject_id} ep_id={best_ep['id']} "
            f"与播出日相差 {best_diff} 天"
        )
        return subject_id, best_ep["id"]

    def _episode_lookup_failed(
        self,
        subject_id: int,
        target_ep: int,
        release_date: str | None,
    ) -> int | None:
        """季集匹配失败后的统一回退：单条目 airdate 择优。"""
        if release_date and target_ep:
            air_pick = self._resolve_episode_by_airdate_in_subject(
                subject_id, release_date
            )
            if air_pick is not None:
                return air_pick
        return None, None if target_ep else None

    @staticmethod
    def _parse_iso_date_ymd(value: str | None) -> datetime.date | None:
        if not value:
            return None
        m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", value.strip())
        if not m:
            return None
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    def _sequel_next_tv_subject_id(self, current_id: str | int) -> int | None:
        related = self.get_related_subjects(current_id)
        if isinstance(related, list):
            nxt = [i for i in related if i.get("relation") == "续集"]
        elif isinstance(related, dict):
            related_list = related.get("data", [])
            nxt = [i for i in related_list if i.get("relation") == "续集"]
        else:
            nxt = []
        if not nxt:
            return None
        return nxt[0]["id"]

    def _extract_season_number(self, name: str, name_cn: str) -> int | None:
        """从名称中提取季度编号，用于续集链季度去重计数"""
        text = f"{name} {name_cn}"
        # "第X期" / "第X季"（阿拉伯数字）
        m = re.search(r"第\s*(\d+)\s*[期季]", text)
        if m:
            return int(m.group(1))
        # "第X期" / "第X季"（中文数字）
        m = re.search(r"第\s*([一二三四五六七八九十]+)\s*[期季]", text)
        if m:
            cn = m.group(1)
            if len(cn) == 1:
                return _CN_NUM.get(cn)
            # "十一"~"十九"
            if cn.startswith("十"):
                return 10 + _CN_NUM.get(cn[1], 0)
            return _CN_NUM.get(cn)
        # "Xnd/Xrd/Xth season"
        m = re.search(r"(\d+)(?:st|nd|rd|th)\s+season", text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None

    def _match_target_ep_rows(
        self, ep_info: list, target_ep: int
    ) -> dict[str, Any] | None:
        """与 target_season>1 分支一致的章节匹配规则。"""
        rows = [i for i in ep_info if i.get("sort") == target_ep]
        if not rows:
            rows = [
                i
                for i in ep_info
                if i.get("ep") == target_ep and i.get("ep", 0) <= i.get("sort", 0)
            ]
        return rows

    def get_movie_main_episode_id(
        self,
        subject_id: str | int,
        target_sort: int = 1,
    ) -> tuple[str | None, str | None]:
        """
        剧场版 / 独立电影：在同一 subject 下解析本篇章节，不走续集链。
        返回 (subject_id 字符串, episode_id 字符串)；无章节时 episode_id 为 None。
        """
        sid = str(subject_id)
        episodes = self.get_episodes(subject_id)
        ep_info: list = episodes.get("data") or []
        if not ep_info:
            logger.debug(
                f"get_movie_main_episode_id: 无章节数据 subject_id={subject_id}"
            )
            return sid, None

        has_type = any("type" in e for e in ep_info)
        pool = [e for e in ep_info if e.get("type") == 0] if has_type else list(ep_info)
        if not pool:
            pool = list(ep_info)

        rows = self._match_target_ep_rows(pool, target_sort)
        if rows:
            return sid, str(rows[0]["id"])

        def _sort_key(e: dict) -> tuple:
            s = e.get("sort")
            return (s is None, s if s is not None else 9999)

        pool_sorted = sorted(pool, key=_sort_key)
        if pool_sorted:
            return sid, str(pool_sorted[0]["id"])
        return sid, None

    def _try_resolve_sequel_by_airdate(
        self,
        subject_id: str | int,
        target_ep: int,
        release_date: str,
        max_hops: int = 15,
        max_days_diff: int = 120,
        root_type: int | None = None,
    ) -> tuple[str | int, str | int] | None:
        """
        沿「续集」链查找与 release_date 最接近的 target_ep 章节（用于 Plex 季数与 Bangumi 分段不一致）。
        仅在存在有效 airdate 且与播出日差距不超过 max_days_diff 时返回。
        """
        target_day = self._parse_iso_date_ymd(release_date)
        if not target_day:
            return None

        candidates: list[
            tuple[str | int, str | int, int, int]
        ] = []  # sid, ep_id, diff_days, hop
        current_id: str | int = subject_id
        for hop in range(max_hops):
            nxt = self._sequel_next_tv_subject_id(current_id)
            if nxt is None:
                break
            current_id = nxt
            current_info = self.get_subject(current_id)
            if not current_info:
                continue
            if root_type is not None and current_info.get("type") != root_type:
                continue
            episodes = self.get_episodes(current_id)
            ep_info = episodes.get("data", [])
            if not ep_info:
                continue
            rows = self._match_target_ep_rows(ep_info, target_ep)
            if not rows:
                continue
            air_raw = (rows[0].get("airdate") or "").strip()
            ep_day = self._parse_iso_date_ymd(air_raw)
            if not ep_day:
                continue
            diff_days = abs((ep_day - target_day).days)
            candidates.append((current_id, rows[0]["id"], diff_days, hop))

        if not candidates:
            return None
        # 日期差最小；并列时取续集链更靠后的条目（通常更新）
        best = min(candidates, key=lambda x: (x[2], -x[3]))
        if best[2] > max_days_diff:
            return None
        logger.debug(
            f"按 airdate 择优续集链匹配: subject_id={best[0]} ep_id={best[1]} "
            f"与播出日相差 {best[2]} 天"
        )
        return best[0], best[1]

    def get_target_season_episode_id(
        self,
        subject_id: int,
        target_season: int,
        target_ep: int,
        is_season_subject_id: bool = False,
        release_date: str | None = None,
    ) -> int | None:
        max_season, max_episode = self._get_episode_sync_limits()

        if target_season > max_season or (target_ep and target_ep > max_episode):
            return None, None if target_ep else None

        # 获取根条目的 subject type，续集链遍历时仅放行相同媒体类型的条目
        root_info = self.get_subject(subject_id)
        root_type = root_info.get("type") if root_info else None

        # 如果已经是目标季数的ID，直接尝试匹配集数
        if is_season_subject_id:
            logger.debug(
                f"直接尝试从指定季度ID匹配集数: {subject_id}, 目标季度: {target_season}, 目标集数: {target_ep}"
            )
            if not target_ep:
                return subject_id

            found = self._find_episode_by_sort(subject_id, target_ep)
            if found:
                return subject_id, found["id"]

            logger.debug(
                f"在指定季度ID中未找到匹配的集数: {subject_id}, 目标集数: {target_ep}"
            )
            logger.debug("回退到传统方式查找集数")

        if target_season == 1:
            if not target_ep:
                return subject_id
            return self._find_season_one_episode(
                subject_id, target_ep, root_type, release_date
            )

        # Plex 季数与 Bangumi 多期/续集计数不一致时，用播出日 + 章节 airdate 择优
        if release_date and target_season > 1 and target_ep:
            air_pick = self._try_resolve_sequel_by_airdate(
                subject_id, target_ep, release_date, root_type=root_type
            )
            if air_pick is not None:
                return air_pick[0], air_pick[1]

        return self._find_multi_season_episode(
            subject_id, target_season, target_ep, root_type, release_date
        )

    def _find_next_sequel_id(self, current_id: int) -> int | None:
        """从关联条目中查找续集 subject_id，无则返回 None"""
        related = self.get_related_subjects(current_id)
        if isinstance(related, list):
            next_id = [i for i in related if i.get("relation") == "续集"]
        elif isinstance(related, dict):
            related_list = related.get("data", [])
            next_id = [i for i in related_list if i.get("relation") == "续集"]
        else:
            next_id = []
        return next_id[0]["id"] if next_id else None

    def _find_season_one_episode(
        self,
        subject_id: int,
        target_ep: int,
        root_type: int,
        release_date: str | None,
    ):
        """在第一季中查找目标集数（遍历续集链）"""
        current_id = subject_id
        first_part = True
        while True:
            if not first_part:
                current_info = self.get_subject(current_id)
                if not current_info:
                    continue
                if root_type is not None and current_info.get("type") != root_type:
                    continue
            found = self._find_episode_by_sort(current_id, target_ep)
            if found:
                return current_id, found["id"]
            episodes = self.get_episodes(current_id)
            ep_info = episodes.get("data", [])
            if not ep_info:
                logger.debug(f"未获取到剧集信息: {current_id}")
                break
            normal_season = (
                True
                if episodes.get("total", 0) > 3 and ep_info[0].get("sort", 0) <= 1
                else False
            )
            if not first_part and normal_season:
                break
            next_id = self._find_next_sequel_id(current_id)
            if not next_id:
                break
            current_id = next_id
            first_part = False
        return self._episode_lookup_failed(subject_id, target_ep, release_date)

    def _find_multi_season_episode(
        self,
        subject_id: int,
        target_season: int,
        target_ep: int,
        root_type: int,
        release_date: str | None,
    ):
        """在多季中查找目标集数（遍历续集链并追踪季数）"""
        current_id = subject_id
        season_num = 1
        last_season_num = None
        while True:
            next_id = self._find_next_sequel_id(current_id)
            if not next_id:
                break
            current_id = next_id
            current_info = self.get_subject(current_id)
            if not current_info:
                continue
            if root_type is not None and current_info.get("type") != root_type:
                continue
            episodes = self.get_episodes(current_id)
            ep_info = episodes.get("data", [])
            if not ep_info:
                logger.debug(f"未获取到剧集信息: {current_id}")
                break
            logger.debug(ep_info)
            sort_rows = [i for i in ep_info if i.get("sort") == target_ep]
            _target_ep = self._match_target_ep_rows(ep_info, target_ep)
            logger.debug(_target_ep)
            ep_found = True if target_ep and _target_ep else False

            sn = self._extract_season_number(
                current_info.get("name", ""), current_info.get("name_cn", "")
            )
            if sn is not None and sn != last_season_num:
                season_num += 1
                last_season_num = sn
            elif sn is None:
                if not sort_rows:
                    if (
                        target_ep
                        and _target_ep
                        and "第2部分" not in current_info.get("name_cn", "")
                    ):
                        season_num += 1
                elif any(ep.get("sort") == 1 for ep in ep_info):
                    season_num += 1
                    last_season_num = None
            if season_num > target_season:
                break
            if season_num == target_season:
                if not target_ep:
                    return current_id
                if target_ep > 99:
                    found = self._find_episode_by_sort(current_id, target_ep)
                    if found:
                        return current_id, found["id"]
                if not ep_found:
                    continue
                return current_id, _target_ep[0]["id"]
        return self._episode_lookup_failed(subject_id, target_ep, release_date)
