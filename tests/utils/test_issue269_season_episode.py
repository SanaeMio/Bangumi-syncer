"""issue #269 回归：续作条目 sort 跨季连续时，季度匹配串到上一季

真实数据（Bangumi + bangumi-data，2026-10 复核）：

| bangumi id | 季度 | 标题 | zh-Hans 别名 | begin | 章节 sort / ep |
| --- | --- | --- | --- | --- | --- |
| 525565 | S1 | 正反対な君と僕 | 相反的你和我、**正相反的你与我** | 2026-01-11 | 1–12 / 1–12 |
| 638497 | S2 | 正反対な君と僕(第2期) | 相反的你和我 第二季、**正相反的你与我 第二季** | 2026-07-05 | **13–25 / 1–13** |

两季的**中文别名并不共享同一个键**：``正相反的你与我`` 只挂在 S1 上，S2 的
别名带了 ``第二季`` 后缀。媒体库推来的标题恰好是第一季的别名，于是：

- ``S02E01``（release 2026-07-04）→ 命中 S1E1（ep 1587644）
- ``S01E13``（TMDB 两季并作一季）→ 命中 S2E13（ep 1717851）

期望都是 S2E1（ep 1704756，sort=13, ep=1）。

根因链（三层，本次修在匹配层第 1、2 层）：

1. 标题索引 O(1) 命中 ``正相反的你与我`` → 只拿到 S1 一条候选；
2. 该候选日期 2026-01-11 与请求 2026-07-04 相差 **174 天 ≤ 180**，
   落在既有日期窗口内被直接采信，S2 条目从未被考虑；
3. 采信结果又令 ``_judge_season_matched_id`` 返回 True（日期匹配），
   执行层于是走「在指定条目内按集号定位」的快路径 —— S02E01 在 S1 里
   找到第 1 集并原样返回，且因为有命中而**永不沿续集链前进**。

本次修复落在第 1、2 层：给 bangumi-data 的候选加**季号收敛**
（``MatchingMixin._filter_candidates_by_season``）。第 3 层无需改动 ——
上游不再把 S1 报成「S2 的季度 ID」后，那条快路径自然不再被走到。
"""

from unittest.mock import patch

from app.utils.bangumi_data import BangumiData

# ---- 真实输入 ----
MEDIA_TITLE = "正相反的你与我"  # 媒体库标题（= S1 的中文别名）
S1_ID = "525565"
S2_ID = "638497"
S1_DATE = "2026-01-11"
S2_DATE = "2026-07-05"
REQ_DATE = "2026-07-04"

# bangumi-data 中这两个条目的真实记录（字段照抄上游数据）
REAL_ITEMS = [
    {
        "title": "正反対な君と僕",
        "titleTranslate": {
            "zh-Hans": ["相反的你和我", "正相反的你与我"],
            "zh-Hant": ["相反的你和我"],
            "en": ["You and I Are Polar Opposites"],
        },
        "begin": "2026-01-11T08:00:00.000Z",
        "sites": [{"site": "bangumi", "id": S1_ID}],
    },
    {
        "title": "正反対な君と僕(第2期)",
        "titleTranslate": {
            "zh-Hans": ["相反的你和我 第二季", "正相反的你与我 第二季"],
            "zh-Hant": ["相反的你和我 第二季"],
            "en": ["You and I are Polar Opposites Season 2"],
        },
        "begin": "2026-07-05T08:00:00.000Z",
        "sites": [{"site": "bangumi", "id": S2_ID}],
    },
]


def _make_data() -> BangumiData:
    """构建只含上述两条真实记录的 BangumiData。

    刻意**不**用全局单例 ``bangumi_data``：那是个模块级对象，
    tests/utils/test_bangumi_data.py 会就地改写它的 ``local_cache_path`` /
    ``use_cache`` 而不还原，全量跑时本文件会读到被改坏的缓存配置而误判未命中。
    自带实例既免疫顺序污染，也让本回归完全自足。
    """
    with (
        patch.object(BangumiData, "_check_and_download_cache_on_startup"),
        patch.object(BangumiData, "_preload_data_to_memory"),
        patch.object(BangumiData, "_build_tmdb_mapping"),
    ):
        data = BangumiData()
    data._data_cache = list(REAL_ITEMS)
    data.local_cache_path = ""  # 阻止落盘读缓存，确保只用 _data_cache
    data.use_cache = False
    with patch.object(data, "_ensure_fresh_data"):
        data._build_title_index()
    return data


def _find(data: BangumiData, season: int, date: str = REQ_DATE):
    return data.find_bangumi_id(
        title=MEDIA_TITLE,
        ori_title=None,
        release_date=date,
        season=season,
        media_type="episode",
    )


