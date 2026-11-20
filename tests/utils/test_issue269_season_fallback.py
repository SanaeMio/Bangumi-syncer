"""issue #269 后续：季号收敛不能把「续作条目缺失」一律判成未命中

#269 的季号收敛修好了「续作被解析成上一季」，但把另一种情况一起判死了：
请求标题自带 ``第二季`` / ``Season 2``，而 bangumi-data 里**根本没有**这一季的
条目（单季番被媒体库加上季后缀，或续作尚未收录）。此时精确索引只剩第一季条目，
被 ``_filter_candidates_by_season`` 筛空后直接判未命中。

L1 黄金集实测：这类用例 30 条里 28 条从命中退化为未命中或**错命中别的番**
（``マジカノ 第二季`` → ``棒球大联盟 第二季``），命中率 98.8% → 90.3%。
本文件钉住修复后的边界：

1. 请求自带本季季标 + 基础条目日期吻合 → 落回基础条目（恢复旧行为）；
2. 请求不带季标（``正相反的你与我`` 请求 S3）→ 仍然不退回第一季（#269）；
3. 部分匹配按季号择优必须同时有**名字证据**与**日期证据**，否则会挑到
   全库任意一条「第二季」。
"""

from unittest.mock import patch

from app.utils.bangumi_data import BangumiData
from app.utils.bangumi_data.matching import MatchingMixin

BASE_ID = "25608"
BASE_TITLE = "マジカノ"
BASE_DATE = "2006-01-01"
# 同档期播出的另一部番（自带「第二季」字样），用来复现错命中
DISTRACTOR_ID = "2886"
DISTRACTOR_TITLE = "MAJOR 2nd season"
DISTRACTOR_DATE = "2006-01-05"

ITEMS = [
    {
        "title": BASE_TITLE,
        "titleTranslate": {"zh-Hans": ["魔法少女真帆"]},
        "begin": "2006-01-01T00:00:00.000Z",
        "sites": [{"site": "bangumi", "id": BASE_ID}],
    },
    {
        "title": DISTRACTOR_TITLE,
        "titleTranslate": {"zh-Hans": ["棒球大联盟 第二季"]},
        "begin": "2006-01-05T00:00:00.000Z",
        "sites": [{"site": "bangumi", "id": DISTRACTOR_ID}],
    },
]


def _make_data() -> BangumiData:
    """只含两条条目的离线 BangumiData（构造方式同 test_issue269_season_episode）

    刻意不用全局单例：全量跑时其它测试会就地改写它的缓存配置。
    """
    with (
        patch.object(BangumiData, "_check_and_download_cache_on_startup"),
        patch.object(BangumiData, "_preload_data_to_memory"),
        patch.object(BangumiData, "_build_tmdb_mapping"),
    ):
        data = BangumiData()
    data._data_cache = list(ITEMS)
    data._cache_items = list(ITEMS)
    data.local_cache_path = ""
    data.use_cache = False
    with patch.object(data, "_ensure_fresh_data"):
        data._build_title_index()
    return data


def _find(title, ori_title, date, season=2):
    return _make_data().find_bangumi_id(
        title=title,
        ori_title=ori_title,
        release_date=date,
        season=season,
        media_type="episode",
    )


