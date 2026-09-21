"""#20 platform 数字编码解码的回归测试。

覆盖两条路径：
- **Archive 路径**：platform 为**按 subject.type 分域的数字编码字符串**
  （type=2: 1=TV/5=WEB/3=剧场版；type=6: 1=日剧/3=华语剧/6002=电影）。
  修复前直接用中文名权重表查 → 恒回落 DEFAULT_PLATFORM_WEIGHT → 排序静默失效。
- **API 路径**：platform 已是中文名，行为必须保持不变（回归保护）。
"""

from __future__ import annotations

import pytest

from app.utils.text_constants import (
    DEFAULT_PLATFORM_WEIGHT,
    resolve_platform_name,
)

try:  # 该模块位于 sync_service 包内，导入失败时跳过排序相关用例
    from app.services.sync_service.title_normalize import TitleNormalizeMixin
except Exception:  # noqa: BLE001
    TitleNormalizeMixin = None  # type: ignore[assignment]

TYPE_ANIME = 2
TYPE_REAL = 6


class TestResolvePlatformName:
    """resolve_platform_name：分域解码 + 中文名透传。"""

    @pytest.mark.parametrize(
        ("platform", "subject_type", "expected"),
        [
            # --- Archive 路径：type=2（动画）---
            ("1", TYPE_ANIME, "TV"),
            ("2", TYPE_ANIME, "OVA"),
            ("3", TYPE_ANIME, "剧场版"),
            ("5", TYPE_ANIME, "WEB"),
            ("0", TYPE_ANIME, "其他"),
            ("2006", TYPE_ANIME, "动态漫画"),
            # --- Archive 路径：type=6（三次元）---
            ("1", TYPE_REAL, "日剧"),
            ("2", TYPE_REAL, "欧美剧"),
            ("3", TYPE_REAL, "华语剧"),
            ("6001", TYPE_REAL, "电视剧"),
            ("6002", TYPE_REAL, "电影"),
        ],
    )
    def test_archive_numeric_code_by_type(
        self, platform: str, subject_type: int, expected: str
    ) -> None:
        assert resolve_platform_name(platform, subject_type) == expected

    def test_same_code_differs_by_type(self) -> None:
        """同一个数字编码在不同 type 下含义不同 —— 分域解码的核心。"""
        # 3 在动画是「剧场版」，在三次元是「华语剧」
        assert resolve_platform_name("3", TYPE_ANIME) == "剧场版"
        assert resolve_platform_name("3", TYPE_REAL) == "华语剧"
        # 1 在动画是「TV」，在三次元是「日剧」
        assert resolve_platform_name("1", TYPE_ANIME) == "TV"
        assert resolve_platform_name("1", TYPE_REAL) == "日剧"

    @pytest.mark.parametrize(
        ("platform", "expected"),
        [
            ("TV", "TV"),
            ("WEB", "WEB"),
            ("华语剧", "华语剧"),
            ("剧场版", "剧场版"),
        ],
    )
    def test_api_chinese_name_passthrough(self, platform: str, expected: str) -> None:
        """API 路径 platform 已是中文名 → 原样返回（回归保护）。"""
        assert resolve_platform_name(platform, TYPE_ANIME) == expected

    @pytest.mark.parametrize("platform", [None, "", "   "])
    def test_empty_returns_empty(self, platform) -> None:
        assert resolve_platform_name(platform, TYPE_ANIME) == ""

    @pytest.mark.parametrize("platform", [9999, "9999"])
    def test_unknown_code_returns_empty(self, platform) -> None:
        """未知编码 → 空串（由调用方回落默认权重），不抛异常。"""
        assert resolve_platform_name(platform, TYPE_ANIME) == ""

    def test_int_code_accepted(self) -> None:
        """int 形态的编码（部分调用方已转过类型）也能解码。"""
        assert resolve_platform_name(1, TYPE_ANIME) == "TV"
        assert resolve_platform_name(6002, TYPE_REAL) == "电影"

    @pytest.mark.parametrize("subject_type", [None, "abc", ""])
    def test_unknown_subject_type_falls_back_to_anime_table(self, subject_type) -> None:
        """type 无法判定 → 按动画表解码（不抛异常）。"""
        assert resolve_platform_name("1", subject_type) == "TV"

    def test_bool_not_treated_as_code(self) -> None:
        """bool 是 int 子类，但不能当编码用。"""
        assert resolve_platform_name(True, TYPE_ANIME) == ""