class TestIssue269SeasonMatch:
    """季号必须参与 bangumi-data 的匹配判决"""

    def test_s02_request_resolves_to_second_season(self):
        """核心回归：S2 的请求必须解析到第二季条目（638497）

        旧行为：返回 525565（第一季）—— 日期差 174 天落在 180 天窗口内被采信，
        而 S2（日期仅差 1 天）从未进入候选。
        """
        result = _find(_make_data(), season=2)
        assert result is not None, "S2 请求不应匹配失败"
        assert result[0] == S2_ID, (
            f"S2 的请求解析到了 {result[0]}（{'第一季' if result[0] == S1_ID else '其它'}），"
            f"应为第二季 {S2_ID} —— issue #269 回归"
        )

    def test_s02_title_is_second_season(self):
        """命中的标题应当是第二季的中文名，便于日志/通知辨认"""
        result = _find(_make_data(), season=2)
        assert result is not None
        assert "第二季" in result[1]

    def test_s01_request_unchanged(self):
        """不能修坏第一季：S1 的请求仍应命中 525565"""
        result = _find(_make_data(), season=1)
        assert result is not None
        assert result[0] == S1_ID

    def test_s01_with_matching_date_unchanged(self):
        """第一季且日期吻合（正常场景）行为不变"""
        result = _find(_make_data(), season=1, date=S1_DATE)
        assert result is not None
        assert result[0] == S1_ID

    def test_s02_with_exact_season_title_still_works(self):
        """标题自带「第二季」时本就走部分匹配，修复后仍应命中 S2"""
        data = _make_data()
        result = data.find_bangumi_id(
            title="相反的你和我 第二季",
            ori_title=None,
            release_date=REQ_DATE,
            season=2,
            media_type="episode",
        )
        assert result is not None
        assert result[0] == S2_ID

    def test_nonexistent_season_does_not_fall_back_to_season_one(self):
        """请求一个不存在的季（S3）时不应退回第一季 —— 宁可漏标不可错标"""
        result = _find(_make_data(), season=3)
        assert result is None or result[0] != S1_ID, (
            f"S3 的请求退化匹配到了 {result[0] if result else None}，会错标第一季"
        )

    def test_both_seasons_resolvable_from_same_title(self):
        """同一媒体库标题下，S1/S2 各自解析到自己的条目（修复的核心价值）"""
        data = _make_data()
        assert _find(data, season=1)[0] == S1_ID
        assert _find(data, season=2)[0] == S2_ID


class TestSeasonFilterUnit:
    """``_filter_candidates_by_season`` 的规则边界"""

    def _mix(self):
        from app.utils.bangumi_data.matching import MatchingMixin

        s1 = {
            "title": "正反対な君と僕",
            "titleTranslate": {"zh-Hans": ["相反的你和我", "正相反的你与我"]},
        }
        s2 = {
            "title": "正反対な君と僕(第2期)",
            "titleTranslate": {"zh-Hans": ["相反的你和我 第二季"]},
        }
        m = MatchingMixin()
        return m, [(s1, S1_ID, "k"), (s2, S2_ID, "k")]

    def test_season_one_never_filtered(self):
        """season=1 不做任何筛选（第一季条目标题通常不带季号，筛了会误伤）"""
        m, cands = self._mix()
        assert m._filter_candidates_by_season(cands, 1) == cands

    def test_declared_target_season_wins(self):
        """明确声明目标季的候选优先保留"""
        m, cands = self._mix()
        kept = m._filter_candidates_by_season(cands, 2)
        assert [c[1] for c in kept] == [S2_ID]

    def test_seasonless_candidate_treated_as_season_one(self):
        """无季号候选按第一季处理：请求 S2 时应被判定为未命中（返回空）

        这是 issue #269 的关键：S1 条目标题不带季号，旧逻辑视其为「任意季
        皆可」，于是 S2 的请求被它接走。
        """
        m, _ = self._mix()
        s1 = {
            "title": "正反対な君と僕",
            "titleTranslate": {"zh-Hans": ["相反的你和我", "正相反的你与我"]},
        }
        assert m._filter_candidates_by_season([(s1, S1_ID, "k")], 2) == []

    def test_empty_input_safe(self):
        from app.utils.bangumi_data.matching import MatchingMixin

        m = MatchingMixin()
        assert m._filter_candidates_by_season([], 2) == []

    def test_candidate_season_numbers_reads_all_translations(self):
        from app.utils.bangumi_data.matching import MatchingMixin

        m = MatchingMixin()
        s2 = {
            "title": "正反対な君と僕(第2期)",
            "titleTranslate": {
                "zh-Hans": ["相反的你和我 第二季"],
                "en": ["You and I are Polar Opposites Season 2"],
            },
        }
        assert m._candidate_season_numbers(s2) == {2}
        assert m._candidate_season_numbers({"title": "某番"}) == set()
