"""「同义正则只有一份实现」的守护用例。

这些断言不是测业务行为，而是测**结构不变量**：共享词元只有一份、派生外壳
仍等价于历史字面量、同一含义的两处引用的是同一个对象。任何一次"顺手复制一份
正则"的改动都会在这里失败。

背景：仓里已有 ``app/utils/title_patterns.py`` 作为跨模块共享正则的单源，但它
一度只被用了 1 个常量，年份/空白/时间戳仍在各处各写一份。
"""

from __future__ import annotations

from app.utils import log_grouping, title_patterns
from app.utils.bangumi_archive._title_normalize import _DECOR_YEAR_RE, strip_year_suffix
from app.utils.media_type_detector import (
    OAD_EXACT_RE,
    OVA_EXACT_RE,
    detect_media_type,
)
from app.utils.title_patterns import WHITESPACE_RE, YEAR_RE, YEAR_TOKEN


class TestYearSingleSource:
    def test_year_re_is_built_from_token(self):
        assert YEAR_RE.pattern == YEAR_TOKEN

    def test_decor_year_derives_from_token(self):
        """archive 深度归一的年份外壳必须由共享词元拼出，而不是重写一遍。"""
        assert YEAR_TOKEN in _DECOR_YEAR_RE.pattern
        # 行为：连全半角括号一起剥；裸年份只剥年份本身，尾随空格由调用方 strip
        assert strip_year_suffix("Name（2021）") == "Name"
        assert strip_year_suffix("Name 2021") == "Name "

    def test_movie_year_derives_from_token(self):
        from app.services.fongmi.client import _MOVIE_YEAR_RE

        assert YEAR_TOKEN in _MOVIE_YEAR_RE.pattern
        # 行为：要求文件名边界，避免把长数字串里的片段当年份
        assert _MOVIE_YEAR_RE.search("君の名は 2016.mkv")
        assert not _MOVIE_YEAR_RE.search("12020.mkv")

    def test_extract_year_still_works(self):
        assert title_patterns.extract_year("放送 2019 年") == 2019
        assert title_patterns.extract_year("无年份") is None


class TestWhitespaceSingleSource:
    def test_both_normalizers_share_the_object(self):
        """两个归一化器一个折成单空格、一个删空，但必须是同一个 Pattern。"""
        from app.services.sync_service import title_normalize

        assert title_normalize.WHITESPACE_RE is WHITESPACE_RE
        assert title_normalize.WHITESPACE_RE.sub(" ", "a   b") == "a b"
        assert WHITESPACE_RE.sub("", "a   b") == "ab"


class TestLogTimestampSingleSource:
    def test_one_pattern_for_log_lines(self):
        """日志页与分组解析必须用同一份时间戳模式。"""
        from app.mcp import tools

        assert tools._LOG_TIMESTAMP_RE is log_grouping.TIMESTAMP_RE

    def test_accepts_with_and_without_millis(self):
        with_ms = "[2026/09/03 12:00:00.123] [INFO] x"
        no_ms = "[2026/09/03 12:00:00] [INFO] x"
        assert log_grouping.TIMESTAMP_RE.match(with_ms).group(1) == (
            "2026/09/03 12:00:00.123"
        )
        assert log_grouping.TIMESTAMP_RE.match(no_ms).group(1) == "2026/09/03 12:00:00"


class TestMediaTypeKeywords:
    def test_exact_patterns_still_discriminate(self):
        assert OVA_EXACT_RE.pattern == r"\bOVA\b"
        assert OAD_EXACT_RE.pattern == r"\bOAD\b"
        assert detect_media_type("某作品 OVA") == "ova"
        assert detect_media_type("某作品 OAD") == "oad"

    def test_generic_ova_keyword_falls_through_to_ova(self):
        """「特别篇」走泛关键词分支，仍归 ova 而不是 oad。"""
        assert detect_media_type("某作品 特别篇") == "ova"


class TestReasoningModelRegex:
    def test_shared_between_openai_providers(self):
        from app.services.llm.providers import base, openai_compat, openai_responses

        assert openai_compat.REASONING_MODEL_RE is base.REASONING_MODEL_RE
        assert openai_responses.REASONING_MODEL_RE is base.REASONING_MODEL_RE

    def test_only_o_series_matches(self):
        from app.services.llm.providers.base import REASONING_MODEL_RE

        for model in ("o1", "o3-mini", "o4-mini"):
            assert REASONING_MODEL_RE.match(model), model
        for model in ("ollama/llama3", "openrouter/openai/gpt-4o", "gpt-4o", "claude"):
            assert not REASONING_MODEL_RE.match(model), model
