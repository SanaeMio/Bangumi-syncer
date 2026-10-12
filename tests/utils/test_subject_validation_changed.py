"""collect_changed_subject_ids：写入前只校验「变化过的」subject_id

背景（review 阻断级 1）：`POST /api/mappings` 名义上只校验本次提交的 ID，
但前端保存（新增 / 编辑 / 改规则 / 导入）每次都把**整份** mappings + rules
塞进 body，于是后端实际把配置里所有 ID 都重验了一遍 —— archive 未命中时
每个 ID 都会新建 API 客户端同步 GET，映射一多就是一次扇出。

更麻烦的是「坏 ID 绑架无关编辑」：配置里只要留着一个历史无效 ID（条目被删 /
合并，或早期写入的书籍条目），之后改另一条映射、加一条正则都会 400，且
「删掉那一条」「清空全部」本身也要过校验，用户被锁死只能手改文件。

本文件钉死差集语义：未改动的跳过、新增/改动的校验。
"""

from app.utils.bangumi_api.subject_validation import (
    collect_changed_subject_ids,
    collect_subject_ids,
)


class TestChangedEntries:
    def test_new_entry_is_collected(self):
        out = collect_changed_subject_ids({"新番": "999"}, None, {}, None)
        assert out == ["999"]

    def test_unchanged_entry_is_skipped(self):
        out = collect_changed_subject_ids({"番": "111"}, None, {"番": "111"}, None)
        assert out == []

    def test_changed_value_is_collected(self):
        out = collect_changed_subject_ids({"番": "999"}, None, {"番": "111"}, None)
        assert out == ["999"]

    def test_removed_entry_is_not_collected(self):
        """删除的条目不在提交里，天然不会被校验（否则删掉坏 ID 会被挡住）"""
        out = collect_changed_subject_ids({}, None, {"坏番": "111"}, None)
        assert out == []

    def test_simple_to_dict_migration_counts_as_change(self):
        """简单格式 "123" → 对象格式 {subject_id: "123"} 也算变化"""
        out = collect_changed_subject_ids(
            {"番": {"subject_id": "123", "season": 2}}, None, {"番": "123"}, None
        )
        assert out == ["123"]

    def test_dict_key_order_does_not_matter(self):
        """对象格式的键序不同不应被判成变化（JSON 规范化比较）"""
        out = collect_changed_subject_ids(
            {"番": {"season": 2, "subject_id": "123"}},
            None,
            {"番": {"subject_id": "123", "season": 2}},
            None,
        )
        assert out == []

    def test_segments_of_changed_entry_are_collected(self):
        out = collect_changed_subject_ids(
            {"JOJO": {"subject_id": "43558", "segments": [{"subject_id": "639938"}]}},
            None,
            {"JOJO": "43558"},
            None,
        )
        assert out == ["43558", "639938"]

    def test_segments_of_unchanged_entry_are_skipped(self):
        entry = {"subject_id": "43558", "segments": [{"subject_id": "639938"}]}
        out = collect_changed_subject_ids({"JOJO": entry}, None, {"JOJO": entry}, None)
        assert out == []


class TestChangedRules:
    def test_new_rule_is_collected(self):
        out = collect_changed_subject_ids(
            {}, [{"pattern": "^x$", "subject_id": "999"}], {}, []
        )
        assert out == ["999"]

    def test_unchanged_rule_is_skipped(self):
        rule = {"pattern": "^x$", "subject_id": "111"}
        out = collect_changed_subject_ids({}, [rule], {}, [rule])
        assert out == []

    def test_edited_rule_collects_only_new_id(self):
        out = collect_changed_subject_ids(
            {},
            [
                {"pattern": "^甲$", "subject_id": "111"},
                {"pattern": "^乙$", "subject_id": "333"},
            ],
            {},
            [
                {"pattern": "^甲$", "subject_id": "111"},
                {"pattern": "^乙$", "subject_id": "222"},
            ],
        )
        assert out == ["333"]

    def test_duplicate_identical_rules_consumed_one_by_one(self):
        """两条完全相同的规则：磁盘一条 → 提交两条时只算一条是新增"""
        rule = {"pattern": "^x$", "subject_id": "111"}
        out = collect_changed_subject_ids({}, [rule, dict(rule)], {}, [rule])
        assert out == ["111"]

    def test_rules_none_means_keep_existing(self):
        """rules=None 表示保留现有规则 → 不应产生任何待校验 ID"""
        rule = {"pattern": "^x$", "subject_id": "111"}
        out = collect_changed_subject_ids({}, None, {}, [rule])
        assert out == []


class TestDedupAndOrder:
    def test_same_id_in_mapping_and_rule_collected_once(self):
        out = collect_changed_subject_ids(
            {"番": "111"},
            [{"pattern": "^x$", "subject_id": "111"}],
            {},
            [],
        )
        assert out == ["111"]

    def test_order_is_stable(self):
        out = collect_changed_subject_ids({"a": "111", "b": "222"}, None, {}, None)
        assert out == ["111", "222"]


class TestEdgeCases:
    def test_empty_submission_yields_nothing(self):
        """清空全部配置必须能成功（否则坏 ID 会把用户锁死）"""
        out = collect_changed_subject_ids({}, [], {"坏番": "111"}, [])
        assert out == []

    def test_none_entries_are_ignored(self):
        out = collect_changed_subject_ids({"番": None}, None, {}, None)
        assert out == []

    def test_non_dict_rules_are_ignored(self):
        out = collect_changed_subject_ids({}, ["not-a-dict"], {}, [])
        assert out == []


class TestFullCollectStillAvailable:
    def test_collect_subject_ids_unchanged_behaviour(self):
        """全量收集仍供其它路径使用，语义不变"""
        out = collect_subject_ids(
            {"番": {"subject_id": "1", "segments": [{"subject_id": "2"}]}},
            [{"subject_id": "3"}],
        )
        assert out == ["1", "2", "3"]