class TestFallbackToBaseEntry:
    """续作条目不存在时落回基础条目（L1 黄金集 S3 场景）"""

    def test_chinese_season_suffix_falls_back_to_base_entry(self):
        """``XX 第二季`` + 第一名条目日期 → 命中基础条目且 date_matched"""
        result = _find("マジカノ 第二季", BASE_TITLE, BASE_DATE)
        assert result is not None, "单季番加季后缀不应判未命中"
        assert result[0] == BASE_ID
        assert result[2] is True, "命中的是基础条目，应标记为日期匹配"

    def test_english_season_suffix_uses_original_title_as_evidence(self):
        """``XX Season 2`` 会先被季标剥离改写，证据要看剥离前的原始标题

        ``find_bangumi_id`` 在 season>1 时把 ``マジカノ Season 2`` 剥成
        ``マジカノ``；若兜底拿剥过的标题判断「请求是否声明了第二季」，证据会
        凭空消失，这条又会退回未命中。
        """
        result = _find("マジカノ Season 2", None, BASE_DATE)
        assert result is not None
        assert result[0] == BASE_ID
        assert result[2] is True

    def test_unrelated_season_entry_is_not_picked(self):
        """同档期另一部「第二季」番不能被当成本番（旧行为下的错命中来源）"""
        result = _find("マジカノ 第二季", BASE_TITLE, BASE_DATE)
        assert result is not None
        assert result[0] == BASE_ID, "季号相同但名字无关的候选不应被采信"

    def test_far_release_date_does_not_fall_back(self):
        """请求日期与基础条目差 4 年 → 判未命中（真·未收录的续作不该错标）"""
        assert _find("マジカノ 第二季", BASE_TITLE, "2010-06-01") is None

    def test_missing_release_date_does_not_fall_back(self):
        """没有日期就没有证据，宁可漏标"""
        assert _find("マジカノ 第二季", BASE_TITLE, None) is None

    def test_unmarked_request_still_rejects_other_season(self):
        """不带季标的 S2 请求仍不退回第一季（issue #269 的核心约束）"""
        result = _find("マジカノ", None, BASE_DATE)
        assert result is None, "无季标证据时不能拿第一季顶替第二季"


class TestSeasonedPartialPickEvidence:
    """``_pick_season_from_partial`` 需要名字 + 日期双重证据"""

    def _mixin(self):
        return MatchingMixin()

    def _distractor(self):
        return {
            "title": DISTRACTOR_TITLE,
            "titleTranslate": {"zh-Hans": ["棒球大联盟 第二季"]},
            "begin": "2006-01-05T00:00:00.000Z",
        }

    def _sibling(self):
        """同一部番的第二季条目（名字去掉季标后与查询同源）"""
        return {
            "title": "マジカノ 第二季",
            "titleTranslate": {"zh-Hans": ["魔法少女真帆 第二季"]},
            "begin": "2006-01-05T00:00:00.000Z",
        }

    def test_unrelated_candidate_rejected(self):
        m = self._mixin()
        picked = m._pick_season_from_partial(
            "マジカノ 第二季", [(self._distractor(), 0.5, DISTRACTOR_ID)], BASE_DATE, 2
        )
        assert picked is None

    def test_related_candidate_with_far_date_rejected(self):
        m = self._mixin()
        picked = m._pick_season_from_partial(
            "マジカノ 第二季", [(self._sibling(), 0.5, BASE_ID)], "2010-06-01", 2
        )
        assert picked is None

    def test_related_candidate_accepted(self):
        m = self._mixin()
        picked = m._pick_season_from_partial(
            "マジカノ 第二季", [(self._sibling(), 0.5, BASE_ID)], BASE_DATE, 2
        )
        assert picked is not None
        assert picked[0] == BASE_ID


# ----- 索引路径的兜底不许盖掉真实续作（审查 F1） -----

SEQ_BASE_ID = "1001"
SEQ_SEQ_ID = "1002"
SEQ_BASE_TITLE = "テストアニメ"
SEQ_BASE_DATE = "2026-01-11"

WITH_SEQUEL = [
    {
        "title": SEQ_BASE_TITLE,
        "titleTranslate": {"zh-Hans": ["测试动画"]},
        "type": "tv",
        "begin": "2026-01-11T15:00:00.000Z",
        "end": "2026-03-29T15:00:00.000Z",
        "sites": [{"site": "bangumi", "id": SEQ_BASE_ID}],
    },
    {
        "title": f"{SEQ_BASE_TITLE} 2nd Season",
        "titleTranslate": {"zh-Hans": ["测试动画 第二季"]},
        "type": "tv",
        "begin": "2026-07-04T15:00:00.000Z",
        "end": "2026-09-26T15:00:00.000Z",
        "sites": [{"site": "bangumi", "id": SEQ_SEQ_ID}],
    },
]


