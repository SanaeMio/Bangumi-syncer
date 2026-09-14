"""BangumiApi 章集解析（mixin）"""

from __future__ import annotations

import datetime
import re
import time
from typing import Any

import httpx

from ...core.config import config_manager
from ...core.logging import logger
from ...utils.bangumi_constants import (
    EPISODE_TYPE_NORMAL,
    RELATION_ID_PARENT_STORY,
    RELATION_ID_PREQUEL,
    RELATION_ID_SEQUEL,
    RELATIONS,
    SUBJECT_TYPE_ANIME,
    SUBJECT_TYPE_REAL,
)
from ...utils.season_title import extract_explicit_season

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
        """单次分页请求章节列表（不写入实例缓存）。

        API 不可达（TTL 内）时跳过实际请求直接返回空分页，
        archive 短路已在 get_episodes 层先行处理。
        """
        if self.is_api_unreachable():
            logger.warning(
                f"📚 Bangumi API 不可达（TTL 内），get_episodes({subject_id}) 返回空"
            )
            return {"data": [], "total": 0}
        try:
            res = self.get(
                "episodes",
                params={
                    "subject_id": subject_id,
                    "type": _type,
                    "limit": limit,
                    "offset": offset,
                },
            )
        except httpx.HTTPError as e:
            # 网络不可达/重试耗尽：吞掉异常返回空分页，
            # 保证调用方（章节查找/续集链遍历）继续降级而不是崩溃
            logger.error(f"get_episodes API 请求失败（网络错误）: {e}")
            return {"data": [], "total": 0}
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
        """在 subject 内按 sort/ep 规则查找章节。

        优先级：
        1. Archive 短路（若启用且命中，直接在内存数据中筛选，避免任何 API 调用）
        2. API offset 快速路径（target_sort>99 时单次定位）
        3. 全量拉取 + 本地匹配（兜底，archive 未命中且 offset 快速路径未命中时）
        """
        # 1. 优先尝试 archive 短路，避免对长篇动画（如 sort>99）也走 API
        archive_shortcut = self._archive.try_get_episodes(
            subject_id, episode_type=_type
        )
        if archive_shortcut.hit:
            ep_info = archive_shortcut.data or []
            rows = self._match_target_ep_rows(ep_info, target_sort)
            if rows:
                logger.debug(
                    f"archive 短路命中 sort={target_sort} subject_id={subject_id}"
                )
                return rows[0]
            # archive 命中但未匹配到该 sort：archive 可能不完整，降级到 API
            logger.debug(
                f"archive 命中但未找到 sort={target_sort} subject_id={subject_id}，降级 API"
            )
            # 跳过 offset 快速路径（archive 已知该 subject 数据存在但不完整）
            # 直接全量拉取 + 本地匹配
            episodes = self.get_episodes(subject_id, _type, fetch_all=True)
            ep_info = episodes.get("data") or []
            rows = self._match_target_ep_rows(ep_info, target_sort)
            return rows[0] if rows else None

        # 2. API offset 快速路径（target_sort>99 时单次定位）
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

        # 3. 全量拉取 + 本地匹配（archive 未命中时兜底）
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
    ) -> tuple[int | None, int | None]:
        """季集匹配失败后的统一回退。

        回退顺序：
        1. 单条目 airdate 择优（需 release_date + 章节数 >= min_total）
        2. 连续编号推断（通过 ep 字段重置检测季度边界，无需 release_date）

        返回 (subject_id, episode_id)；无回退命中时返回 (None, None)，
        保持 tuple 契约以便调用方统一解包。
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
        return None, None

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
        return extract_explicit_season(f"{name} {name_cn}")

    def _match_target_ep_rows(
        self, ep_info: list, target_ep: int
    ) -> list[dict[str, Any]]:
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
    ) -> tuple[int | None, int | None]:
        max_season, max_episode = self._get_episode_sync_limits()

        if target_season > max_season or (target_ep and target_ep > max_episode):
            return None, None

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
                return subject_id, None

            found = self._find_episode_by_sort(subject_id, target_ep)
            if found:
                return subject_id, found["id"]

            logger.debug(
                f"在指定季度ID中未找到匹配的集数: {subject_id}, 目标集数: {target_ep}"
            )
            logger.debug("回退到传统方式查找集数")

        if target_season == 1:
            if not target_ep:
                return subject_id, None
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
        target_season: int = 1,
    ) -> tuple[int, int] | None:
        """在当前条目及其前传/续集链中查找目标章节（target_season>1 时优先按季定位，否则按全局 sort 解析）。

        场景：fongmi 解析出连续编号 episode=102，但已命中的 subject（如第六季）
        的 sort 范围是 235-286，不含 102。需通过前传链向前找到含 sort=102 的
        季条目（如第三/四季）。

        Bangumi 国漫常见编号模式：每季条目内 ep 从1开始，sort 是整部作品连续编号。
        本方法不依赖 release_date，仅通过 sort 字段匹配。

        Args:
            subject_id: 已命中的初始条目 id
            target_ep: 目标集数（季内集编号，对应 ep.ep；target_season<=1 时回退为全局 sort）
            max_depth: 沿单方向最多遍历多少个关联条目，防极端环
            target_season: 目标季编号；大于 1 时 target_ep 按季内集编号在关联链上
                定位目标季后解析。季定位未命中时不回退全局 sort，除非该编号超出
                目标季总集数（可判定调用方给出的是跨季连续编号）

        Returns:
            (subject_id, episode_id) 或 None
        """
        if not target_ep:
            return None

        # 重置跨季链命中路径标记（供 orchestrator 区分 chain / 同 IP 改编）
        self.last_cross_season_path = ""

        # 整体 deadline：链式 API 调用累计耗时超过 60s 立即放弃
        # （防御错误 subject_id 触发的长链遍历占用 sync 线程池）
        deadline = time.monotonic() + _CROSS_SEASON_DEADLINE_SECONDS

        # 季定位优先：明确指定季时，先按季在关联链上定位目标条目，再按集解析，
        # 避免仅凭全局 sort 命中错误季（例如第 4 季第 13 集误命中第 1 季第 13 集）。
        if target_season and target_season > 1:
            season_pick, beyond_season_total = self._resolve_by_season_chain(
                subject_id, target_season, target_ep, max_depth, deadline
            )
            if season_pick:
                self.last_cross_season_path = "chain"
                return season_pick
            # target_season>1 时 target_ep 是季内集编号。目标季未定位、或该季章节
            # 无法按 ep 解析时回退全局 sort，会把季内编号当作全局序号命中错误季
            # （第 4 季第 13 集命中第 1 季第 13 集）；仅当编号超出目标季总集数、
            # 可判定为跨季连续编号时才回退。
            if not beyond_season_total:
                return None

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
            # P1-6: 无 type=0 章节（空列表或全 SP），无法通过 sort 范围判断方向。
            # 不直接返回 None，两个方向都尝试（prequel + sequel）。
            directions = ["prequel", "sequel"]
        else:
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
                self.last_cross_season_path = "chain"
                return result
        # 同 IP 改编兜底：续集/前传链覆盖不到「改编」等跨媒体边
        # （如动画 ↔ 真人网剧同名场景：凡人修仙传等）。
        # 分层策略（archive 命中零成本全量闭包，在线仅一跳，见
        # _try_find_episode_in_franchise 说明）：不引入在线完整 BFS。
        franchise_result = self._try_find_episode_in_franchise(
            subject_id, target_ep, visited, max_depth, deadline
        )
        if franchise_result:
            return franchise_result
        return None

    def _resolve_by_season_chain(
        self,
        subject_id: int,
        target_season: int,
        target_ep: int,
        max_depth: int,
        deadline: float | None = None,
    ) -> tuple[tuple[int, int] | None, bool]:
        """按季在关联链上定位目标条目并解析季内集编号。

        调用方明确提供季编号（target_season>1）时，依据各条目标题中的季声明在
        整条前传/续集链上定位目标季的条目（分篇 cours 视为同一季的连续段），再按
        累计集数把 target_ep 解析为目标季内的具体章节。

        Returns:
            (命中结果, target_ep 是否超出目标季总集数)
            - 命中结果非 None 时为 (subject_id, episode_id)
            - 命中为 None 且未超范围：目标季不在关联链上，或该季章节缺少 ep 字段，
              无法按季内编号解析
            - 命中为 None 且超出范围：目标季已定位，但 target_ep 大于该季总集数，
              说明调用方给出的是跨季连续编号而非季内编号
        """
        chain = self._collect_related_subjects(subject_id, max_depth, deadline)
        if not chain:
            return None, False

        # 收集关联链上每个条目的季编号与正片章节，按链序排列
        season_buckets: list[tuple[int | None, int, list[dict]]] = []
        for sid in chain:
            if deadline is not None and time.monotonic() > deadline:
                break
            info = self.get_subject(sid)
            if not info:
                continue
            subject_type = info.get("type")
            if subject_type is not None and subject_type != SUBJECT_TYPE_ANIME:
                continue
            season = self._extract_season_number(
                info.get("name") or "", info.get("name_cn") or ""
            )
            episodes = self.get_episodes(sid, fetch_all=True).get("data") or []
            type0 = [e for e in episodes if e.get("type", 0) == 0]
            season_buckets.append((season, sid, type0))

        # 仅保留目标季的连续段，按链序累计集数定位目标集
        targets = [
            (sid, eps)
            for (season, sid, eps) in season_buckets
            if season == target_season
        ]
        if not targets:
            return None, False

        # 超出目标季总集数：不是季内相对编号，交由调用方按全局 sort 解释
        if target_ep > sum(len(eps) for _, eps in targets):
            return None, True

        cumulative = 0
        for sid, eps in targets:
            count = len(eps)
            if cumulative < target_ep <= cumulative + count:
                local_ep = target_ep - cumulative
                rows = [e for e in eps if e.get("ep") == local_ep]
                if rows:
                    return (sid, rows[0]["id"]), False
                break
            cumulative += count
        return None, False

    def _collect_related_subjects(
        self,
        subject_id: int,
        max_depth: int,
        deadline: float | None = None,
    ) -> list[int]:
        """按时间顺序收集关联链条目（前传在前、起始居中、续集在后）。

        续集方向优先使用 Archive 续集链短路，未命中时降级逐跳；前传方向使用
        search_previous_subjects（由近到远），反转后置于起始条目之前以形成时间序。
        """
        chain: list[int] = [subject_id]
        visited: set[int] = {subject_id}

        # 续集方向（由近到远追加在起始之后）
        shortcut = self._archive.try_find_sequel_chain(subject_id, max_hops=max_depth)
        if shortcut.hit and shortcut.data:
            for sid in shortcut.data:
                if sid and sid not in visited:
                    visited.add(sid)
                    chain.append(sid)
        else:
            current_id = subject_id
            for _ in range(max_depth):
                if deadline is not None and time.monotonic() > deadline:
                    break
                next_id = self._find_related_id_by_relation(
                    current_id, _RELATION_CN_SEQUEL
                )
                if not next_id or next_id in visited:
                    break
                visited.add(next_id)
                chain.append(next_id)
                current_id = next_id

        # 前传方向（由近到远），反转后置于起始之前形成时间序
        prequels = self.search_previous_subjects(subject_id, max_hops=max_depth) or []
        prefixed: list[int] = []
        for sid in reversed(prequels):
            if sid and sid not in visited:
                visited.add(sid)
                prefixed.append(sid)
        return prefixed + chain

    def _try_find_episode_in_franchise(
        self,
        subject_id: int,
        target_ep: int,
        visited: set,
        max_depth: int,
        deadline: float | None = None,
    ) -> tuple[int, int] | None:
        """同 IP 改编链兜底：在 franchise 关系闭包内查找含 sort=target_ep 的条目

        链式 sequel/prequel 遍历（_walk_chain_for_episode）只沿前传/续集边爬，
        动画与真人剧（如凡人修仙传动画 ↔ 网剧）之间的「改编」边不在其中。
        本方法在链式全部 miss 后兜底：

        - Archive 命中：try_find_franchise_closure 一次本地 SQL 拿完整连通分量
          （含改编/相同系列/外传等边，FRANCHISE_RELATION_TYPES），零 API 成本，
          全量遍历找目标 sort。
        - Archive miss / 未命中：不做在线完整 BFS（最坏 64 节点 × 2 次 API/节点
          且 sort 等值匹配命中率低，成本与收益不成正比），仅一跳直接邻居检查
          （改编关系通常就是起始条目的一跳邻居）。

        Args:
            subject_id: 起始条目
            target_ep: 目标集数
            visited: 已访问 subject_id 集合（防环，会被更新）
            max_depth: archive 闭包最大节点数
            deadline: 整体 deadline

        Returns:
            (subject_id, episode_id) 或 None
        """
        # Archive 快速路径：本地 SQL 闭包（含改编等边），零 API
        shortcut = self._archive.try_find_franchise_closure(
            subject_id, max_hops=max_depth
        )
        if shortcut.hit:
            chain = shortcut.data or []
            if chain:
                result = self._find_episode_in_chain(
                    chain,
                    target_ep,
                    visited,
                    deadline,
                    allowed_types=(SUBJECT_TYPE_ANIME, SUBJECT_TYPE_REAL),
                )
                if result:
                    self.last_cross_season_path = "franchise_archive"
                    return result
            # archive 命中但闭包为空或未找到：archive 数据可能不完整，
            # 继续在线一跳检查
        # 在线降级：仅一跳直接邻居检查（不 BFS）
        return self._try_online_franchise_one_hop(
            subject_id, target_ep, visited, deadline
        )

    def _try_online_franchise_one_hop(
        self,
        subject_id: int,
        target_ep: int,
        visited: set,
        deadline: float | None = None,
    ) -> tuple[int, int] | None:
        """在线一跳同 IP 改编检查：只查起始条目的直接邻居

        「动画 ↔ 真人剧」改编场景通常就是一跳直连，一跳覆盖绝大多数同类场景；
        不做在线完整 BFS（否则最坏 64 节点 × (get_related_subjects + get_subject)
        约 128 次 API，且跨媒体 sort 等值命中率低，付出与收益不成正比）。

        Args:
            subject_id: 起始条目
            target_ep: 目标集数
            visited: 已访问 subject_id 集合（会被更新）
            deadline: 整体 deadline

        Returns:
            (subject_id, episode_id) 或 None
        """
        from ..bangumi_archive._store import FRANCHISE_RELATION_CN_SET

        if deadline is not None and time.monotonic() > deadline:
            return None
        related = self.get_related_subjects(subject_id)
        if isinstance(related, dict):
            items = related.get("data", [])
        elif isinstance(related, list):
            items = related
        else:
            return None
        for rel in items:
            if not isinstance(rel, dict):
                continue
            if (rel.get("relation") or "").strip() not in FRANCHISE_RELATION_CN_SET:
                continue
            rid = rel.get("id")
            if not rid or rid in visited:
                continue
            if deadline is not None and time.monotonic() > deadline:
                return None
            visited.add(rid)
            info = self.get_subject(rid)
            if not info:
                continue
            if info.get("type") not in (SUBJECT_TYPE_ANIME, SUBJECT_TYPE_REAL):
                continue
            # 跳过剧场版/电影（标题命中关键词），与链式路径行为一致
            name = (info.get("name", "") or "") + (info.get("name_cn", "") or "")
            if "剧场版" in name or "电影" in name:
                continue
            if deadline is not None and time.monotonic() > deadline:
                return None
            found = self._find_episode_by_sort(rid, target_ep)
            if found:
                self.last_cross_season_path = "franchise_online"
                logger.debug(
                    f"通过同 IP 改编一跳找到目标集: subject_id={rid}, "
                    f"sort={target_ep}, ep_id={found['id']}"
                )
                return rid, found["id"]
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

        # 快速路径：archive 启用且命中时，一次拿完整续集链批量遍历
        # 避免 sequel 方向逐跳 _find_related_id_by_relation + get_subject 调用
        # archive 命中时链上 get_subject / _find_episode_by_sort 也都走 archive 短路
        # archive miss / 未启用 / 空链时降级到逐跳逻辑（保持原行为）
        if direction == "sequel":
            shortcut = self._archive.try_find_sequel_chain(start_id, max_hops=max_depth)
            if shortcut.hit:
                chain = shortcut.data or []
                if chain:
                    result = self._find_episode_in_chain(
                        chain, target_ep, visited, deadline
                    )
                    if result:
                        return result
                    # P1-5: archive 链非空但未找到目标，不再直接 return None。
                    # archive 链可能不完整（缺少部分续集），降级到逐 hop API
                    # 以发现 archive 链外的后续条目。chain 中已检查的 subject
                    # 已加入 visited，逐 hop 会跳过它们继续向链尾推进。
                # archive 命中但续集链为空：关联数据可能不完整，降级到逐跳 API

        if direction == "prequel":
            # 前传方向：正式启用 RelationMixin.search_previous_subjects
            # 内部优先 try_find_prequel_chain 短路、archive miss 时降级在线逐跳，
            # 拿到完整前传链后复用 _find_episode_in_chain 批量遍历（与续集方向对称）。
            chain = self.search_previous_subjects(start_id, max_hops=max_depth) or []
            result = self._find_episode_in_chain(chain, target_ep, visited, deadline)
            if result:
                return result
            # search_previous_subjects 已确认无前传链或无目标，返回 None
            # （等价于原逐跳降级路径的终点，避免重复查询）
            return None

        # 降级路径：逐跳遍历（sequel 方向 archive miss/空链/非空未命中时走到）
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
            if not next_id:
                return None
            if next_id == current_id:
                # 自环：关系数据异常，终止遍历
                return None
            if next_id in visited:
                # P1-5: 已通过 archive 链检查过的 subject（如 _find_episode_in_chain
                # 遍历过），跳过检查但继续沿链前进，发现 archive 链外的后续条目。
                # max_depth 限制总迭代数，避免环导致无限循环。
                current_id = next_id
                continue
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

    def _find_episode_in_chain(
        self,
        chain: list[int],
        target_ep: int,
        visited: set,
        deadline: float | None = None,
        allowed_types: tuple[int, ...] = (SUBJECT_TYPE_ANIME,),
    ) -> tuple[int, int] | None:
        """在已知续集链上批量遍历查找含 sort=target_ep 的条目

        与 _walk_chain_for_episode 的逐跳逻辑相比：
        - 跳过 _find_related_id_by_relation（chain 已预构图）
        - 保留类型/剧场版过滤和 deadline 检查
        - 已访问的 subject_id 跳过（防环）

        Args:
            chain: 关联条目链 subject_id 列表（不含起始条目）
            target_ep: 目标集数
            visited: 已访问的 subject_id 集合（会被本方法更新）
            deadline: 整体 deadline
            allowed_types: 允许的 subject type 集合。默认仅动画（2）；
                同 IP 改编链（动画↔网剧）场景放行 (SUBJECT_TYPE_ANIME,
                SUBJECT_TYPE_REAL)。
        """
        for current_id in chain:
            if current_id in visited:
                continue
            visited.add(current_id)

            if deadline is not None and time.monotonic() > deadline:
                logger.warning(
                    f"_find_episode_in_chain 整体 deadline 超时，终止链遍历: "
                    f"current_id={current_id}, target_ep={target_ep}"
                )
                return None

            # 类型过滤：只看动画（SUBJECT_TYPE_ANIME），跳过书籍/音乐等
            info = self.get_subject(current_id)
            if not info:
                continue
            if info.get("type") not in allowed_types:
                continue
            # 跳过剧场版/电影（标题命中关键词）
            name = (info.get("name", "") or "") + (info.get("name_cn", "") or "")
            if "剧场版" in name or "电影" in name:
                continue

            if deadline is not None and time.monotonic() > deadline:
                logger.warning(
                    f"_find_episode_in_chain 整体 deadline 超时，终止链遍历: "
                    f"current_id={current_id}, target_ep={target_ep}"
                )
                return None

            # 在当前条目内查 sort
            found = self._find_episode_by_sort(current_id, target_ep)
            if found:
                logger.debug(
                    f"通过 archive 续集链找到目标集: subject_id={current_id}, "
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
        visited = {subject_id}  # 防环：Bangumi 关系数据可能存在循环引用
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
            if next_id in visited:
                logger.warning(
                    f"_find_season_one_episode 检测到续集链环引用，终止遍历: "
                    f"subject_id={subject_id}, next_id={next_id}"
                )
                break
            visited.add(next_id)
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
        visited = {subject_id}  # 防环：Bangumi 关系数据可能存在循环引用
        while True:
            next_id = self._find_next_sequel_id(current_id)
            if not next_id:
                break
            if next_id in visited:
                logger.warning(
                    f"_find_multi_season_episode 检测到续集链环引用，终止遍历: "
                    f"subject_id={subject_id}, next_id={next_id}"
                )
                break
            visited.add(next_id)
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
                    return current_id, None
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
