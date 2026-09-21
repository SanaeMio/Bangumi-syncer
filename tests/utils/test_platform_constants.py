"""bangumi_constants 死编码守卫测试（CONSTANTS-HARDENING.md 范围 A）。

三层防线：
1. **官方快照守卫**：本地平台表必须与 bangumi/common subject_platforms.yml
   （快照时间 2026-09-09）逐项一致——Bangumi 上游新增/改动平台时本测试失败，
   强制有意识同步，而不是静默死编码（解码空串 → 回落默认权重）。
2. **权重表全覆盖守卫**：任何可解码出的中文名都必须在 TV/Movie 两套权重基线
   中有显式权重，杜绝「解码成功但权重静默回落 50」的死名字。
3. **活数据守卫**：本地 archive 库中出现的全部 (type, platform) 编码必须
   100% 可解码（无 DB 时 skip，CI 不受影响）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.utils.bangumi_constants import (
    ANIME_PLATFORMS,
    PLATFORMS_BY_TYPE,
    REAL_PLATFORMS,
    RELATIONS,
    SUBJECT_TYPE_ANIME,
    SUBJECT_TYPE_REAL,
)
from app.utils.text_constants import (
    _MOVIE_MODE_BASE_WEIGHTS,
    _TV_MODE_BASE_WEIGHTS,
    DEFAULT_PLATFORM_WEIGHT,
    PLATFORM_WEIGHT_MOVIE_MODE,
    PLATFORM_WEIGHT_TV_MODE,
    resolve_platform_name,
)

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_DB = ROOT / "data" / "archive" / "bangumi_archive_a.db"

# 官方快照：https://github.com/bangumi/common subject_platforms.yml
# 快照时间 2026-09-09（anime/real 分域，id → type_cn）
OFFICIAL_ANIME_PLATFORMS: dict[int, str] = {
    1: "TV",
    2: "OVA",
    3: "剧场版",
    4: "短片",
    5: "WEB",
    2006: "动态漫画",
    0: "其他",
}
OFFICIAL_REAL_PLATFORMS: dict[int, str] = {
    0: "其他",
    1: "日剧",
    2: "欧美剧",
    3: "华语剧",
    6001: "电视剧",
    6002: "电影",
    6003: "演出",
    6004: "综艺",
}

# 官方快照：subject_relations.yml（anime/real 共用，id → cn）
OFFICIAL_RELATIONS: dict[int, str] = {
    1: "改编",
    2: "前传",
    3: "续集",
    4: "总集篇",
    5: "全集",
    6: "番外篇",
    7: "角色出演",
    8: "相同世界观",
    9: "不同世界观",
    10: "不同演绎",
    11: "衍生",
    12: "主线故事",
    14: "联动",
    99: "其他",
}


class TestOfficialSnapshotGuard:
    """上游同步守卫：本地表 ≠ 官方快照 → 失败并提示重新比对。"""

    def test_anime_platforms_match_official(self) -> None:
        assert ANIME_PLATFORMS == OFFICIAL_ANIME_PLATFORMS, (
            "ANIME_PLATFORMS 与 bangumi/common 快照不一致："
            "请比对 https://github.com/bangumi/common subject_platforms.yml，"
            "确认上游变更后同步本表与本测试快照（避免静默死编码）"
        )

    def test_real_platforms_match_official(self) -> None:
        assert REAL_PLATFORMS == OFFICIAL_REAL_PLATFORMS, (
            "REAL_PLATFORMS 与 bangumi/common 快照不一致："
            "请比对 https://github.com/bangumi/common subject_platforms.yml，"
            "确认上游变更后同步本表与本测试快照（避免静默死编码）"
        )

    def test_relations_match_official(self) -> None:
        assert RELATIONS == OFFICIAL_RELATIONS, (
            "RELATIONS 与 bangumi/common subject_relations.yml 快照不一致"
        )

    def test_platforms_by_type_covers_allowed_types(self) -> None:
        """分域解码表必须覆盖 archive 允许的全部 subject.type。"""
        assert set(PLATFORMS_BY_TYPE) == {SUBJECT_TYPE_ANIME, SUBJECT_TYPE_REAL}
        assert PLATFORMS_BY_TYPE[SUBJECT_TYPE_ANIME] is ANIME_PLATFORMS
        assert PLATFORMS_BY_TYPE[SUBJECT_TYPE_REAL] is REAL_PLATFORMS


class TestWeightTableCoverage:
    """死名字守卫：可解码出的中文名必须在权重基线中有显式条目。

    注：不能用「≠ DEFAULT_PLATFORM_WEIGHT」判断——TV 模式下「剧场版/电影」
    显式配权 50 恰等于默认值，属有意设计；真正要防的是基线表漏配 key。
    """

    @pytest.mark.parametrize(
        "base_table", [_TV_MODE_BASE_WEIGHTS, _MOVIE_MODE_BASE_WEIGHTS]
    )
    def test_every_decodable_name_has_explicit_weight(self, base_table) -> None:
        names = {*(ANIME_PLATFORMS.values()), *(REAL_PLATFORMS.values())}
        missing = [n for n in names if n not in base_table]
        assert missing == [], (
            f"平台名 {missing} 未在权重基线表中显式配置（将静默回落 "
            f"{DEFAULT_PLATFORM_WEIGHT}）：请同步 _TV_MODE_BASE_WEIGHTS / "
            "_MOVIE_MODE_BASE_WEIGHTS"
        )

    def test_weight_tables_share_same_key_set(self) -> None:
        assert set(PLATFORM_WEIGHT_TV_MODE) == set(PLATFORM_WEIGHT_MOVIE_MODE)
        names = {*(ANIME_PLATFORMS.values()), *(REAL_PLATFORMS.values())}
        assert set(PLATFORM_WEIGHT_TV_MODE) == names


class TestResolvePlatformNameHardening:
    """resolve_platform_name 边界加固（isdecimal / 跨域警示）。"""

    def test_superscript_digit_no_crash(self) -> None:
        """'²'.isdigit() 为 True 但 int() 会崩 —— isdecimal 修复后透传不抛异常。"""
        assert resolve_platform_name("²", SUBJECT_TYPE_ANIME) == "²"
        assert resolve_platform_name("①", SUBJECT_TYPE_ANIME) == "①"

    def test_circled_digit_no_crash(self) -> None:
        assert resolve_platform_name("1²", SUBJECT_TYPE_ANIME) == "1²"

    def test_fullwidth_digits_decode(self) -> None:
        """全角数字是 isdecimal 且 int() 可解析 → 正常解码。"""
        assert resolve_platform_name("１", SUBJECT_TYPE_ANIME) == "TV"
        assert resolve_platform_name("６００２", SUBJECT_TYPE_REAL) == "电影"

    def test_negative_number_passthrough(self) -> None:
        """'-1' 非十进制数字串 → 原样透传（权重表查不到 → 回落默认，不抛异常）。"""
        assert resolve_platform_name("-1", SUBJECT_TYPE_ANIME) == "-1"
        assert resolve_platform_name(-1, SUBJECT_TYPE_ANIME) == ""

    def test_real_codes_without_type_are_dead(self) -> None:
        """文档化已知跨域死编码：type 未知按动画表 → 三次元专属编码解码为空。"""
        assert resolve_platform_name("6001", None) == ""
        assert resolve_platform_name("6004", None) == ""

    def test_all_official_codes_decode_with_type(self) -> None:
        """官方全集逐项解码：任何 (type, code) 组合都不允许返回空串。"""
        for code, cn in OFFICIAL_ANIME_PLATFORMS.items():
            assert resolve_platform_name(str(code), SUBJECT_TYPE_ANIME) == cn
        for code, cn in OFFICIAL_REAL_PLATFORMS.items():
            assert resolve_platform_name(str(code), SUBJECT_TYPE_REAL) == cn


@pytest.mark.skipif(
    not ARCHIVE_DB.exists(), reason="本地无 archive 数据库（CI 无数据源）"
)
class TestArchiveLiveDataGuard:
    """活数据守卫：archive 库中实际出现的编码必须 100% 可解码。"""

    def test_all_platform_codes_decode(self) -> None:
        conn = sqlite3.connect(f"file:{ARCHIVE_DB}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT DISTINCT type, platform FROM subject "
                "WHERE type IN (2, 6) AND platform IS NOT NULL AND TRIM(platform) <> ''"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "archive 库无 platform 数据？"
        dead = [(t, p) for t, p in rows if not resolve_platform_name(p, t)]
        assert dead == [], (
            f"archive 库出现无法解码的 (type, platform) 编码：{dead}——"
            "Bangumi 上游新增了平台？请同步 bangumi_constants 与官方快照测试"
        )
