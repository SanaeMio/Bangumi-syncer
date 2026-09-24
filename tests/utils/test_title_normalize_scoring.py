"""校验标题“匹配相似度”归一化工具已从 bangumi_api/search.py 搬迁到
bangumi_archive/_title_normalize.py，且行为不变。

这些工具供系统 B（title_diff_ratio）打分使用；集中到 _title_normalize.py
是为后续 scorer.py 直接依赖、消除跨模块漂移（见设计文档 §12 后续）。
"""

from app.utils.bangumi_archive._title_normalize import (
    _TITLE_DECORATORS,
    _TITLE_SUFFIXES,
    _normalize_title_for_match,
    _strip_media_suffix,
)


def test_title_suffixes_is_nonempty_tuple():
    assert isinstance(_TITLE_SUFFIXES, tuple)
    assert "动画版" in _TITLE_SUFFIXES
    # §15.1 瘦身：移除误剥风险高的「动画」「动漫版」「动漫」
    assert "动画" not in _TITLE_SUFFIXES
    assert "动漫版" not in _TITLE_SUFFIXES
    assert "动漫" not in _TITLE_SUFFIXES
    # §15.3 B：OVA/OAD 按"大小写归一后删"决议不入此表（折叠语境下词边界无法安全剥离）
    assert "OVA" not in _TITLE_SUFFIXES
    assert "OAD" not in _TITLE_SUFFIXES


def test_title_decorators_is_nonempty_tuple():
    assert isinstance(_TITLE_DECORATORS, tuple)
    assert "年番" in _TITLE_DECORATORS
    # §15.2 清理：删除死码与空格锚定死项
    assert "半年番" not in _TITLE_DECORATORS
    assert "季番" not in _TITLE_DECORATORS
    assert "ova" not in _TITLE_DECORATORS
    assert "oad" not in _TITLE_DECORATORS
    assert "剧场版 " not in _TITLE_DECORATORS


def test_strip_media_suffix_removes_tail():
    assert _strip_media_suffix("遮天动画版") == "遮天"
    assert _strip_media_suffix("魔法少女小圆电影版") == "魔法少女小圆"


def test_strip_media_suffix_no_suffix_unchanged():
    assert _strip_media_suffix("剑来") == "剑来"


def test_normalize_does_not_corrupt_terra_nova():
    # §15.2/§15.3：归一化不得因 ova/oad 误剥「Terra Nova」「El Dorado」之类标题
    assert _normalize_title_for_match("Terra Nova") == "TerraNova"
    assert _normalize_title_for_match("El Dorado") == "ElDorado"


def test_strip_media_suffix_too_short_keeps_original():
    # “动画”剥离后为空串（len < 2），应保留原值避免误剥
    assert _strip_media_suffix("动画") == "动画"


def test_normalize_folds_whitespace_and_removes_decorator():
    # 空白折叠 + 修饰词去除，两种写法归一为同一核心标题
    assert _normalize_title_for_match("斗破苍穹 年番") == "斗破苍穹"
    assert _normalize_title_for_match("斗破苍穹年番") == "斗破苍穹"


def test_normalize_trailing_huanwai_only():
    # §15.2：番外 改为尾部锚定，避免吃掉中间的「番外地」
    assert _normalize_title_for_match("番外地") == "番外地"
    assert _normalize_title_for_match("X 番外") == "X"
    assert _normalize_title_for_match("X番外") == "X"


def test_normalize_trims_outer_whitespace():
    assert _normalize_title_for_match("  进击的巨人  ") == "进击的巨人"


def test_normalize_empty_returns_empty():
    assert _normalize_title_for_match("") == ""


class TestSeasonMismatchPenalty:
    """季号不一致时的扣分（`SearchMixin._apply_season_penalty`）。

    背景：Bangumi 把每一季做成**独立条目**，且「第 N 季」显式季号只有部分季
    填写（真实库 30,556 部动画中 2,635 部有季号，8.6%）。于是
    「水星领航员 第二季」会因字面相似度接近而命中**第三季**。
    真实数据（191 对同基础名样本）：18 例被兄弟季抢走，差值最大 -0.111；
    真实 sync_records 中 38.4% 的标题带显式季号。

    设计原则：**仅当双方都有季号且不等时**才扣分；候选季号为 None 时不扣
    （如「鬼滅の刃 遊郭編」本身不含季号但确是 TV 第二季）。
    """

    @staticmethod
    def _penalize(score, title, name="", name_cn=""):
        from app.utils.bangumi_api.search import SearchMixin

        return SearchMixin._apply_season_penalty(
            score, title, {"name": name, "name_cn": name_cn}
        )

    def test_penalizes_mismatched_season(self):
        """双方都有季号且不等 → 扣分"""
        s = self._penalize(0.875, "水星领航员 第二季", "水星领航员 第三季")
        assert s == 0.875 - 0.15

    def test_no_penalty_when_season_matches(self):
        """季号一致 → 不扣分"""
        assert self._penalize(0.9, "水星领航员 第二季", "水星领航员 第二季") == 0.9

    def test_no_penalty_when_candidate_has_no_season(self):
        """候选无季号 → 不扣分（无法判定，如「鬼滅の刃 遊郭編」）"""
        assert self._penalize(0.9, "鬼滅の刃 第三季", "鬼滅の刃 遊郭編") == 0.9

    def test_no_penalty_when_request_has_no_season(self):
        """请求无季号 → 不扣分"""
        assert self._penalize(0.9, "斗破苍穹年番", "斗破苍穹 年番4") == 0.9

    def test_penalty_recognizes_multiple_season_forms(self):
        """「第2季」/「Season 2」/「2nd Season」都能识别"""
        for title in ("X 第2季", "X Season 2", "X 2nd Season"):
            s = self._penalize(0.9, title, "X 第三季")
            assert s == 0.9 - 0.15, f"{title!r} 未被识别"

    def test_penalty_floors_at_zero(self):
        """低分候选扣分后不为负"""
        assert self._penalize(0.05, "X 第二季", "X 第三季") == 0.0

    def test_matching_season_outranks_mismatched(self):
        """同基础名下，正确季的最终分必须高于错误季（核心保证）"""
        title = "水星领航员 第二季"
        right = self._penalize(0.875, title, "水星领航员 第二季")
        wrong = self._penalize(0.875, title, "水星领航员 第三季")
        assert right > wrong

    def test_no_penalty_when_request_has_conflicting_seasons(self):
        """请求标题含多个矛盾季号 → 不扣分（真实脏标题）

        真实形态：媒体库把英文原名与中文季名拼在一起 ——
        ``"Marvel's Guardians of the Galaxy Season 1 第二季"``。
        首个匹配给出 2，但该条目实际是第一季；此时季号不可信。
        """
        title = "Marvel's Guardians of the Galaxy Season 1 第二季"
        assert self._penalize(1.0, title, "银河守护者 第一季") == 1.0

    def test_conflicting_seasons_helper(self):
        """extract_season_candidates 能看出矛盾"""
        from app.utils.season_title import extract_season_candidates

        assert extract_season_candidates("X Season 1 第二季") == {1, 2}
        assert extract_season_candidates("X 第二季") == {2}
        assert extract_season_candidates("X") == set()
        # 多个写法指向同一季号 → 不算矛盾
        assert extract_season_candidates("X 第2季 Season 2") == {2}
