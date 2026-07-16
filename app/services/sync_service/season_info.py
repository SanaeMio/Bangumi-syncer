"""
季度信息检测 Mixin：检查标题中是否包含季度关键词。
纯函数逻辑，不依赖任何单例。
"""

from __future__ import annotations

import re

from ...core.logging import logger

# 中文数字 → 阿拉伯数字（1-10，支持"十一"~"十九"组合）
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


class SeasonInfoMixin:
    """季度信息检测（纯逻辑，无外部依赖）。"""

    def _get_explicit_season_from_title(self, title: str) -> int | None:
        """从标题中提取明确声明的季度编号。

        返回值：
        - 明确声明第N季时返回 N（>=1）
        - 标题不含季度声明时返回 None（可能是第一季本体，也可能是总集篇等）

        覆盖形式：第X季/第X期（阿拉伯与中文数字）、Xnd/Xrd/Xth season、Season X
        """
        if not title:
            return None
        text = title.strip()

        # "第X期" / "第X季"（阿拉伯数字）
        m = re.search(r"第\s*(\d+)\s*[期季]", text)
        if m:
            return int(m.group(1))
        # "第X期" / "第X季"（中文数字，含"十一"~"十九"）
        m = re.search(r"第\s*([一二三四五六七八九十]+)\s*[期季]", text)
        if m:
            cn = m.group(1)
            if len(cn) == 1:
                return _CN_NUM.get(cn)
            if cn.startswith("十"):
                return 10 + _CN_NUM.get(cn[1], 0)
            return _CN_NUM.get(cn)
        # "Xnd/Xrd/Xth season"
        m = re.search(r"(\d+)(?:st|nd|rd|th)\s+season", text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        # "Season X"（需带数字，避免误匹配"Season"单词本身）
        m = re.search(r"season\s*(\d+)", text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None

    def _check_season_info_in_title(self, title: str, season: int) -> bool:
        """检查标题中是否包含季度信息"""
        # 中文数字映射
        chinese_numbers = {
            1: "一",
            2: "二",
            3: "三",
            4: "四",
            5: "五",
            6: "六",
            7: "七",
            8: "八",
            9: "九",
            10: "十",
        }

        # 数字形式
        season_keywords = [
            f"第{season}季",
            f"第{season}期",
            f"{season}期",
            f"{season}季",
            f"Season {season}",
            f"S{season}",
        ]

        # 中文数字形式
        if season in chinese_numbers:
            chinese_num = chinese_numbers[season]
            season_keywords.extend(
                [
                    f"第{chinese_num}季",
                    f"第{chinese_num}期",
                    f"{chinese_num}期",
                    f"{chinese_num}季",
                ]
            )

        # 检查基本季度关键词
        for keyword in season_keywords:
            if keyword in title:
                logger.debug(f'匹配标题 "{title}" 包含季度信息: {keyword}')
                return True

        # 检查更复杂的格式
        chinese_num = chinese_numbers.get(season, "")

        # 基础模式：第X季 或 X季
        base_patterns = [rf"第{season}季", rf"{season}季"]

        # 如果有中文数字，添加中文数字模式
        if chinese_num:
            base_patterns.extend([rf"第{chinese_num}季", rf"{chinese_num}季"])

        # 部分标识符
        part_indicators = [r"\s+上半", r"\s+下半", r"\s+第2部分", r"\s+第二部分"]

        # 组合所有模式
        for base_pattern in base_patterns:
            for indicator in part_indicators:
                full_pattern = base_pattern + indicator
                if re.search(full_pattern, title):
                    logger.debug(f'匹配标题 "{title}" 包含复杂季度信息: {full_pattern}')
                    return True

        return False
