"""wiki_parser 单元测试

覆盖：
- 基本解析：单行参数、多行参数、嵌套模板
- 列表值识别：bullet list、{{list|...}}/{{ll|...}}、<br> 分隔
- wiki 标记清理：[[link|显示]]、bold/italic
- 边界与容错：空串、非 Infobox 模板、不闭合大括号、None 输入
- 集成：_store._adapt_subject_row 接入 wiki_parser 后的 infobox 适配
"""

from __future__ import annotations

from app.utils.bangumi_archive._store import ArchiveStore
from app.utils.bangumi_archive._wiki_parser import (
    _split_top_level_params,
    parse_infobox,
)

# ===== 基本解析 =====


class TestParseInfoboxBasic:
    """基本参数解析"""

    def test_simple_single_param(self) -> None:
        text = "{{Infobox|alias=Test}}"
        result = parse_infobox(text)
        assert result == [{"key": "alias", "value": "Test"}]

    def test_multiple_params_inline(self) -> None:
        text = "{{Infobox|alias=Test|中文名=测试|英文名=TestEN}}"
        result = parse_infobox(text)
        assert result == [
            {"key": "alias", "value": "Test"},
            {"key": "中文名", "value": "测试"},
            {"key": "英文名", "value": "TestEN"},
        ]

    def test_multiline_params(self) -> None:
        text = "{{Infobox\n|alias=Test\n|中文名=测试\n}}"
        result = parse_infobox(text)
        assert result == [
            {"key": "alias", "value": "Test"},
            {"key": "中文名", "value": "测试"},
        ]

    def test_lowercase_infobox(self) -> None:
        """模板名不区分大小写"""
        text = "{{infobox|alias=Test}}"
        result = parse_infobox(text)
        assert result == [{"key": "alias", "value": "Test"}]

    def test_skip_param_without_equal(self) -> None:
        """无 = 的参数（如类型修饰符 anime）应跳过"""
        text = "{{Infobox|anime|alias=Test}}"
        result = parse_infobox(text)
        assert result == [{"key": "alias", "value": "Test"}]

    def test_empty_value(self) -> None:
        """空值应返回空字符串"""
        text = "{{Infobox|alias=}}"
        result = parse_infobox(text)
        assert result == [{"key": "alias", "value": ""}]

    def test_value_with_spaces_stripped(self) -> None:
        text = "{{Infobox|alias=  Test  }}"
        result = parse_infobox(text)
        assert result == [{"key": "alias", "value": "Test"}]

    def test_key_with_spaces_stripped(self) -> None:
        text = "{{Infobox| alias =Test}}"
        result = parse_infobox(text)
        assert result == [{"key": "alias", "value": "Test"}]

    def test_no_params(self) -> None:
        """无参数（仅模板名）应返回空列表"""
        text = "{{Infobox}}"
        result = parse_infobox(text)
        assert result == []

    def test_value_contains_equals_sign(self) -> None:
        """值中包含 = 字符（如 URL）应正确处理（仅第一个 = 分割 key/value）"""
        text = "{{Infobox|url=http://example.com?a=1&b=2}}"
        result = parse_infobox(text)
        assert result == [{"key": "url", "value": "http://example.com?a=1&b=2"}]


# ===== 列表值识别 =====


