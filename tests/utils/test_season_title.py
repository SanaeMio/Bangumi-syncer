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
