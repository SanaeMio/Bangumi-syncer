"""text_constants 常量测试"""

from app.utils.text_constants import (
    CN_NUM,
    DEFAULT_PLATFORM_WEIGHT,
    PLATFORM_WEIGHT_MOVIE_MODE,
    PLATFORM_WEIGHT_TV_MODE,
    PUNCTUATION_MAP,
)


class TestCNNum:
    def test_basic_digits(self):
        assert CN_NUM["一"] == 1
        assert CN_NUM["九"] == 9
        assert CN_NUM["十"] == 10

    def test_supports_eleven_to_nineteen(self):
        """支持"十一"~"十九"组合（由调用方拆解，字典只提供单字）"""
        assert 10 + CN_NUM["一"] == 11
        assert 10 + CN_NUM["九"] == 19

    def test_count(self):
        assert len(CN_NUM) == 10


class TestPunctuationMap:
    def test_common_mappings(self):
        assert PUNCTUATION_MAP["："] == ":"
        assert PUNCTUATION_MAP["，"] == ","
        assert PUNCTUATION_MAP["（"] == "("
        assert PUNCTUATION_MAP["【"] == "["

    def test_quotes(self):
        assert PUNCTUATION_MAP["“"] == '"'
        assert PUNCTUATION_MAP["”"] == '"'
        assert PUNCTUATION_MAP["‘"] == "'"
        assert PUNCTUATION_MAP["’"] == "'"

    def test_dashes(self):
        assert PUNCTUATION_MAP["—"] == "-"
        assert PUNCTUATION_MAP["–"] == "-"
        assert PUNCTUATION_MAP["―"] == "-"

    def test_maketrans_compatible(self):
        """可作为 str.maketrans 入参"""
        trans = str.maketrans(PUNCTUATION_MAP)
        assert "测试：".translate(trans) == "测试:"


class TestPlatformWeights:
    def test_tv_mode_tv_highest(self):
        assert PLATFORM_WEIGHT_TV_MODE["TV"] == 100
        assert PLATFORM_WEIGHT_TV_MODE["WEB"] == 90

    def test_movie_mode_movie_highest(self):
        assert PLATFORM_WEIGHT_MOVIE_MODE["剧场版"] == 100
        assert PLATFORM_WEIGHT_MOVIE_MODE["电影"] == 100

    def test_default_weight(self):
        assert DEFAULT_PLATFORM_WEIGHT == 50

    def test_consistent_keys(self):
        """两种模式覆盖相同的平台集合"""
        assert set(PLATFORM_WEIGHT_TV_MODE.keys()) == set(
            PLATFORM_WEIGHT_MOVIE_MODE.keys()
        )