class TestParseInfoboxList:
    """列表值解析"""

    def test_bullet_list(self) -> None:
        text = "{{Infobox|别名=* a1\n* a2\n* a3}}"
        result = parse_infobox(text)
        assert result == [
            {"key": "别名", "value": [{"v": "a1"}, {"v": "a2"}, {"v": "a3"}]}
        ]

    def test_bullet_list_single_item(self) -> None:
        """单个 bullet 项也视为列表"""
        text = "{{Infobox|别名=* a1}}"
        result = parse_infobox(text)
        assert result == [{"key": "别名", "value": [{"v": "a1"}]}]

    def test_bullet_list_with_spaces(self) -> None:
        text = "{{Infobox|别名=* a1\n * a2}}"
        result = parse_infobox(text)
        assert result == [{"key": "别名", "value": [{"v": "a1"}, {"v": "a2"}]}]

    def test_double_bullet_nested(self) -> None:
        """** 双星号也识别为列表项"""
        text = "{{Infobox|别名=** a1\n** a2}}"
        result = parse_infobox(text)
        assert result == [{"key": "别名", "value": [{"v": "a1"}, {"v": "a2"}]}]

    def test_list_template(self) -> None:
        """{{list|a|b|c}} 模板"""
        text = "{{Infobox|别名={{list|a1|a2|a3}}}}"
        result = parse_infobox(text)
        assert result == [
            {"key": "别名", "value": [{"v": "a1"}, {"v": "a2"}, {"v": "a3"}]}
        ]

    def test_ll_template(self) -> None:
        """{{ll|a|b}} 是 list 的简写"""
        text = "{{Infobox|别名={{ll|a1|a2}}}}"
        result = parse_infobox(text)
        assert result == [{"key": "别名", "value": [{"v": "a1"}, {"v": "a2"}]}]

    def test_list_template_case_insensitive(self) -> None:
        """模板名不区分大小写"""
        text = "{{Infobox|别名={{LIST|a1|a2}}}}"
        result = parse_infobox(text)
        assert result == [{"key": "别名", "value": [{"v": "a1"}, {"v": "a2"}]}]

    def test_br_separator(self) -> None:
        """<br> 分隔（至少 2 项才视为列表）"""
        text = "{{Infobox|别名=a1<br>a2}}"
        result = parse_infobox(text)
        assert result == [{"key": "别名", "value": [{"v": "a1"}, {"v": "a2"}]}]

    def test_br_with_slash(self) -> None:
        """<br/> 和 <br /> 也识别"""
        text = "{{Infobox|别名=a1<br/>a2<br />a3}}"
        result = parse_infobox(text)
        assert result == [
            {"key": "别名", "value": [{"v": "a1"}, {"v": "a2"}, {"v": "a3"}]}
        ]

    def test_br_single_item_returns_string(self) -> None:
        """<br> 分隔但仅 1 个非空项时返回字符串"""
        text = "{{Infobox|别名=only_one<br>}}"
        result = parse_infobox(text)
        assert result == [{"key": "别名", "value": "only_one"}]

    def test_mixed_list_and_text(self) -> None:
        """bullet list 与其他文本混合时，bullet 优先"""
        text = "{{Infobox|别名=前置文本\n* a1\n* a2}}"
        result = parse_infobox(text)
        # bullet 识别优先，前置文本被丢弃
        assert result == [{"key": "别名", "value": [{"v": "a1"}, {"v": "a2"}]}]


def _alias_of(parsed: list[dict]) -> object:
    """取 infobox 解析结果中「别名」项的 value（便于断言多参数场景）"""
    for item in parsed:
        if item.get("key") == "别名":
            return item.get("value")
    return None


# ===== 方块列表 `{[a][b]}`（Archive dump 别名主流格式） =====


class TestBraceBracketList:
    """`{\\n[a]\\n[b]\\n}` 方块列表

    Archive dump 中 98.9% 的「别名」字段使用此格式（见
    alias_format_census.txt）。未识别时会退化成整块脏字符串，
    导致 FTS aliases 列含噪声、带空格的别名无法精确命中。
    """

    def test_brace_bracket_list_crlf(self) -> None:
        """CRLF 换行的方块列表被拆成独立别名"""
        text = "{{Infobox|别名={\r\n[叛逆的鲁路修R2]\r\n[Code Geass]\r\n}\r\n|其他=x}}"
        result = parse_infobox(text)
        assert _alias_of(result) == [
            {"v": "叛逆的鲁路修R2"},
            {"v": "Code Geass"},
        ]

    def test_brace_bracket_list_lf(self) -> None:
        """LF 换行同样识别"""
        text = "{{Infobox|别名={\n[a1]\n[a2]\n}\n|其他=x}}"
        result = parse_infobox(text)
        assert _alias_of(result) == [{"v": "a1"}, {"v": "a2"}]

    def test_brace_bracket_single_item(self) -> None:
        """单个方括号项也返回列表结构（与 bullet 单行为一致）"""
        text = "{{Infobox|别名={\n[only]\n}\n|其他=x}}"
        result = parse_infobox(text)
        assert _alias_of(result) == [{"v": "only"}]

    def test_brace_bracket_skips_blank_lines(self) -> None:
        """空行被跳过，不产生空别名"""
        text = "{{Infobox|别名={\n\n[a1]\n   \n[a2]\n}\n|其他=x}}"
        result = parse_infobox(text)
        assert _alias_of(result) == [{"v": "a1"}, {"v": "a2"}]

    def test_brace_without_bracket_items_falls_through(self) -> None:
        """大括号内无整行方括号项（如 `{链接}`）不视为列表"""
        text = "{{Infobox|其他={链接}}}"
        result = parse_infobox(text)
        assert result == [{"key": "其他", "value": "{链接"}]

    def test_brace_bracket_wiki_link_cleaned(self) -> None:
        """方块列表项内的 wiki 链接被清理"""
        text = "{{Infobox|别名={\n[[target|显示名]]\n}\n|其他=x}}"
        result = parse_infobox(text)
        assert _alias_of(result) == [{"v": "显示名"}]

    def test_bullet_takes_priority_over_brace(self) -> None:
        """同时存在 bullet 与方括号时 bullet 优先（保持既有行为）"""
        text = "{{Infobox|别名=* b1\n* b2}}"
        result = parse_infobox(text)
        assert _alias_of(result) == [{"v": "b1"}, {"v": "b2"}]


