"""
映射服务模块

支持两种映射格式（向后兼容）：
1. 简单格式：``"番剧名": "subject_id"``，标题精确匹配，所有季度共用一个 ID
2. 高级格式：``"番剧名": {"subject_id": "123", "season": 2}``，指定季度才命中
   高级格式还可携带 ``segments``（集数分段表），用于把媒体库与 Bangumi
   两套不同的季/集划分「拉平对齐」，见 :meth:`find_episode_mapping`。

另支持正则规则匹配（``rules`` 数组），用正则表达式匹配标题，适合处理续作/特殊命名。

**本文件是全局共享配置**：映射对整个程序生效，不区分媒体服务器用户与
Bangumi 账号（账号阶段只决定「同步到哪个账号」，不决定「标题对应哪个条目」）。
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import regex as re

from ..core.container import Injectable
from ..core.logging import logger

# 单条正则规则匹配的超时（秒）。
# regex 库支持 timeout 参数，可防御用户配置的病态正则引发的灾难性回溯。
_REGEX_TIMEOUT = 1.0

# 映射配置文件的候选路径（读写共用同一份顺序，避免读写路径各写一遍而漂移）。
_MAPPING_FILE_PATHS = (
    "./bangumi_mapping.json",  # 当前目录
    "/app/config/bangumi_mapping.json",  # Docker挂载目录
    "/app/bangumi_mapping.json",  # Docker内部目录
)


class MappingService:
    """映射服务"""

    def __init__(self) -> None:
        self._cached_mappings: dict[str, Any] = {}
        self._cached_rules: list[dict[str, Any]] = []
        self._mapping_file_path: str | None = None
        self._last_modified_time: float = 0

    def _find_existing_path(self) -> str | None:
        """返回第一个存在的映射配置文件路径，都不存在返回 None"""
        for path in _MAPPING_FILE_PATHS:
            if os.path.exists(path):
                return path
        return None

    def load_custom_mappings(self) -> dict[str, Any]:
        """从外部JSON文件读取自定义映射配置

        返回的 mappings 字典值可能为 str（简单格式）或 dict（高级格式），
        调用方应使用 :meth:`find_mapping` 而非直接查表，以统一处理两种格式。
        """
        current_file_path = self._find_existing_path()

        # 如果没有找到配置文件，创建默认文件
        if not current_file_path:
            default_file = "./bangumi_mapping.json"
            try:
                default_config = {
                    "_comment": "自定义映射配置文件 - 用于处理程序通过搜索无法自动匹配的项目，参考_examples的格式将新内容添加到mappings中",
                    "_format": "番剧名: bangumi_subject_id 或 {'subject_id': 'id', 'season': 1, 'segments': [...]}",
                    "_note": "映射是全局配置：对所有媒体服务器用户与 Bangumi 账号生效。bangumi_subject_id 需要配置第一季的，程序会自动往后找；若该季在 Bangumi 上是独立条目，请用季度感知格式或 segments 指定。",
                    "_examples": {
                        "魔王学院的不适任者": "292222",
                        "我推的孩子": "386809",
                    },
                    "_rules_format": "rules 每项：{'pattern': '正则', 'subject_id': 'id', 'season': 1, 'description': '说明'}；season 可省略（表示所有季）",
                    "_rules_example": [
                        {
                            "pattern": "^.*之.*刃$",
                            "subject_id": "123456",
                            "description": "示例：匹配标题以「之刃」结尾的番剧",
                        },
                        {
                            "pattern": "^某番剧$",
                            "subject_id": "789012",
                            "season": 2,
                            "description": "示例：仅第 2 季命中该规则",
                        },
                    ],
                    "mappings": {"假面骑士加布": "502002"},
                    "rules": [],
                }
                self._atomic_write(default_file, default_config)
                logger.info(f"创建了默认的自定义映射文件: {default_file}")
                current_file_path = default_file
            except Exception as e:
                logger.error(f"创建默认映射文件失败: {e}")
                return {}

        try:
            # 获取文件修改时间
            current_modified_time = os.path.getmtime(current_file_path)

            # 检查是否需要重新加载
            need_reload = (
                self._mapping_file_path != current_file_path  # 文件路径变化
                or current_modified_time != self._last_modified_time  # 文件被修改
                or not self._cached_mappings  # 缓存为空
            )

            if need_reload:
                logger.debug(f"检测到映射配置文件变化，重新加载: {current_file_path}")

                with open(current_file_path, encoding="utf-8") as f:
                    data = json.load(f)

                if not isinstance(data, dict):
                    raise ValueError(
                        f"映射配置顶层必须是对象，实际为 {type(data).__name__}"
                    )

                mappings = data.get("mappings", {})
                if not isinstance(mappings, dict):
                    raise ValueError(
                        "mappings 必须是对象（标题 → subject_id 或配置对象）"
                    )
                rules = data.get("rules", []) or []
                if not isinstance(rules, list):
                    raise ValueError("rules 必须是数组")

                # 规范化 rules：仅用于**匹配**，过滤无效条目并预编译正则。
                # 写回时不使用这份过滤后的列表 —— 写盘路径（update_custom_
                # mappings）会重新从磁盘读取原始 rules，确保非法规则不被静默删除。
                normalized_rules: list[dict[str, Any]] = []
                for rule in rules:
                    if not isinstance(rule, dict):
                        continue
                    pattern = rule.get("pattern", "")
                    subject_id = rule.get("subject_id", "")
                    if not pattern or not subject_id:
                        continue
                    try:
                        re.compile(pattern)
                    except re.error as e:
                        logger.warning(
                            f"映射规则正则编译失败，已忽略：{pattern}（{e}）"
                        )
                        continue
                    normalized_rules.append(rule)

                # 更新缓存
                self._cached_mappings = mappings
                self._cached_rules = normalized_rules
                self._mapping_file_path = current_file_path
                self._last_modified_time = current_modified_time

                logger.debug(
                    f"从 {current_file_path} 重新加载了 {len(mappings)} 个映射、"
                    f"{len(normalized_rules)} 条正则规则"
                    f"（原始规则 {len(rules)} 条）"
                )
            else:
                logger.debug(
                    f"使用缓存的映射配置，共 {len(self._cached_mappings)} 个映射、"
                    f"{len(self._cached_rules)} 条规则"
                )

            return self._cached_mappings.copy()  # 返回副本以避免外部修改影响缓存

        except Exception as e:
            logger.error(
                f"读取自定义映射文件 {current_file_path} 失败: {e}；"
                "已保留上次成功加载的配置，并在问题解决前拒绝写入该文件"
            )
            # 解析/结构错误：保持缓存不动（不回退为 {}），并记住文件路径。
            # 写入路径会自行重新解析该文件，解析失败即拒绝写入，因此不会
            # 出现「用空配置覆盖用户数据」的情况。
            self._mapping_file_path = current_file_path
            # 如果读取失败，返回缓存的配置（如果有的话）
            return self._cached_mappings.copy() if self._cached_mappings else {}

    def load_regex_rules(self) -> list[dict[str, Any]]:
        """加载正则规则列表（与 mappings 同步加载）

        必须**无条件**调用 ``load_custom_mappings()``：它内部按 mtime 判断是否
        需要重载，缓存命中时只是一次 stat，开销可忽略。

        此前这里写的是 ``if not self._cached_mappings and not self._cached_rules``，
        于是「缓存里已有 mappings」时直接返回 ``_cached_rules``，永不重载 ——
        规则与映射存在同一文件、同一缓存里，但只有映射那一半会被刷新。
        实际后果：一次同步填充缓存后，用户手改正则规则，再打开映射管理页
        （``GET /api/mappings`` → ``get_all_rules()``）看到的仍是**旧规则**，
        而同步路径（先走 ``find_mapping``）用的却是新规则 —— 界面与实际行为
        不一致，且此时保存会把旧规则写回，等于撤销用户的修改。
        """
        self.load_custom_mappings()
        return list(self._cached_rules)

    def find_mapping(
        self, title: str, ori_title: str = "", season: int = 1
    ) -> tuple[str, str, str, bool]:
        """在自定义映射中查找匹配的 subject_id

        查找顺序：
        1. 季度感知精确匹配（高级格式且 season 匹配）
        2. 简单格式精确匹配（不指定 season）
        3. 正则规则匹配（按 rules 顺序）

        返回 ``(subject_id, match_type, reason, is_explicit)``：
        - match_type: ``"season"`` / ``"exact"`` / ``"regex"`` / ``""``（未命中）
        - reason: 命中说明，供 trace 使用
        - is_explicit: 该命中是否「用户显式指定了目标条目/季度」。为 True 时
          下游应把 subject_id 当作**可信的季条目**（在条目内直接按集号定位），
          并禁止跨季链改选；为 False 时沿用「填主条目、程序往后找」的历史语义。
        """
        mappings = self.load_custom_mappings()

        # 1. 季度感知精确匹配（优先尝试标题与原始标题）
        # 标题与原始标题常指向同一条目，用 seen 避免同一条配置被反复处理
        # （否则下面的「season 不可解析」警告会在每次查询里重复打印两遍）。
        seen_titles: set[str] = set()
        for candidate_title in (title, ori_title):
            if not candidate_title or candidate_title in seen_titles:
                continue
            seen_titles.add(candidate_title)
            entry = mappings.get(candidate_title)
            if not isinstance(entry, dict):
                continue
            entry_sid = str(entry.get("subject_id", "") or "")
            if not entry_sid:
                continue
            entry_season = self._parse_season(entry.get("season"))
            if entry_season is None and entry.get("season") is not None:
                # season 字段存在但不可解析（如 "S2"）：跳过而不抛异常，
                # 否则一次手滑会让整个匹配流程崩掉。
                logger.warning(
                    f"映射 {candidate_title!r} 的 season 值无法解析"
                    f"（{entry.get('season')!r}），已忽略该季度条件"
                )
            if entry_season is None or entry_season == season:
                reason = f"季度感知映射命中：{candidate_title}={entry_sid}"
                if entry_season is not None:
                    reason += f"（season={entry_season}）"
                # 声明了 season 或携带 segments = 用户显式指定了条目结构。
                # segments 必须是**非空 list** 才算数：若写成字符串等非法类型，
                # find_episode_mapping 会忽略它（要求 isinstance list），
                # 这里若用真值判断就会得出「显式」而实际无任何段可用。
                segs = entry.get("segments")
                has_segments = isinstance(segs, list) and bool(segs)
                explicit = entry_season is not None or has_segments
                return entry_sid, "season", reason, explicit

        # 2. 简单格式精确匹配（向后兼容）
        for candidate_title in (title, ori_title):
            if not candidate_title:
                continue
            entry = mappings.get(candidate_title)
            if isinstance(entry, str) and entry:
                return (
                    entry,
                    "exact",
                    f"自定义映射命中：{candidate_title}={entry}",
                    False,
                )

        # 3. 正则规则匹配
        for candidate_title in (title, ori_title):
            if not candidate_title:
                continue
            for rule in self.load_regex_rules():
                pattern = rule.get("pattern", "")
                subject_id = str(rule.get("subject_id", ""))
                if not pattern or not subject_id:
                    continue

                # season 限定（可选）：与高级格式映射一致，声明了就只在该季生效。
                # 原实现忽略该字段，导致用户对「同名不同季」的番剧无法用规则区分，
                # 且写了 season 也不报错、静默失效（最难排查的一类）。
                rule_season = self._parse_season(rule.get("season"))
                if rule.get("season") is not None and rule_season is None:
                    logger.warning(
                        f"正则规则 /{pattern}/ 的 season 值无法解析"
                        f"（{rule.get('season')!r}），已忽略该季条件"
                    )
                if rule_season is not None and rule_season != season:
                    continue

                try:
                    if re.search(pattern, candidate_title, timeout=_REGEX_TIMEOUT):
                        desc = rule.get("description", "")
                        reason = f"正则规则命中：/{pattern}/ → {subject_id}"
                        if rule_season is not None:
                            reason += f"（season={rule_season}）"
                        if desc:
                            reason += f"（{desc}）"
                        return subject_id, "regex", reason, False
                except re.error:
                    continue
                except TimeoutError:
                    logger.warning(
                        f"映射规则正则匹配超时（>{_REGEX_TIMEOUT}s），已跳过："
                        f"/{pattern}/ 标题={candidate_title!r}"
                    )
                    continue

        return "", "", "", False

    @staticmethod
    def _parse_season(value: Any) -> int | None:
        """把 season 字段安全解析为 int；空值/不可解析返回 None。

        原实现直接 ``int(entry_season)``，用户手写 ``"season": "S2"`` 会抛
        ValueError 并沿匹配管线一路炸穿（CustomMappingStep 与 MatchPipeline
        都没有兜底），导致「配了一条映射反而让同步整体失败」。
        """
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            # bool 是 int 的子类，但 "true" 当季号没有意义
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def find_episode_mapping(
        self, title: str, ori_title: str, season: int, episode: int
    ) -> tuple[str, int | None, str]:
        """在 ``segments`` 中查找集数级映射。

        用于处理「媒体库与 Bangumi 的季/集划分不同」的场景（如 TMDB 分 3 季、
        Bangumi 分 2 季，总集数相同），把两套编号拉平对齐。

        返回 ``(subject_id, target_episode, reason)``；未命中返回 ``("", None, "")``，
        此时调用方应回退到 :meth:`find_mapping` 得到的顶层 subject_id。

        段结构::

            {"season": 6, "from": 2, "to": null,
             "subject_id": "639938", "offset": 1,
             "episodes_of": {"11": 5}}

        - ``season``：该段作用于哪个媒体季；省略表示所有季
        - ``from`` / ``to``：媒体集号区间（含两端）；``to`` 为 null 表示开放上界
        - ``offset``：该段第一集对应的**目标集号**（``目标 = 集号 - from + offset``）
        - ``episodes_of``：段内逐集例外（媒体集号 → 目标集号），优先于 offset

        选取规则：先按 season 过滤，再按区间命中；多段重叠时取**数组中靠前**的
        一段（可预测，且便于用户把更具体的段写在前面）。
        """
        mappings = self.load_custom_mappings()
        if not episode:
            return "", None, ""

        for candidate_title in (title, ori_title):
            if not candidate_title:
                continue
            entry = mappings.get(candidate_title)
            if not isinstance(entry, dict):
                continue
            segments = entry.get("segments")
            if not isinstance(segments, list) or not segments:
                continue
            default_sid = str(entry.get("subject_id", "") or "")

            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                seg_season = self._parse_season(seg.get("season"))
                # season 省略（None）= 匹配任意季；声明了则必须一致
                if seg_season is not None and seg_season != season:
                    continue

                lower = self._parse_season(seg.get("from"))
                upper = self._parse_season(seg.get("to"))

                # 反向区间（to < from）永远不可能被满足，是个纯手滑。若不显式
                # 报出来，这段会永久失效且静默回退到顶层 subject_id —— 用户
                # 看到的是「配了分段但好像没生效」，极难自查。
                if lower is not None and upper is not None and upper < lower:
                    logger.warning(
                        f"集数分段映射区间反向（{candidate_title}：from={lower} "
                        f"> to={upper}），该段永远不会命中，请检查配置"
                    )
                    continue

                if lower is not None and episode < lower:
                    continue
                if upper is not None and episode > upper:
                    continue

                seg_sid = str(seg.get("subject_id", "") or "") or default_sid
                if not seg_sid:
                    continue

                # 段内逐集例外优先
                target = self._lookup_episode_of(seg.get("episodes_of"), episode)
                if target is None:
                    # offset = 该段第一集对应的目标集号；未给则默认首集对齐
                    offset = self._parse_season(seg.get("offset"))
                    base = lower if lower is not None else 1
                    target = episode - base + (offset if offset is not None else 1)

                if target <= 0:
                    logger.warning(
                        f"集数分段映射算出非法目标集号（{candidate_title} "
                        f"S{season}E{episode} → {target}），已跳过该段"
                    )
                    continue

                span = f"{lower if lower is not None else 1}-"
                span += "∞" if upper is None else str(upper)
                reason = (
                    f"集数分段映射命中：{candidate_title} S{season}E{episode} → "
                    f"subject/{seg_sid} E{target}（区间 {span}"
                )
                if seg_season is not None:
                    reason += f"，season={seg_season}"
                reason += "）"
                return seg_sid, target, reason

        return "", None, ""

    @staticmethod
    def _lookup_episode_of(raw: Any, episode: int) -> int | None:
        """在 ``episodes_of`` 中查媒体集号对应的目标集号。

        键兼容 int 与 str 两种写法（JSON 里键必然是字符串，但用户手写时
        可能写成数字，两处都要认）。

        返回 ``None`` 表示「没有可用的例外」，调用方据此回退到 offset 计算。
        注意「键不存在」与「键存在但值不可解析」都会返回 None —— 后者会
        ``logger.warning`` 提示，否则一个写错的例外值会被静默忽略并悄悄
        回退成 offset 推算结果（可能指向错误的一集，但条目是对的，极难发现）。
        """
        if not isinstance(raw, dict):
            return None
        for key in (episode, str(episode)):
            if key in raw:
                parsed = MappingService._parse_season(raw[key])
                if parsed is None:
                    logger.warning(
                        f"集数分段映射的 episodes_of 中，第 {episode} 集对应的值 "
                        f"{raw[key]!r} 不是正整数，已忽略该例外并回退到 offset 推算"
                    )
                return parsed
        return None

    def matches_any(
        self, title: str, ori_title: str = "", season: int = 1, episode: int = 0
    ) -> bool:
        """该请求是否命中任意自定义映射（标题级或集数分段级）。

        **这是「是否命中自定义映射」的唯一权威判定**。屏蔽关键词的豁免逻辑
        （SyncService._is_title_blocked）与匹配管线的 CustomMappingStep 都必须
        经由它判断，避免两处各写一份而口径漂移。

        历史教训：_is_title_blocked 曾自行解包 find_mapping 的返回值，在
        find_mapping 增加返回值后因解包数不匹配抛 ValueError，又被自身的
        ``except Exception`` 吞掉，导致「自定义映射优先于屏蔽词」静默失效。
        """
        if episode:
            seg_sid, _target, _reason = self.find_episode_mapping(
                title, ori_title, season, episode
            )
            if seg_sid:
                return True
        mapping_sid, _match_type, _reason, _explicit = self.find_mapping(
            title=title or "", ori_title=ori_title or "", season=season
        )
        # 显式 bool()：调用方可能被 mock 替换（返回 MagicMock 等真值对象），
        # 不归一化会让「未命中」被当成命中，进而误豁免屏蔽词。
        return bool(mapping_sid)

    def reload_custom_mappings(self) -> dict[str, Any]:
        """强制重新加载自定义映射配置"""
        # 清空缓存强制重新加载
        self._cached_mappings = {}
        self._cached_rules = []
        self._mapping_file_path = None
        self._last_modified_time = 0

        logger.info("强制重新加载自定义映射配置")
        return self.load_custom_mappings()

    @staticmethod
    def _atomic_write(path: str, data: dict[str, Any]) -> None:
        """原子写入 JSON：先写同目录临时文件，再 os.replace 覆盖。

        直接 open(path, "w") 会在写入过程中把文件截断为 0 字节，进程崩溃
        或磁盘写满时会留下损坏的配置文件（用户映射全丢）。os.replace 在
        同一文件系统上是原子的，读者要么看到旧内容、要么看到新内容。
        """
        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp_path = tempfile.mkstemp(
            dir=directory, prefix=".bangumi_mapping.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            # 清理临时文件；失败不应掩盖原始异常
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def update_custom_mappings(
        self, mappings: dict[str, Any], rules: list[dict[str, Any]] | None = None
    ) -> bool:
        """更新自定义映射配置

        :param mappings: 映射字典（支持简单/高级格式混合）
        :param rules: 正则规则列表，None 表示保留现有规则

        安全保证：
        - 现有文件存在但解析失败（损坏）时**拒绝写入**并返回 False，
          避免用内存中的空/残缺数据覆盖用户配置。
        - 保留文件顶层的自定义键与元字段，只更新 mappings / rules。
        - 原子写入（临时文件 + os.replace），不会留下截断的半个文件。
        """
        mapping_file_path = self._find_existing_path() or "./bangumi_mapping.json"
        return self.update_custom_mappings_with_path(
            mapping_file_path, mappings, rules=rules
        )

    def update_custom_mappings_with_path(
        self,
        mapping_file_path: str,
        mappings: dict[str, Any],
        rules: list[dict[str, Any]] | None = None,
    ) -> bool:
        """按指定路径更新映射配置（:meth:`update_custom_mappings` 的实现体）

        单独抽出路径参数，便于调用方精确指定目标文件（测试与多环境部署）。
        """
        try:
            # 读取现有配置（保留元字段与用户自定义顶层键）。
            # 损坏时中止而不是当成空配置 —— 否则一次保存就会清空用户全部配置。
            config_data: dict[str, Any] = {}
            if os.path.exists(mapping_file_path):
                try:
                    with open(mapping_file_path, encoding="utf-8") as f:
                        loaded = json.load(f)
                    if not isinstance(loaded, dict):
                        raise ValueError("映射配置顶层必须是对象")
                    config_data = loaded
                except Exception as e:
                    logger.error(
                        f"映射配置文件 {mapping_file_path} 解析失败，已拒绝写入以免"
                        f"覆盖用户配置: {e}。请先修复该文件的 JSON 语法。"
                    )
                    return False

            # 未显式提供 rules 时，从磁盘读原始（未过滤）rules —— 内存里的
            # _cached_rules 是过滤后的匹配用视图，写回它会静默删除用户的
            # 非法/不完整规则。
            if rules is None:
                rules = config_data.get("rules", []) or []

            # 保留元字段，更新 mappings 和 rules
            config_data.setdefault(
                "_comment",
                "自定义映射配置文件 - 用于处理程序通过搜索无法自动匹配的项目",
            )
            config_data.setdefault(
                "_format",
                "番剧名: bangumi_subject_id 或 {'subject_id': 'id', 'season': 1, "
                "'segments': [{'season': 6, 'from': 2, 'to': null, "
                "'subject_id': '639938', 'offset': 1}]}",
            )
            config_data["_note"] = (
                "映射是全局配置（对所有用户与账号生效）。bangumi_subject_id 一般"
                "配置第一季/主条目，程序会自动往后找；若该季在 Bangumi 上是独立"
                "条目，请用 {'subject_id': id, 'season': N} 或 segments 指定。"
                "segments 的 offset 表示该段第一集对应的目标集号。"
            )
            config_data["mappings"] = mappings
            config_data["rules"] = rules

            # 原子写入
            self._atomic_write(mapping_file_path, config_data)

            # 重新加载映射
            self.reload_custom_mappings()

            logger.info(
                f"自定义映射已更新，共 {len(mappings)} 个映射、{len(rules)} 条规则"
            )
            return True
        except Exception as e:
            logger.error(f"更新自定义映射失败: {e}")
            return False

    def upsert_single_mapping(
        self, title: str, subject_id: str, season: int = 1
    ) -> bool:
        """新增或更新单条映射（读全量→合并→写回）

        season > 1 写高级格式 ``{"subject_id": str(subject_id), "season": int(season)}``，
        season <= 1 写简单格式 ``str(subject_id)``（与 confirm_pending_candidate 原逻辑一致）。
        返回是否写入成功。

        **合并而非重建**：标题下若已存在同 season 的配置对象，只更新
        ``subject_id``，保留 ``segments`` 等其它字段，避免覆盖式写入把
        用户配置的集数分段表抹掉。
        """
        all_mappings = self.get_all_mappings()
        existing = all_mappings.get(title)

        if season > 1:
            if isinstance(existing, dict):
                merged = dict(existing)
                merged["subject_id"] = str(subject_id)
                merged["season"] = int(season)
                all_mappings[title] = merged
            else:
                all_mappings[title] = {
                    "subject_id": str(subject_id),
                    "season": int(season),
                }
        else:
            # season<=1 用简单格式；已有富配置对象时保留其结构只改 subject_id，
            # 不降级成裸字符串（否则会丢掉 segments/season 等信息）。
            if isinstance(existing, dict):
                merged = dict(existing)
                merged["subject_id"] = str(subject_id)
                all_mappings[title] = merged
            else:
                all_mappings[title] = str(subject_id)
        return self.update_custom_mappings(all_mappings)

    def delete_single_mapping(self, title: str) -> bool:
        """删除单条映射（读全量→删除→写回）

        返回是否删除成功（不存在也算成功）。
        """
        all_mappings = self.get_all_mappings()
        if title not in all_mappings:
            return True  # 不存在视为成功
        del all_mappings[title]
        return self.update_custom_mappings(all_mappings)

    def delete_custom_mapping(self, title: str) -> bool:
        """删除自定义映射

        向后兼容包装：不存在时返回 False（与历史行为一致），
        存在时委托 :meth:`delete_single_mapping` 完成读全量→删除→写回。
        """
        try:
            mappings = self.load_custom_mappings()
            if title not in mappings:
                logger.warning(f'映射 "{title}" 不存在')
                return False
            if self.delete_single_mapping(title):
                logger.info(f'映射 "{title}" 已删除')
                return True
            return False
        except Exception as e:
            logger.error(f"删除自定义映射失败: {e}")
            return False

    def get_mappings_status(self) -> dict[str, Any]:
        """获取映射配置状态"""
        mappings = self.load_custom_mappings()
        rules = self.load_regex_rules()
        return {
            "mappings_count": len(mappings),
            "rules_count": len(rules),
            "file_path": self._mapping_file_path,
            "last_modified": self._last_modified_time,
            "cached": bool(self._cached_mappings),
            "mappings": mappings,
            "rules": rules,
        }

    def get_all_mappings(self) -> dict[str, Any]:
        """获取所有映射（可能包含简单/高级格式混合）"""
        return self.load_custom_mappings()

    def get_all_rules(self) -> list[dict[str, Any]]:
        """获取所有正则规则"""
        return self.load_regex_rules()

    def update_mappings(self, mappings: dict[str, Any]) -> bool:
        """更新映射（别名，保留现有 rules）"""
        return self.update_custom_mappings(mappings)


# 全局映射服务实例（惰性：首次访问才创建，可经 set_mapping_service 注入替换）
_injectable = Injectable(MappingService)


def get_mapping_service() -> MappingService:
    """获取全局映射服务单例（惰性创建）。"""
    return _injectable.get()


def set_mapping_service(instance: MappingService) -> None:
    """替换映射服务实例（测试/DI 注入）。"""
    _injectable.set(instance)


def reset_mapping_service() -> None:
    """复位映射服务单例，下次访问时按工厂重建。"""
    _injectable.reset()


def __getattr__(name: str) -> Any:
    """向后兼容：``from ...mapping_service import mapping_service`` 仍可访问。"""
    if name == "mapping_service":
        return _injectable.get()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
