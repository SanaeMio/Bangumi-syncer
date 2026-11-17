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
