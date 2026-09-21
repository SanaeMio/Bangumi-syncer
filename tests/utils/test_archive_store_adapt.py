"""Archive subject 行适配 + 同 IP 关系集合 单元测试

覆盖两条 2026-09-09 实测修复：

1. ``_adapt_subject_row`` 反序列化 ``favorite``（此前只处理 tags/score/
   score_details/meta_tags，导致候选里的 favorite 恒为 str，#7 favorite
   tie-breaker 无法落地）
2. ``FRANCHISE_RELATION_CN_SET`` 移除「衍生」——离线侧 FRANCHISE_RELATION_TYPES
   本就不含衍生，在线侧曾误含，导致 franchise 兜底把广播/游戏等衍生节目
   当作本篇参与 sort 定位（实测：鬼灭之刃 ep=28 → 鬼灭广播 404879，89 集）
"""

from __future__ import annotations

import pytest

from app.utils.bangumi_archive._store import (
    FRANCHISE_RELATION_CN_SET,
    FRANCHISE_RELATION_TYPES,
    ArchiveStore,
)


class TestAdaptSubjectRowFavorite:
    """favorite 字段反序列化（#7 前置）"""

    def test_favorite_json_str_becomes_dict(self):
        row = {
            "id": 434076,
            "name": "凡人修仙传",
            "favorite": '{"wish": 9, "done": 42, "doing": 16}',
        }
        out = ArchiveStore._adapt_subject_row(row)
        assert out["favorite"] == {"wish": 9, "done": 42, "doing": 16}
        assert out["favorite"]["done"] == 42

    def test_favorite_invalid_json_kept_as_str(self):
        """解析失败时保留原字符串，不破坏数据（与其他 JSON 字段行为一致）"""
        row = {"id": 1, "favorite": "not-json"}
        out = ArchiveStore._adapt_subject_row(row)
        assert out["favorite"] == "not-json"

    def test_favorite_missing_or_empty_untouched(self):
        """缺失/空值不反序列化（注意 infobox 会按契约补为 []）"""
        assert ArchiveStore._adapt_subject_row({"id": 1}) == {
            "id": 1,
            "infobox": [],
        }
        assert (
            ArchiveStore._adapt_subject_row({"id": 1, "favorite": ""})["favorite"] == ""
        )

    def test_other_json_fields_still_parsed(self):
        row = {
            "id": 1,
            "favorite": '{"done": 7}',
            "meta_tags": '["中国", "动画"]',
            "tags": '[{"name": "科幻"}]',
        }
        out = ArchiveStore._adapt_subject_row(row)
        assert out["meta_tags"] == ["中国", "动画"]
        assert out["tags"] == [{"name": "科幻"}]
        assert out["favorite"] == {"done": 7}


class TestFranchiseRelationSets:
    """同 IP 关系集合：离线/在线语义一致 + 排除衍生边"""

    def test_衍生_not_in_online_cn_set(self):
        """「衍生」必须排除：衍生节目（广播/游戏）章节体系与本篇不同"""
        assert "衍生" not in FRANCHISE_RELATION_CN_SET

    def test_offline_types_exclude_derivative(self):
        """离线侧 relation_type 11（衍生）不在集合内"""
        assert 11 not in FRANCHISE_RELATION_TYPES

    def test_cross_media_relation_preserved(self):
        """跨媒体场景（动画 ↔ 真人剧）依赖「不同演绎」，移除衍生不能误伤"""
        assert "不同演绎" in FRANCHISE_RELATION_CN_SET
        assert "改编" in FRANCHISE_RELATION_CN_SET

    def test_core_relations_present(self):
        for name in ("前传", "续集", "主线故事"):
            assert name in FRANCHISE_RELATION_CN_SET

    @pytest.mark.parametrize(
        "noise", ["角色出演", "不同世界观", "联动", "其他", "全集"]
    )
    def test_noise_relations_excluded(self, noise):
        assert noise not in FRANCHISE_RELATION_CN_SET
