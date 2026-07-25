"""
映射服务模块

支持两种映射格式（向后兼容）：
1. 简单格式：``"番剧名": "subject_id"``，标题精确匹配，所有季度共用一个 ID
2. 高级格式：``"番剧名": {"subject_id": "123", "season": 2}``，指定季度才命中

另支持正则规则匹配（``rules`` 数组），用正则表达式匹配标题，适合处理续作/特殊命名。
"""

from __future__ import annotations

import json
import os
from typing import Any

import regex as re

from ..core.logging import logger

# 单条正则规则匹配的超时（秒）。
# regex 库支持 timeout 参数，可防御用户配置的病态正则引发的灾难性回溯。
_REGEX_TIMEOUT = 1.0


class MappingService:
    """映射服务"""

    def __init__(self) -> None:
        self._cached_mappings: dict[str, Any] = {}
        self._cached_rules: list[dict[str, Any]] = []
        self._mapping_file_path: str | None = None
        self._last_modified_time: float = 0

    def load_custom_mappings(self) -> dict[str, Any]:
        """从外部JSON文件读取自定义映射配置

        返回的 mappings 字典值可能为 str（简单格式）或 dict（高级格式），
        调用方应使用 :meth:`find_mapping` 而非直接查表，以统一处理两种格式。
        """
        # 定义可能的配置文件路径
        mapping_file_paths = [
            "./bangumi_mapping.json",  # 当前目录
            "/app/config/bangumi_mapping.json",  # Docker挂载目录
            "/app/bangumi_mapping.json",  # Docker内部目录
        ]

        # 查找存在的配置文件
        current_file_path = None
        for mapping_file in mapping_file_paths:
            if os.path.exists(mapping_file):
                current_file_path = mapping_file
                break

        # 如果没有找到配置文件，创建默认文件
        if not current_file_path:
            default_file = "./bangumi_mapping.json"
            try:
                default_config = {
                    "_comment": "自定义映射配置文件 - 用于处理程序通过搜索无法自动匹配的项目，参考_examples的格式将新内容添加到mappings中",
                    "_format": "番剧名: bangumi_subject_id 或 {'subject_id': 'id', 'season': 1}",
                    "_note": "bangumi_subject_id需要配置第一季的，程序会自动往后找",
                    "_examples": {
                        "魔王学院的不适任者": "292222",
                        "我推的孩子": "386809",
                    },
                    "_rules_example": [
                        {
                            "pattern": "^.*之.*刃$",
                            "subject_id": "123456",
                            "description": "示例：匹配标题以「之刃」结尾的番剧",
                        }
                    ],
                    "mappings": {"假面骑士加布": "502002"},
                    "rules": [],
                }
                with open(default_file, "w", encoding="utf-8") as f:
                    json.dump(default_config, f, ensure_ascii=False, indent=2)
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
                    mappings = data.get("mappings", {})
                    rules = data.get("rules", []) or []

                    # 规范化 rules：过滤无效条目，预编译正则
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
                    )
            else:
                logger.debug(
                    f"使用缓存的映射配置，共 {len(self._cached_mappings)} 个映射、"
                    f"{len(self._cached_rules)} 条规则"
                )

            return self._cached_mappings.copy()  # 返回副本以避免外部修改影响缓存

        except Exception as e:
            logger.error(f"读取自定义映射文件 {current_file_path} 失败: {e}")
            # 如果读取失败，返回缓存的配置（如果有的话）
            return self._cached_mappings.copy() if self._cached_mappings else {}

    def load_regex_rules(self) -> list[dict[str, Any]]:
        """加载正则规则列表（与 mappings 同步加载）"""
        # 确保已加载
        if not self._cached_mappings and not self._cached_rules:
            self.load_custom_mappings()
        return list(self._cached_rules)

    def find_mapping(
        self, title: str, ori_title: str = "", season: int = 1
    ) -> tuple[str, str, str]:
        """在自定义映射中查找匹配的 subject_id

        查找顺序：
        1. 季度感知精确匹配（高级格式且 season 匹配）
        2. 简单格式精确匹配（不指定 season）
        3. 正则规则匹配（按 rules 顺序）

        返回 ``(subject_id, match_type, reason)``：
        - match_type: ``"season"`` / ``"exact"`` / ``"regex"`` / ``""``（未命中）
        - reason: 命中说明，供 trace 使用
        """
        mappings = self.load_custom_mappings()

        # 1. 季度感知精确匹配（优先尝试标题与原始标题）
        for candidate_title in (title, ori_title):
            if not candidate_title:
                continue
            entry = mappings.get(candidate_title)
            if isinstance(entry, dict):
                entry_sid = str(entry.get("subject_id", ""))
                entry_season = entry.get("season")
                if entry_sid and (entry_season is None or int(entry_season) == season):
                    reason = f"季度感知映射命中：{candidate_title}={entry_sid}"
                    if entry_season is not None:
                        reason += f"（season={entry_season}）"
                    return entry_sid, "season", reason

        # 2. 简单格式精确匹配（向后兼容）
        for candidate_title in (title, ori_title):
            if not candidate_title:
                continue
            entry = mappings.get(candidate_title)
            if isinstance(entry, str) and entry:
                return entry, "exact", f"自定义映射命中：{candidate_title}={entry}"

        # 3. 正则规则匹配
        for candidate_title in (title, ori_title):
            if not candidate_title:
                continue
            for rule in self.load_regex_rules():
                pattern = rule.get("pattern", "")
                subject_id = str(rule.get("subject_id", ""))
                if not pattern or not subject_id:
                    continue
                try:
                    if re.search(pattern, candidate_title, timeout=_REGEX_TIMEOUT):
                        desc = rule.get("description", "")
                        reason = f"正则规则命中：/{pattern}/ → {subject_id}"
                        if desc:
                            reason += f"（{desc}）"
                        return subject_id, "regex", reason
                except re.error:
                    continue
                except TimeoutError:
                    logger.warning(
                        f"映射规则正则匹配超时（>{_REGEX_TIMEOUT}s），已跳过："
                        f"/{pattern}/ 标题={candidate_title!r}"
                    )
                    continue

        return "", "", ""

    def reload_custom_mappings(self) -> dict[str, Any]:
        """强制重新加载自定义映射配置"""
        # 清空缓存强制重新加载
        self._cached_mappings = {}
        self._cached_rules = []
        self._mapping_file_path = None
        self._last_modified_time = 0

        logger.info("强制重新加载自定义映射配置")
        return self.load_custom_mappings()

    def update_custom_mappings(
        self, mappings: dict[str, Any], rules: list[dict[str, Any]] | None = None
    ) -> bool:
        """更新自定义映射配置

        :param mappings: 映射字典（支持简单/高级格式混合）
        :param rules: 正则规则列表，None 表示保留现有规则
        """
        try:
            # 找到配置文件路径
            mapping_file_paths = [
                "./bangumi_mapping.json",
                "/app/config/bangumi_mapping.json",
                "/app/bangumi_mapping.json",
            ]

            mapping_file_path = None
            for path in mapping_file_paths:
                if os.path.exists(path):
                    mapping_file_path = path
                    break

            if not mapping_file_path:
                mapping_file_path = "./bangumi_mapping.json"

            # 如果未提供 rules，读取现有 rules
            if rules is None:
                try:
                    with open(mapping_file_path, encoding="utf-8") as f:
                        existing_data = json.load(f)
                        rules = existing_data.get("rules", []) or []
                except Exception:
                    rules = []

            # 读取现有配置（保留 _comment 等元字段）
            config_data: dict[str, Any] = {}
            try:
                with open(mapping_file_path, encoding="utf-8") as f:
                    config_data = json.load(f)
            except Exception:
                config_data = {}

            # 保留元字段，更新 mappings 和 rules
            config_data.setdefault(
                "_comment",
                "自定义映射配置文件 - 用于处理程序通过搜索无法自动匹配的项目",
            )
            config_data.setdefault(
                "_format",
                "番剧名: bangumi_subject_id 或 {'subject_id': 'id', 'season': 1}",
            )
            config_data["_note"] = (
                "bangumi_subject_id需要配置第一季的，程序会自动往后找"
            )
            config_data["mappings"] = mappings
            config_data["rules"] = rules

            # 保存配置
            with open(mapping_file_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)

            # 重新加载映射
            self.reload_custom_mappings()

            logger.info(
                f"自定义映射已更新，共 {len(mappings)} 个映射、{len(rules)} 条规则"
            )
            return True
        except Exception as e:
            logger.error(f"更新自定义映射失败: {e}")
            return False

    def delete_custom_mapping(self, title: str) -> bool:
        """删除自定义映射"""
        try:
            mappings = self.load_custom_mappings()
            if title in mappings:
                del mappings[title]

                # 更新配置文件（保留现有 rules）
                if self.update_custom_mappings(mappings):
                    logger.info(f'映射 "{title}" 已删除')
                    return True
                else:
                    return False

            logger.warning(f'映射 "{title}" 不存在')
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


# 全局映射服务实例
mapping_service = MappingService()
