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
