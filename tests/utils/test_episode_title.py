"""集号抽取单一实现的回归表。

重点钉住「两种强度」的边界 —— 结构化标记 vs 文件名兜底。边界一旦被抹平，
``86 不存在的战区`` 会被读成第 86 集、``機動戦士ガンダム0083`` 会被读成
第 83 集，进而把整部作品标到错误集数上。
"""

from __future__ import annotations

import pytest

from app.utils.episode_title import (
    MAX_EPISODE,
    detect_filename_season,
    extract_episode_from_filename,
    extract_episode_number,
    has_episode_marker,
    parse_season_episode,
)


class TestExtractEpisodeNumber:
    """强度 1：只认结构化集号标记。"""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("S02E12", 12),
            ("s1e1", 1),
            ("S01.E05", 5),
            ("EP05", 5),
            ("ep12", 12),
            ("E03", 3),
            ("E.05", 5),
            ("E-12", 12),
            ("第3集", 3),
            ("第10话", 10),
            ("第5話", 5),
            ("第5章", 5),
            ("第8回", 8),
            ("Show #01", 1),
            ("番剧 #12", 12),
            ("[01]", 1),
            ("番剧【12】", 12),
            ("Show(05)", 5),
            # 0 抬成 1（与历史 max(1, n) 一致）
            ("EP00", 1),
        ],
    )
    def test_structured_markers(self, text, expected):
        assert extract_episode_number(text) == expected

    def test_bare_numbers_are_not_episodes(self):
        """作品编号不能被当成集号（这是 feiniu 旧正则的真实风险）。"""
        assert extract_episode_number("86 不存在的战区") is None
        assert extract_episode_number("機動戦士ガンダム0083") is None
        assert extract_episode_number("207 4K.mp4") is None

    def test_season_marker_is_not_episode(self):
        """「第N季」不是集号。

        旧 feiniu 正则是 ``(?:第|E|ep)\\s*0*(\\d+)(?:集|话|話)?``，后缀可选，
        于是「斗破苍穹 第2季」会被读成第 2 集 —— 库里没有 episode_number 列
        时，整季每一集都会报成第 2 集。
        """
        assert extract_episode_number("斗破苍穹 第2季") is None
        assert extract_episode_number("番剧 第二季") is None

    def test_upper_bound(self):
        assert extract_episode_number(f"第{MAX_EPISODE}集") == MAX_EPISODE
        assert extract_episode_number("第1234集") is None

    def test_empty(self):
        assert extract_episode_number("") is None
        assert extract_episode_number(None) is None  # type: ignore[arg-type]


class TestExtractEpisodeFromFilename:
    """强度 2：文件名激进兜底（仅在确认输入是文件名时使用）。"""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("[1.63GB] 001-4K.mp4", 1),
            ("[734MB] 01-100 04 4K.mp4", 4),
            ("http://x/斗破苍穹年番/207 4K.mp4", 207),
            ("http://x/05 1080p.mp4", 5),
            ("http://x/12.720p.mkv", 12),
            ("[1013.46MB] 001-4K F.S01E003.mp4", 1),
            ("", 0),
        ],
    )
    def test_filename_fallback(self, text, expected):
        assert extract_episode_from_filename(text) == expected


class TestDetectFilenameSeason:
    """文件名季号：只认带「季/期/Season」字样的标记（方案 B）。"""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("番剧 第二季", 2),
            ("番剧 第二期", 2),
            ("番剧 第3季", 3),
            ("Show Season 02", 2),
            # S01 / 无标记 / 纯数字噪声一律视为第一季
            ("S01E05", 1),
            ("05 1080p.mp4", 1),
            ("", 1),
        ],
    )
    def test_detect(self, text, expected):
        assert detect_filename_season(text) == expected


class TestHasEpisodeMarker:
    def test_marker(self):
        assert has_episode_marker("S01E05") is True
        assert has_episode_marker("第3集") is True
        assert has_episode_marker("[05]") is True

    def test_no_marker(self):
        assert has_episode_marker("君の名は.mp4") is False
        assert has_episode_marker("") is False


class TestParseSeasonEpisode:
    """多文本源的优先级（fongmi 的 url > artist）。"""

    @pytest.mark.parametrize(
        "sources,expected",
        [
            (("http://x/S02E12.mp4", ""), (2, 12)),
            (("http://x/S01E003.mp4", ""), (1, 3)),
            (("", "[909.50MB] 201-300 207 4K.mp4"), (1, 207)),
            (("http://x/第2期.mp4", ""), (2, 1)),
            (("http://x/番剧 第二季/EP05.mp4", ""), (2, 5)),
            (("", ""), (1, 1)),
        ],
    )
    def test_priority(self, sources, expected):
        assert parse_season_episode(*sources) == expected

    def test_first_source_wins(self):
        """url 命中 SxxExx 时，artist 里的数字不参与。"""
        assert parse_season_episode("http://x/S02E05.mp4", "[1GB] 999-4K.mp4") == (2, 5)
