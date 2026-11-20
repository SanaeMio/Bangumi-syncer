"""季号「反查」单一实现的回归表。

``title_declares_season`` 承接原 ``SeasonInfoMixin._check_season_info_in_title``
的全部历史行为（含那些看似宽松的写法），并加了一条边界约束：裸 ``S{N}`` 不能
命中 ``S12``。Mixin 现在只是转发，下面的 delegation 用例保证它不会再次长出
第二套关键词表。
"""

from __future__ import annotations

import pytest

from app.utils.season_title import title_declares_season

# (标题, 季号, 期望) —— 覆盖旧 Mixin 测试里的全部断言
CASES = [
    ("测试动画 第一季", 1, True),
    ("测试动画 第二季", 2, True),
    ("测试动画 第1季", 1, True),
    ("测试动画 1季", 1, True),
    ("Test Anime Season 1", 1, True),
    ("Test Anime S1", 1, True),
    ("Test Anime", 1, False),
    ("某番第3季上半", 3, True),
    # 无「第N季」字样的续作写法（结构化解析兜底）
    ("笨蛋，测验，召唤兽2", 2, True),
    ("偶像大师 II", 2, True),
    # 作品编号不是季号
    ("機動戦士ガンダム0083", 2, False),
    # 季号不符
    ("某番 第二季", 1, False),
    ("某番 第二季", 2, True),
]


@pytest.mark.parametrize("title,season,expected", CASES)
def test_title_declares_season(title, season, expected):
    assert title_declares_season(title, season) is expected


def test_bare_s_marker_respects_digit_boundary():
    """``S1`` 不能命中 ``S12``。

    旧实现是裸子串 ``"S1" in title``：请求第 1 季时，「某剧 S12」会被判定
    「已声明第 1 季」，季过滤器于是放行一个第 12 季的候选 —— 是错配而非漏标。
    """
    assert title_declares_season("某剧 S12", 1) is False
    assert title_declares_season("某剧 S12", 12) is True
    assert title_declares_season("某剧 S3", 3) is True


@pytest.mark.parametrize(
    "title,season",
    [
        ("Test Anime Season 12", 1),
        ("Test Anime Season 10", 1),
        ("某番 第11季", 1),
        ("某番 第12季", 2),
        ("某番 11季", 1),
        ("某番 第十一季", 1),
        ("某番 第十季", 1),
    ],
)
def test_long_season_number_not_matched_by_shorter_one(title, season):
    """短季号不许吃掉长季号的前缀（PR #293 review）。

    ``Season 12`` 含 ``Season 1``、``第11季`` 含 ``1季``、``第12季`` 含 ``2季``、
    ``第十一季`` 含 ``一季`` —— 纯子串匹配下，请求第 1/2 季时季过滤器会把
    一个第 10~12 季的候选判成「已声明本季」而放行，是错配不是漏标。
    尾部边界不够用（``第11季`` 的问题在**前缀**），中文数字要一起挡。
    """
    assert title_declares_season(title, season) is False


@pytest.mark.parametrize(
    "title,season",
    [
        ("Test Anime Season 12", 12),
        ("Test Anime Season 10", 10),
        ("某番 第11季", 11),
        ("某番 第12季", 12),
        ("某番 11季", 11),
        ("某番 第十一季", 11),
        ("某番 第十季", 10),
    ],
)
def test_long_season_number_still_matches_itself(title, season):
    """加了边界之后，长季号仍要能命中自己（别把误判修成漏判）"""
    assert title_declares_season(title, season) is True


def test_loose_writings_survive_the_boundary():
    """宽松写法（省略「第」的 ``N季`` / ``N期``）不能被边界收紧掉"""
    assert title_declares_season("凡人修仙传5季", 5) is True
    assert title_declares_season("某番 3期", 3) is True
    assert title_declares_season("测试动画 1季", 1) is True


def test_invalid_inputs():
    assert title_declares_season("", 1) is False
    assert title_declares_season("某番 第一季", 0) is False


def test_mixin_delegates_to_single_implementation():
    """Mixin 不得再持有第二份判定逻辑。"""
    from app.services.sync_service.season_info import SeasonInfoMixin

    class _Service(SeasonInfoMixin):
        pass

    service = _Service()
    for title, season, _expected in CASES:
        assert service._check_season_info_in_title(
            title, season
        ) == title_declares_season(title, season)