# ===== wiki 标记清理 =====


class TestWikiMarkupClean:
    """wiki 标记清理"""

    def test_wiki_link_simple(self) -> None:
        """[[link]] → link"""
        text = "{{Infobox|alias=[[Test]]}}"
        result = parse_infobox(text)
        assert result == [{"key": "alias", "value": "Test"}]

    def test_wiki_link_with_display(self) -> None:
        """[[link|显示]] → 显示"""
        text = "{{Infobox|alias=[[target|display]]}}"
        result = parse_infobox(text)
        assert result == [{"key": "alias", "value": "display"}]

    def test_bold_italic(self) -> None:
        """'''bold''' 和 ''italic'' 标记被去除"""
        text = "{{Infobox|alias='''bold''' and ''italic''}}"
        result = parse_infobox(text)
        assert result == [{"key": "alias", "value": "bold and italic"}]

    def test_link_in_bullet_list(self) -> None:
        """列表项内的 wiki 链接也被清理"""
        text = "{{Infobox|别名=* [[a1|alias1]]\n* [[a2]]}}"
        result = parse_infobox(text)
        assert result == [{"key": "别名", "value": [{"v": "alias1"}, {"v": "a2"}]}]


# ===== 边界与容错 =====


class TestParseInfoboxEdgeCases:
    """边界与异常容错"""

    def test_empty_string(self) -> None:
        assert parse_infobox("") == []

    def test_none_input(self) -> None:
        """None 输入返回空列表（不抛异常）"""
        assert parse_infobox(None) == []  # type: ignore[arg-type]

    def test_non_infobox_template(self) -> None:
        """非 Infobox 模板返回空"""
        text = "{{Other|alias=Test}}"
        assert parse_infobox(text) == []

    def test_plain_text(self) -> None:
        """纯文本返回空"""
        assert parse_infobox("just text") == []

    def test_unclosed_template(self) -> None:
        """未闭合的 {{ 返回空"""
        text = "{{Infobox|alias=Test"
        assert parse_infobox(text) == []

    def test_malformed_no_braces(self) -> None:
        """无大括号返回空"""
        assert parse_infobox("Infobox|alias=Test") == []

    def test_nested_template_in_value(self) -> None:
        """值中包含嵌套模板时正确分割参数"""
        text = "{{Infobox|key1={{inner|a|b}}|key2=value2}}"
        result = parse_infobox(text)
        # key1 的值是 {{inner|a|b}}，不是 list/ll 模板，应作为字符串处理
        assert len(result) == 2
        assert result[0]["key"] == "key1"
        assert result[1] == {"key": "key2", "value": "value2"}

    def test_nested_template_not_split_by_pipe(self) -> None:
        """嵌套模板内的 | 不应被当作参数分隔符"""
        text = "{{Infobox|别名={{list|a1|a2}}|中文名=测试}}"
        result = parse_infobox(text)
        assert result == [
            {"key": "别名", "value": [{"v": "a1"}, {"v": "a2"}]},
            {"key": "中文名", "value": "测试"},
        ]

    def test_deeply_nested_templates(self) -> None:
        """多层嵌套模板的深度计数"""
        text = "{{Infobox|key1={{outer|{{inner|a}}|b}}|key2=v2}}"
        result = parse_infobox(text)
        assert len(result) == 2
        assert result[1] == {"key": "key2", "value": "v2"}

    def test_extra_braces_outside(self) -> None:
        """模板外有多余大括号"""
        text = "}{{Infobox|alias=Test}}"
        # 起始 regex 要求 {{ 在开头（忽略空白），所以 } 在前会导致不匹配
        assert parse_infobox(text) == []

    def test_chinese_keys(self) -> None:
        """中文 key 完整支持"""
        text = "{{Infobox|中文名=测试|别名=* a1\n* a2|罗马音=Romaji}}"
        result = parse_infobox(text)
        assert result == [
            {"key": "中文名", "value": "测试"},
            {"key": "别名", "value": [{"v": "a1"}, {"v": "a2"}]},
            {"key": "罗马音", "value": "Romaji"},
        ]

    def test_unpaired_closing_brackets_do_not_swallow_params(self) -> None:
        """值中出现未配对的 ]] 时，其后的顶层 | 仍能切分参数

        真实条目（竞技体育类）的别名常写成 ``[アタックNo.1[ナンバーワン]]``，
        单个 ``[`` 使括号不配对，深度不得转负而吞掉后续参数。
        """
        text = (
            "{{Infobox|别名=[アタックNo.1[ナンバーワン]]|话数=104|放送开始=1969-12-07}}"
        )
        result = parse_infobox(text)
        assert {"key": "话数", "value": "104"} in result
        assert {"key": "放送开始", "value": "1969-12-07"} in result

    def test_split_params_depth_not_negative(self) -> None:
        """未配对的 }} / ]] 不使深度转负，后续参数仍按顶层 | 切分"""
        assert _split_top_level_params("a=[x]]|b=1") == ["a=[x]]", "b=1"]
        assert _split_top_level_params("a=x}}|b=1") == ["a=x}}", "b=1"]

    def test_unclosed_opening_brackets_do_not_swallow_params(self) -> None:
        """值中出现未闭合的 [[ 时，其后的顶层 | 仍能切分参数

        真实条目（我推的孩子 第三季）的别名写作 ``[[Oshi no Ko] 3rd Season]``，
        只有 ``[[`` 没有 ``]]``，深度不得滞留而吞掉后续参数。
        """
        text = (
            "{{Infobox animanga/TVAnime\r\n"
            "|中文名= 【我推的孩子】 第三季\r\n"
            "|别名={\r\n"
            "[[Oshi no Ko] 3rd Season]\r\n"
            "[Oshi no Ko Season 3]\r\n"
            "}\r\n"
            "|话数= 11\r\n"
            "|放送开始= 2026年1月14日\r\n"
            "}}"
        )
        result = parse_infobox(text)
        assert {"key": "话数", "value": "11"} in result
        assert {"key": "放送开始", "value": "2026年1月14日"} in result

    def test_split_params_resets_bracket_depth_at_newline(self) -> None:
        """换行处重置方括号深度：``[[...]]`` 链接不跨行，未闭合的 [[ 不跨越换行生效"""
        assert _split_top_level_params(
            "别名=[[Oshi no Ko] 3rd Season]\r\n|话数= 11"
        ) == [
            "别名=[[Oshi no Ko] 3rd Season]\r\n",
            "话数= 11",
        ]
        assert _split_top_level_params("a=[[x]y]\r\n|b=1\r\n|c=2") == [
            "a=[[x]y]\r\n",
            "b=1\r\n",
            "c=2",
        ]

    def test_split_params_keeps_pipe_inside_balanced_link(self) -> None:
        """同一行内成对 ``[[target|display]]`` 的 | 仍不切分（链接的保护语义不变）"""
        assert _split_top_level_params("别名=[[A|B]]|话数=1") == [
            "别名=[[A|B]]",
            "话数=1",
        ]


