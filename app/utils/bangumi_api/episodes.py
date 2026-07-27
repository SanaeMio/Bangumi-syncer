"""BangumiApi 章集解析（mixin）"""

from __future__ import annotations

import datetime
import re
import time
from typing import Any

from ...core.config import config_manager
from ...core.logging import logger
from ...utils.bangumi_constants import (
    EPISODE_TYPE_NORMAL,
    RELATION_ID_PARENT_STORY,
    RELATION_ID_PREQUEL,
    RELATION_ID_SEQUEL,
    RELATIONS,
    SUBJECT_TYPE_ANIME,
)
from ...utils.text_constants import CN_NUM

# 关联类型中文名（由 ID 常量推导，避免硬编码字符串）
_RELATION_CN_SEQUEL = RELATIONS[RELATION_ID_SEQUEL]
_RELATION_CN_PREQUEL = RELATIONS[RELATION_ID_PREQUEL]
_RELATION_CN_PARENT_STORY = RELATIONS[RELATION_ID_PARENT_STORY]

_EPISODES_PAGE_LIMIT = 200
_LONG_SERIES_AIRDATE_MIN_TOTAL = 100
# find_episode_across_seasons 整体 deadline（秒）。
# 防御错误 subject_id 触发的长链遍历：单次 API 超时 10s × 多跳累加可能逼近数分钟，
# 超过此 deadline 立即放弃，避免占用 sync 线程池导致整体卡死。
_CROSS_SEASON_DEADLINE_SECONDS = 60.0


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

        # Archive 短路：本地命中即返回（始终全量，对 fetch_all=False 也安全）
        # 未命中降级到 API 分页拉取（保持原行为）
        shortcut = self._archive.try_get_episodes(subject_id, episode_type=_type)
        if shortcut.hit:
            data_list = shortcut.data or []
            result = {"data": data_list, "total": len(data_list)}
            self._put_cache("get_episodes", cache_key, result)
            return result

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
        target_season: int = 1,
    ) -> int | None:
        """季集匹配失败后的统一回退。

        回退顺序：
        1. 单条目 airdate 择优（需 release_date + 章节数 >= min_total）
        2. 连续编号推断（通过 ep 字段重置检测季度边界，无需 release_date）
        """
        if release_date and target_ep:
            air_pick = self._resolve_episode_by_airdate_in_subject(
                subject_id, release_date
            )
            if air_pick is not None:
                return air_pick
        if target_season > 1 and target_ep:
            cont_pick = self._try_resolve_continuous_season_episode(
                subject_id, target_season, target_ep
            )
            if cont_pick is not None:
                return cont_pick
        return None, None if target_ep else None

    def _try_resolve_continuous_season_episode(
        self,
        subject_id: int,
        target_season: int,
        target_ep: int,
    ) -> tuple[str | int, str | int] | None:
        """单 subject 连续编号场景：通过季度边界检测定位目标 sort。

        适用于 Bangumi 将多季合并到一个 subject 的场景：
        第一季 ep=1..24 sort=1..24，第二季 ep=1..24 sort=25..48。

        季度边界检测优先级：
        1. ep 字段重置检测：ep 从 >1 降到 1，说明新季开始
        2. sort 跳变检测（ep 字段缺失时兜底）：
           sort 不连续（如 24 → 1，或 24 → 26 但下一行 sort 又回到 1）
           说明新季开始，且新季 sort 从 1 重新计数
        """
        if target_season < 2 or not target_ep:
            return None

        episodes = self.get_episodes(subject_id, fetch_all=True)
        ep_info = episodes.get("data") or []
        if len(ep_info) < 2:
            return None

        # 仅取本篇章节（type=0），按 sort 排序
        has_type = any("type" in e for e in ep_info)
        pool = (
            [e for e in ep_info if e.get("type", 0) == 0] if has_type else list(ep_info)
        )
        if len(pool) < 2:
            pool = list(ep_info)
        pool.sort(key=lambda e: e.get("sort", 0))

        # 检测 ep 字段是否有效（archive 旧库可能 ep 全为 NULL/0）
        ep_valid_count = sum(1 for e in pool if (e.get("ep") or 0) > 0)
        use_ep_field = ep_valid_count >= len(pool) * 0.5  # 半数以上有效才用

        # 检测季度边界
        # 按 id 升序排（episode id 通常递增，能反映章节的录入顺序），
        # 而非按 sort 排（sort 重置场景下排序会聚拢相同 sort，无法检测跳变）
        pool_by_id = sorted(pool, key=lambda e: e.get("id", 0))
        season_start_sorts: list[int] = [pool_by_id[0].get("sort", 1)]
        if use_ep_field:
            # 策略 1：ep 字段重置检测（ep 从 >1 降到 1）
            prev_ep = 0
            for ep in pool_by_id:
                ep_num = ep.get("ep", 0) or 0
                sort_num = ep.get("sort", 0) or 0
                if ep_num == 1 and prev_ep > 1:
                    season_start_sorts.append(sort_num)
                prev_ep = ep_num
        else:
            # 策略 2：sort 跳变检测（ep 字段缺失时兜底）
            # 场景：archive dump 缺 ep 字段，但 sort 在每季开始时重置为 1。
            # 按 id 升序遍历，sort 从高值跳到 1 说明新季开始。
            # 仅检测 sort=1 的重置点（最可靠）。
            prev_sort = pool_by_id[0].get("sort", 0) or 0
            for ep in pool_by_id[1:]:
                sort_num = ep.get("sort", 0) or 0
                # sort=1 且前一个 sort > 1，说明新季开始
                if sort_num == 1 and prev_sort > 1:
                    season_start_sorts.append(sort_num)
                prev_sort = sort_num

        if len(season_start_sorts) < target_season:
            logger.debug(
                f"连续编号: 检测到 {len(season_start_sorts)} 季，"
                f"无法定位第 {target_season} 季 (subject_id={subject_id}, "
                f"use_ep_field={use_ep_field})"
            )
            return None

        # 目标季的起始 sort
        season_start_sort = season_start_sorts[target_season - 1]
        target_sort = season_start_sort + target_ep - 1

        # sort 重置场景：每季 sort 从 1 开始，target_sort 可能与多季的 sort 重复。
        # 此时需跳过前 (target_season - 1) 个 sort=target_sort 的章节。
        # 检测是否为 sort 重置场景：season_start_sorts 中有多个 1（或多个相同值）
        if (
            not use_ep_field
            and target_season > 1
            and season_start_sorts.count(season_start_sort) > 1
        ):
            # sort 重置场景：跳过前 (target_season - 1) 个匹配
            match_count = 0
            for ep in pool:
                if ep.get("sort") == target_sort:
                    match_count += 1
                    if match_count == target_season:
                        logger.debug(
                            f"连续编号匹配(sort 重置): subject_id={subject_id} "
                            f"season={target_season} ep={target_ep} → sort={target_sort} "
                            f"ep_id={ep['id']} (第 {match_count} 个匹配)"
                        )
                        return subject_id, ep["id"]
        else:
            # 连续编号场景：sort 唯一
            for ep in pool:
                if ep.get("sort") == target_sort:
                    logger.debug(
                        f"连续编号匹配: subject_id={subject_id} "
                        f"season={target_season} ep={target_ep} → sort={target_sort} "
                        f"ep_id={ep['id']} (use_ep_field={use_ep_field})"
                    )
                    return subject_id, ep["id"]

        logger.debug(
            f"连续编号: 未找到 sort={target_sort} 的章节 "
            f"(subject_id={subject_id}, season={target_season}, ep={target_ep})"
        )
        return None

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
            nxt = [i for i in related if i.get("relation") == _RELATION_CN_SEQUEL]
        elif isinstance(related, dict):
            related_list = related.get("data", [])
            nxt = [i for i in related_list if i.get("relation") == _RELATION_CN_SEQUEL]
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
                return CN_NUM.get(cn)
            # "十一"~"十九"
            if cn.startswith("十"):
                return 10 + CN_NUM.get(cn[1], 0)
            return CN_NUM.get(cn)
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
        pool = (
            [e for e in ep_info if e.get("type") == EPISODE_TYPE_NORMAL]
            if has_type
            else list(ep_info)
        )
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
        root_platform: str = "",
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
            # 续集链 platform 隔离：根条目与当前条目都带 platform 且不同时跳过
            cur_platform = (current_info.get("platform") or "").strip()
            if root_platform and cur_platform and cur_platform != root_platform:
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

        # 获取根条目的 subject type 与 platform，续集链遍历时仅放行相同媒体类型/平台的条目
        root_info = self.get_subject(subject_id)
        root_type = root_info.get("type") if root_info else None
        root_platform = (root_info.get("platform") or "").strip() if root_info else ""

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
                subject_id, target_ep, root_type, root_platform, release_date
            )

        # Plex 季数与 Bangumi 多期/续集计数不一致时，用播出日 + 章节 airdate 择优
        if release_date and target_season > 1 and target_ep:
            air_pick = self._try_resolve_sequel_by_airdate(
                subject_id,
                target_ep,
                release_date,
                root_type=root_type,
                root_platform=root_platform,
            )
            if air_pick is not None:
                return air_pick[0], air_pick[1]

        return self._find_multi_season_episode(
            subject_id,
            target_season,
            target_ep,
            root_type,
            root_platform,
            release_date,
        )

    def _find_next_sequel_id(self, current_id: int) -> int | None:
        """从关联条目中查找续集 subject_id，无则返回 None"""
        # Archive 短路：本地命中即返回（int 或 None）
        shortcut = self._archive.try_find_next_sequel_id(current_id)
        if shortcut.hit:
            return shortcut.data

        related = self.get_related_subjects(current_id)
        if isinstance(related, list):
            next_id = [i for i in related if i.get("relation") == _RELATION_CN_SEQUEL]
        elif isinstance(related, dict):
            related_list = related.get("data", [])
            next_id = [
                i for i in related_list if i.get("relation") == _RELATION_CN_SEQUEL
            ]
        else:
            next_id = []
        return next_id[0]["id"] if next_id else None

    def _find_related_id_by_relation(
        self, subject_id: int, relation: str
    ) -> int | None:
        """从关联条目中按 relation 查找 subject_id。

        支持的 relation 示例：
        - "续集"（RELATION_ID_SEQUEL）：续作（与 _find_next_sequel_id 行为一致）
        - "前传"（RELATION_ID_PREQUEL）：前作
        - "主线故事"（RELATION_ID_PARENT_STORY）：剧场版关联条目中的主线剧集条目
        """
        # Archive 短路：本地命中即返回（int 或 None）
        shortcut = self._archive.try_find_related_id_by_relation(subject_id, relation)
        if shortcut.hit:
            return shortcut.data

        related = self.get_related_subjects(subject_id)
        if isinstance(related, list):
            items = related
        elif isinstance(related, dict):
            items = related.get("data", [])
        else:
            return None
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("relation") == relation:
                sid = item.get("id")
                if sid:
                    return sid
        return None

    def find_episode_across_seasons(
        self,
        subject_id: int,
        target_ep: int,
        max_depth: int = 20,
    ) -> tuple[int, int] | None:
        """在当前条目及其前传/续集链中查找含 sort=target_ep 的章节。

        场景：fongmi 解析出连续编号 episode=102，但已命中的 subject（如第六季）
        的 sort 范围是 235-286，不含 102。需通过前传链向前找到含 sort=102 的
        季条目（如第三/四季）。

        Bangumi 国漫常见编号模式：每季条目内 ep 从1开始，sort 是整部作品连续编号。
        本方法不依赖 release_date，仅通过 sort 字段匹配。

        Args:
            subject_id: 已命中的初始条目 id
            target_ep: 目标集数（连续编号，对应 ep.sort）
            max_depth: 沿单方向最多遍历多少个关联条目，防极端环

        Returns:
            (subject_id, episode_id) 或 None
        """
        if not target_ep:
            return None

        # 整体 deadline：链式 API 调用累计耗时超过 60s 立即放弃
        # （防御错误 subject_id 触发的长链遍历占用 sync 线程池）
        deadline = time.monotonic() + _CROSS_SEASON_DEADLINE_SECONDS

        # 先在当前 subject 内查
        found = self._find_episode_by_sort(subject_id, target_ep)
        if found:
            return subject_id, found["id"]

        if time.monotonic() > deadline:
            logger.warning(
                f"find_episode_across_seasons 整体超时（>"
                f"{_CROSS_SEASON_DEADLINE_SECONDS:.0f}s），放弃跨季查找: "
                f"subject_id={subject_id}, target_ep={target_ep}"
            )
            return None

        # 获取当前 subject 的 sort 范围判断方向
        episodes = self.get_episodes(subject_id, fetch_all=True)
        ep_info = episodes.get("data") or []
        type0_rows = [e for e in ep_info if e.get("type", 0) == 0]
        sorts = [e.get("sort", 0) for e in type0_rows if e.get("sort")]
        if not sorts:
            return None
        min_sort = min(sorts)
        max_sort = max(sorts)

        # 决定遍历方向
        if target_ep < min_sort:
            directions = ["prequel"]
        elif target_ep > max_sort:
            directions = ["sequel"]
        else:
            # target_ep 在范围内但未找到（如部分章节缺失），两个方向都试
            directions = ["prequel", "sequel"]

        visited = {subject_id}
        for direction in directions:
            if time.monotonic() > deadline:
                logger.warning(
                    f"find_episode_across_seasons 整体超时（>"
                    f"{_CROSS_SEASON_DEADLINE_SECONDS:.0f}s），放弃剩余方向: "
                    f"subject_id={subject_id}, target_ep={target_ep}, "
                    f"direction={direction}"
                )
                return None
            result = self._walk_chain_for_episode(
                subject_id, target_ep, direction, visited, max_depth, deadline
            )
            if result:
                return result
        return None

    def _walk_chain_for_episode(
        self,
        start_id: int,
        target_ep: int,
        direction: str,
        visited: set,
        max_depth: int,
        deadline: float | None = None,
    ) -> tuple[int, int] | None:
        """沿指定方向遍历关联条目链，查找含 sort=target_ep 的条目。

        Args:
            start_id: 起始条目 id
            target_ep: 目标集数
            direction: "prequel"（前传）或 "sequel"（续集）
            visited: 已访问的 subject_id 集合（防环）
            max_depth: 最大遍历深度
            deadline: 整体 deadline（monotonic 时间戳），超过则立即返回 None
        """
        relation_map = {"prequel": _RELATION_CN_PREQUEL, "sequel": _RELATION_CN_SEQUEL}
        relation = relation_map.get(direction)
        if not relation:
            return None

        current_id = start_id
        for _ in range(max_depth):
            if deadline is not None and time.monotonic() > deadline:
                logger.warning(
                    f"_walk_chain_for_episode 整体 deadline 超时，终止链遍历: "
                    f"direction={direction}, current_id={current_id}, "
                    f"target_ep={target_ep}"
                )
                return None
            next_id = self._find_related_id_by_relation(current_id, relation)
            if not next_id or next_id in visited:
                return None
            visited.add(next_id)
            current_id = next_id

            # 类型过滤：只看动画（SUBJECT_TYPE_ANIME），跳过书籍/音乐等
            info = self.get_subject(current_id)
            if not info:
                continue
            if info.get("type") != SUBJECT_TYPE_ANIME:
                continue
            # 跳过剧场版/电影（标题命中关键词）
            name = (info.get("name", "") or "") + (info.get("name_cn", "") or "")
            if "剧场版" in name or "电影" in name:
                continue

            if deadline is not None and time.monotonic() > deadline:
                logger.warning(
                    f"_walk_chain_for_episode 整体 deadline 超时，终止链遍历: "
                    f"direction={direction}, current_id={current_id}, "
                    f"target_ep={target_ep}"
                )
                return None

            # 在当前条目内查 sort
            found = self._find_episode_by_sort(current_id, target_ep)
            if found:
                logger.debug(
                    f"通过{direction}链找到目标集: subject_id={current_id}, "
                    f"sort={target_ep}, ep_id={found['id']}"
                )
                return current_id, found["id"]
        return None

    def _find_season_one_episode(
        self,
        subject_id: int,
        target_ep: int,
        root_type: int,
        root_platform: str,
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
                # 续集链 platform 隔离：根条目与当前条目都带 platform 且不同时跳过
                cur_platform = (current_info.get("platform") or "").strip()
                if root_platform and cur_platform and cur_platform != root_platform:
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
        root_platform: str,
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
            # 续集链 platform 隔离：根条目与当前条目都带 platform 且不同时跳过
            cur_platform = (current_info.get("platform") or "").strip()
            if root_platform and cur_platform and cur_platform != root_platform:
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
                    elif not _target_ep:
                        # 兜底：新续集 subject 既无 sort=target_ep 也无 ep=target_ep，
                        # 仍应认为是一个新季度，避免 season_num 不递增导致走过头。
                        # 场景：续集链中的 OVA/特别篇 episode 数据稀疏，
                        # 不应因缺少目标章节而跳过整个 subject 的季度计数。
                        season_num += 1
                else:
                    # 有 sort=target_ep 的章节，认为是新季开始。
                    # 不再强制检查 sort=1：长篇续集（如魔人ブウ編 sort 从 99 开始）
                    # 也是独立季度，应递增 season_num。
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
        return self._episode_lookup_failed(
            subject_id, target_ep, release_date, target_season=target_season
        )