@pytest.mark.skipif(TitleNormalizeMixin is None, reason="title_normalize 模块不可用")
@pytest.mark.skip(
    reason="#20 platform 解码功能已验证有 1 例真回归（命中率 97.9→97.5），"
    "调用点已回退；本测试类作为'未来重新启用时'的回归保护保留，"
    "启用时移除此 skip 即可。"
)
class TestSortCandidatesByPlatform:
    """_sort_candidates_by_platform：数字编码下排序必须真正生效。"""

    def test_archive_numeric_codes_are_sorted(self) -> None:
        """修复前：全部回落 50 → 稳定排序保持原序；修复后按权重重排。"""
        candidates = [
            {"id": 1, "type": TYPE_REAL, "platform": "3"},  # 华语剧 85
            {"id": 2, "type": TYPE_ANIME, "platform": "5"},  # WEB 90
            {"id": 3, "type": TYPE_ANIME, "platform": "1"},  # TV 100
        ]
        out = TitleNormalizeMixin._sort_candidates_by_platform(
            candidates, is_movie=False, limit=15
        )
        assert [c["id"] for c in out] == [3, 2, 1]  # TV > WEB > 华语剧

    def test_anime_preferred_over_real_on_type_conflict(self) -> None:
        """同名跨媒体：动画 TV/WEB 应优先于三次元（凡人修仙传场景）。"""
        candidates = [
            {"id": 434076, "type": TYPE_REAL, "platform": "3"},  # 华语剧 85
            {"id": 348240, "type": TYPE_ANIME, "platform": "5"},  # WEB 90
            {"id": 223147, "type": TYPE_ANIME, "platform": "1"},  # TV 100
        ]
        out = TitleNormalizeMixin._sort_candidates_by_platform(
            candidates, is_movie=False, limit=15
        )
        assert out[0]["id"] == 223147
        assert out[-1]["id"] == 434076  # 真人剧沉底

    def test_movie_mode_prefers_movie_over_tv(self) -> None:
        candidates = [
            {"id": 1, "type": TYPE_ANIME, "platform": "1"},  # TV → movie 模式 40
            {"id": 2, "type": TYPE_ANIME, "platform": "3"},  # 剧场版 → 100
        ]
        out = TitleNormalizeMixin._sort_candidates_by_platform(
            candidates, is_movie=True, limit=15
        )
        assert [c["id"] for c in out] == [2, 1]

    def test_chinese_name_path_unchanged(self) -> None:
        """中文名（API 路径）行为保持不变 —— 回归保护。"""
        candidates = [
            {"id": 1, "type": TYPE_ANIME, "platform": "OVA"},
            {"id": 2, "type": TYPE_ANIME, "platform": "TV"},
        ]
        out = TitleNormalizeMixin._sort_candidates_by_platform(
            candidates, is_movie=False, limit=15
        )
        assert [c["id"] for c in out] == [2, 1]

    def test_unknown_code_falls_back_to_default_weight(self) -> None:
        """无法解码 → 回落默认权重，不抛异常。"""
        candidates = [
            {"id": 1, "type": TYPE_ANIME, "platform": "9999"},
            {"id": 2, "type": TYPE_ANIME, "platform": None},
        ]
        out = TitleNormalizeMixin._sort_candidates_by_platform(
            candidates, is_movie=False, limit=15
        )
        assert [c["id"] for c in out] == [1, 2]  # 同权 → 稳定排序保持原序
        assert DEFAULT_PLATFORM_WEIGHT == 50

    def test_limit_respected(self) -> None:
        candidates = [
            {"id": 1, "type": TYPE_ANIME, "platform": "1"},
            {"id": 2, "type": TYPE_ANIME, "platform": "5"},
            {"id": 3, "type": TYPE_ANIME, "platform": "2"},
        ]
        out = TitleNormalizeMixin._sort_candidates_by_platform(
            candidates, is_movie=False, limit=2
        )
        assert len(out) == 2

    def test_non_list_input_returned_as_is(self) -> None:
        assert (
            TitleNormalizeMixin._sort_candidates_by_platform(
                None, is_movie=False, limit=5
            )
            is None
        )