# ===== _store._adapt_subject_row 集成测试 =====


class TestStoreAdaptSubjectRowInfobox:
    """_adapt_subject_row 接入 wiki_parser 后的 infobox 适配"""

    def test_infobox_parsed_to_list(self) -> None:
        row = {
            "id": 1,
            "name": "Test",
            "infobox": "{{Infobox|alias=TestAlias|中文名=测试}}",
        }
        result = ArchiveStore._adapt_subject_row(row)
        assert result["infobox"] == [
            {"key": "alias", "value": "TestAlias"},
            {"key": "中文名", "value": "测试"},
        ]

    def test_infobox_with_list_value(self) -> None:
        row = {
            "id": 1,
            "name": "Test",
            "infobox": "{{Infobox|别名=* a1\n* a2}}",
        }
        result = ArchiveStore._adapt_subject_row(row)
        assert result["infobox"] == [
            {"key": "别名", "value": [{"v": "a1"}, {"v": "a2"}]}
        ]

    def test_infobox_empty_string(self) -> None:
        row = {"id": 1, "name": "Test", "infobox": ""}
        result = ArchiveStore._adapt_subject_row(row)
        assert result["infobox"] == []

    def test_infobox_none(self) -> None:
        row = {"id": 1, "name": "Test", "infobox": None}
        result = ArchiveStore._adapt_subject_row(row)
        assert result["infobox"] == []

    def test_infobox_non_infobox_template(self) -> None:
        """非 Infobox 模板解析失败，回退为空列表"""
        row = {"id": 1, "name": "Test", "infobox": "{{Other|key=v}}"}
        result = ArchiveStore._adapt_subject_row(row)
        assert result["infobox"] == []

    def test_tags_json_deserialized(self) -> None:
        """tags 字段仍按 JSON 反序列化（不受 wiki_parser 影响）"""
        row = {
            "id": 1,
            "name": "Test",
            "infobox": "{{Infobox|alias=Test}}",
            "tags": '[{"name":"tag1","count":10}]',
        }
        result = ArchiveStore._adapt_subject_row(row)
        assert result["tags"] == [{"name": "tag1", "count": 10}]
        assert result["infobox"] == [{"key": "alias", "value": "Test"}]

    def test_infobox_already_list_kept_as_is(self) -> None:
        """已是 list 的 infobox（异常情况）保持原样"""
        original_infobox = [{"key": "alias", "value": "Test"}]
        row = {"id": 1, "name": "Test", "infobox": original_infobox}
        result = ArchiveStore._adapt_subject_row(row)
        assert result["infobox"] is original_infobox


