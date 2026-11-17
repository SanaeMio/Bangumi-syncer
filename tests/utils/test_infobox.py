"""app.utils.infobox 别名抽取的单测。

覆盖三种历史数据形状（API 的 v-dict、API 的裸字符串、archive 的原始 wiki 串）
以及两套 key 集合的差异（打分层只认「别名」，archive 索引扩大召回）。
"""

from __future__ import annotations

from app.utils.infobox import (
    ALIAS_KEYS,
    extract_infobox_aliases,
    iter_infobox_aliases,
)


class TestApiShape:
    def test_list_value_with_v_dict(self) -> None:
        infobox = [{"key": "别名", "value": [{"v": "彼女"}, {"v": "女友"}]}]
        assert extract_infobox_aliases(infobox) == ["彼女", "女友"]

    def test_list_value_with_bare_strings(self) -> None:
        infobox = [{"key": "别名", "value": ["彼女", "女友"]}]
        assert extract_infobox_aliases(infobox) == ["彼女", "女友"]

    def test_string_value(self) -> None:
        infobox = [{"key": "别名", "value": "彼女"}]
        assert extract_infobox_aliases(infobox) == ["彼女"]

    def test_default_keys_only_alias(self) -> None:
        """匹配打分沿用历史行为：只认「别名」，不认中文名/英文名。"""
        infobox = [
            {"key": "中文名", "value": "女友"},
            {"key": "别名", "value": "彼女"},
        ]
        assert extract_infobox_aliases(infobox) == ["彼女"]
        assert extract_infobox_aliases(infobox, ALIAS_KEYS) == ["女友", "彼女"]

    def test_latin_key_is_case_insensitive(self) -> None:
        infobox = [{"key": "Alias", "value": "Kanojo"}]
        assert extract_infobox_aliases(infobox, ALIAS_KEYS) == ["Kanojo"]


class TestArchiveShape:
    def test_raw_wiki_string(self) -> None:
        raw = "{{Infobox|别名=彼女|中文名=女友|英文名=Kanojo}}"
        assert extract_infobox_aliases(raw, ALIAS_KEYS) == ["彼女", "女友", "Kanojo"]

    def test_raw_wiki_string_default_keys(self) -> None:
        raw = "{{Infobox|中文名=女友}}"
        assert extract_infobox_aliases(raw) == []


class TestEdgeCases:
    def test_none_and_empty(self) -> None:
        assert extract_infobox_aliases(None) == []
        assert extract_infobox_aliases("") == []
        assert extract_infobox_aliases([]) == []

    def test_non_string_values_skipped(self) -> None:
        infobox = [{"key": "别名", "value": [{"v": 123}, {"v": " ok "}, ""]}]
        assert extract_infobox_aliases(infobox) == [" ok "]

    def test_iterator_matches_list(self) -> None:
        infobox = [{"key": "别名", "value": ["a", "b"]}]
        assert list(iter_infobox_aliases(infobox)) == extract_infobox_aliases(infobox)