def _make_data_of(items) -> BangumiData:
    """只含给定条目的离线 BangumiData（同 ``_make_data``，语料可换）"""
    with (
        patch.object(BangumiData, "_check_and_download_cache_on_startup"),
        patch.object(BangumiData, "_preload_data_to_memory"),
        patch.object(BangumiData, "_build_tmdb_mapping"),
    ):
        data = BangumiData()
    data._data_cache = list(items)
    data._cache_items = list(items)
    data.local_cache_path = ""
    data.use_cache = False
    with patch.object(data, "_ensure_fresh_data"):
        data._build_title_index()
    return data


class TestIndexPathFallbackGuard:
    """季标被剥离改写过时，索引路径不许用兜底抢先返回基础条目

    ``find_bangumi_id`` 在 season>1 时先把季标剥掉（``XX Season 2`` → ``XX``），
    索引于是只查得到基础条目。若此时按「索引里没有本季」就兜底，真实续作
    （挂在 ``XX 2nd Season`` 这个别名下、只有线性扫描看得见）会被永久盖掉 ——
    正是 #269 修好的那个错，而且 ``date_matched=True`` 会让下游直接采信。
    """

    def _find(self, title, ori_title=None, date="2026-07-04"):
        return _make_data_of(WITH_SEQUEL).find_bangumi_id(
            title=title,
            ori_title=ori_title,
            release_date=date,
            season=2,
            media_type="episode",
        )

    def test_english_season_suffix_keeps_real_sequel(self):
        result = self._find("测试动画 Season 2")
        assert result is not None
        assert result[0] == SEQ_SEQ_ID, "英文季标被剥离后不许把续作降级成第一季"

    def test_arabic_season_suffix_keeps_real_sequel(self):
        result = self._find("测试动画 第2季")
        assert result is not None
        assert result[0] == SEQ_SEQ_ID

    def test_chinese_season_suffix_keeps_real_sequel(self):
        """中文季标不剥，走的是线性扫描（原本就正确，钉住不回归）"""
        result = self._find("测试动画 第二季")
        assert result is not None
        assert result[0] == SEQ_SEQ_ID


class TestFallbackDateMatchedConsistency:
    """同一个基础条目、同一日期，两条路径的 date_matched 必须一致（审查 F2）

    索引路径一直返回 True；线性路径原本返回 False。差异会顺流到
    ``_judge_season_matched_id``（True 则跳过前传链验证）与 ``score``
    （1.0 vs 0.8）—— 同一条命中，走哪条路径决定它「可不可信」。
    """

    def test_same_entry_same_flag_on_both_paths(self):
        # 索引命中（键来自 ori_title）+ 兜底
        indexed = _find("マジカノ 第二季", BASE_TITLE, BASE_DATE)
        # 索引查不到（title 被剥成基础标题后不准再由索引兜底）→ 线性扫描 + 兜底
        linear = _find("マジカノ Season 2", None, BASE_DATE)

        assert indexed is not None and linear is not None
        assert indexed[0] == linear[0] == BASE_ID
        assert indexed[2] is True
        assert linear[2] is True, "线性路径落回基础条目时也必须标记为日期匹配"

    def test_linear_path_marks_date_matched_on_fallback(self):
        """直接走线性选择函数：兜底命中必须返回 date_matched=True

        上一条只断言「两条路径一致」，现状下索引路径会替线性路径把答案答了，
        测不到线性路径自己。这里绕开 ``find_bangumi_id`` 直接调。
        """
        data = _make_data()
        result = data._select_from_exact_matches(
            "マジカノ 第二季",
            [(ITEMS[0], BASE_ID, "title")],
            [(ITEMS[1], 0.5, DISTRACTOR_ID)],
            BASE_DATE,
            2,
            "マジカノ 第二季",
        )
        assert result is not None
        assert result[0] == BASE_ID
        assert result[2] is True, "线性路径落回基础条目时也必须标记为日期匹配"