# ===== 真实场景样例 =====


class TestRealWorldSamples:
    """模拟真实 Bangumi 条目的 infobox 样例"""

    def test_anime_subject_sample(self) -> None:
        """动画条目样例：含 中文名/别名/罗马音/话数 等"""
        text = (
            "{{Infobox\n"
            "|中文名=测试动画\n"
            "|别名={{ll|别名1|别名2|别名3}}\n"
            "|话数=12\n"
            "|放送开始=2024-04\n"
            "}}"
        )
        result = parse_infobox(text)
        assert {"key": "中文名", "value": "测试动画"} in result
        assert {
            "key": "别名",
            "value": [{"v": "别名1"}, {"v": "别名2"}, {"v": "别名3"}],
        } in result
        assert {"key": "话数", "value": "12"} in result

    def test_real_subject_sample_with_br(self) -> None:
        """使用 <br> 分隔别名的条目"""
        text = "{{Infobox|中文名=测试|别名=别名A<br>别名B<br>别名C}}"
        result = parse_infobox(text)
        assert result == [
            {"key": "中文名", "value": "测试"},
            {
                "key": "别名",
                "value": [{"v": "别名A"}, {"v": "别名B"}, {"v": "别名C"}],
            },
        ]

    def test_real_subject_sample_with_brace_alias_block(self) -> None:
        """别名使用 {...} 块并含 ``[x[y]]`` 的条目：后续参数不得被并进别名

        结构取自真实条目（排球甜心 / subject 53773）的原始 infobox。
        """
        text = (
            "{{Infobox animanga/TVAnime\r\n"
            "|中文名= 排球甜心\r\n"
            "|别名={\r\n"
            "[女排No.1]\r\n"
            "[アタックNo.1[ナンバーワン]]\r\n"
            "}\r\n"
            "|话数= 104\r\n"
            "|放送开始= 1969年12月7日\r\n"
            "}}"
        )
        result = parse_infobox(text)
        assert {"key": "话数", "value": "104"} in result
        assert {"key": "放送开始", "value": "1969年12月7日"} in result
        assert [x["key"] for x in result][:3] == ["中文名", "别名", "话数"]

    def test_real_subject_sample_with_unclosed_link_alias(self) -> None:
        """别名含未闭合 ``[[`` 的条目：后续参数不得被并进别名

        结构取自真实条目（我推的孩子 第三季 / subject 517057）的原始 infobox。
        """
        text = (
            "{{Infobox animanga/TVAnime\r\n"
            "|中文名= 【我推的孩子】 第三季\r\n"
            "|别名={\r\n"
            "[[Oshi no Ko] 3rd Season]\r\n"
            "[Oshi no Ko Season 3]\r\n"
            '["Oshi no Ko" 3]\r\n'
            "}\r\n"
            "|话数= 11\r\n"
            "|放送开始= 2026年1月14日\r\n"
            "|放送星期= 星期三\r\n"
            "}}"
        )
        result = parse_infobox(text)
        assert {"key": "话数", "value": "11"} in result
        assert {"key": "放送星期", "value": "星期三"} in result
        assert [x["key"] for x in result][:3] == ["中文名", "别名", "话数"]
