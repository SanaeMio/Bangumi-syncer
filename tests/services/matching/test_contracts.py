"""契约层单元测试（P2）

覆盖 SubjectRef / EpisodeRef / 候选构造 / margin 三态语义。
契约层不改变匹配控制流，这里验证的是「数据形态归一」本身。
"""

from __future__ import annotations

import pytest

from app.services.matching.contracts import (
    SOURCE_API_SEARCH,
    SOURCE_ARCHIVE,
    SOURCE_BANGUMI_DATA,
    EpisodeRef,
    SubjectRef,
    candidates_from_rows,
    compute_margin,
    make_candidate,
)


class TestSubjectRef:
    def test_from_archive_maps_fields(self):
        ref = SubjectRef.from_archive(
            {
                "id": 123,
                "name": "鬼滅の刃",
                "name_cn": "鬼灭之刃",
                "date": "2019-04-06",
                "platform": "TV",
                "type": 2,
            }
        )
        assert ref.subject_id == "123"
        assert ref.name == "鬼滅の刃"
        assert ref.name_cn == "鬼灭之刃"
        assert ref.air_date == "2019-04-06"
        assert ref.platform == "TV"
        assert ref.subject_type == 2
        assert ref.source == SOURCE_ARCHIVE

    def test_from_api_same_shape_as_archive(self):
        ref = SubjectRef.from_api(
            {"id": 456, "name": "A", "name_cn": "甲", "date": "2020-01-01"}
        )
        assert ref.subject_id == "456"
        assert ref.source == SOURCE_API_SEARCH

    def test_from_bangumi_data_maps_different_shape(self):
        """bangumi-data 用 title/titleTranslate/begin/sites，与另两源完全不同"""
        ref = SubjectRef.from_bangumi_data(
            {
                "title": "魔法少女",
                "titleTranslate": {"zh-Hans": ["魔法少女"]},
                "begin": "2021-01-09",
                "sites": [
                    {"site": "bangumi", "id": "789"},
                    {"site": "anilist", "id": "x"},
                ],
            }
        )
        assert ref.subject_id == "789"
        assert ref.name == "魔法少女"
        assert ref.name_cn == "魔法少女"
        assert ref.air_date == "2021-01-09"
        assert ref.source == SOURCE_BANGUMI_DATA

    def test_from_bangumi_data_missing_id(self):
        ref = SubjectRef.from_bangumi_data({"title": "无 sites"})
        assert ref.subject_id == ""

    def test_display_name_prefers_cn(self):
        ref = SubjectRef(subject_id="1", name="Orig", name_cn="中文")
        assert ref.display_name == "中文"
        assert SubjectRef(subject_id="1", name="Orig").display_name == "Orig"


class TestEpisodeRef:
    def test_from_api_maps_ep_sort_type(self):
        ref = EpisodeRef.from_api(
            {
                "id": 100,
                "subject_id": 5,
                "sort": 12.5,
                "ep": 3,
                "type": 0,
                "airdate": "2020-01-01",
            }
        )
        assert ref.episode_id == "100"
        assert ref.subject_id == "5"
        assert ref.sort == 12.5
        assert ref.ep == 3
        assert ref.type == 0

    def test_from_archive_missing_ep_stays_none(self):
        """Archive 无季内集号时 ep 必须保持 None，不能拿 sort 兜底

        下游 episodes.py 以 ``(ep or 0) > 0`` 判定季内话数，若兜底成数字
        会把「无季内话数」误写成数字，破坏季边界判定（242 根因）。
        """
        ref = EpisodeRef.from_archive({"id": 1, "sort": 24, "type": 0})
        assert ref.ep is None
        assert ref.sort == 24.0

    def test_to_api_dict_preserves_existing_values(self):
        """归一只补缺失字段，不覆盖已有值"""
        row = {
            "id": 1,
            "sort": 3,
            "ep": 2,
            "type": 1,
            "name": "SP",
            "custom_field": "keep",
        }
        out = EpisodeRef.from_archive(row).to_api_dict()
        assert out["ep"] == 2
        assert out["custom_field"] == "keep"

    def test_to_api_dict_omits_missing_ep(self):
        """ep 缺失时 to_api_dict 不写入 ep 键（保持缺失，而非 None）"""
        out = EpisodeRef.from_archive({"id": 1, "sort": 5}).to_api_dict()
        assert "ep" not in out

    def test_to_api_dict_fixes_none_sort(self):
        """sort 为 None 时用契约值覆盖（None 会让下游比较出错）"""
        out = EpisodeRef.from_archive({"id": 1, "sort": None, "type": 0}).to_api_dict()
        assert out["sort"] == 0.0


class TestCandidates:
    def test_from_rows_archive(self):
        rows = [
            {"id": 1, "name": "A", "name_cn": "甲", "score": 0.9},
            {"id": 2, "name": "B", "name_cn": "乙", "score": 0.5},
        ]
        cands = candidates_from_rows(rows, SOURCE_ARCHIVE)
        assert [c.subject_id for c in cands] == ["1", "2"]
        assert cands[0].score == 0.9
        assert cands[0].source == SOURCE_ARCHIVE

    def test_from_rows_scorer_overrides(self):
        rows = [{"id": 1, "name": "A"}]
        cands = candidates_from_rows(rows, SOURCE_ARCHIVE, scorer=lambda r: 0.77)
        assert cands[0].score == 0.77

    def test_from_rows_drops_empty_id(self):
        rows = [{"id": "", "name": "无 ID"}, {"id": 9, "name": "有效"}]
        cands = candidates_from_rows(rows, SOURCE_API_SEARCH)
        assert [c.subject_id for c in cands] == ["9"]

    def test_from_rows_unknown_source_raises(self):
        with pytest.raises(ValueError):
            candidates_from_rows([{"id": 1}], "不存在的源")

    def test_from_rows_empty_returns_empty_list(self):
        assert candidates_from_rows(None, SOURCE_ARCHIVE) == []
        assert candidates_from_rows([], SOURCE_ARCHIVE) == []

    def test_make_candidate(self):
        subject = SubjectRef(
            subject_id="1", name="A", name_cn="甲", source=SOURCE_ARCHIVE
        )
        c = make_candidate(subject, score=0.8765, aliases=["别名"])
        assert c.subject_id == "1"
        assert c.score == 0.8765
        assert c.infobox_aliases == ["别名"]


class TestMargin:
    def test_none_when_no_candidates(self):
        assert compute_margin(None) is None
        assert compute_margin([]) is None

    def test_none_when_single_candidate(self):
        assert compute_margin([make_candidate(SubjectRef("1"), 1.0)]) is None

    def test_none_when_all_scores_zero(self):
        """全 0 = 无打分信息（archive 盲信），不是「并列第一」"""
        cands = [
            make_candidate(SubjectRef("1"), 0.0),
            make_candidate(SubjectRef("2"), 0.0),
        ]
        assert compute_margin(cands) is None

    def test_zero_when_tie(self):
        cands = [
            make_candidate(SubjectRef("1"), 1.0),
            make_candidate(SubjectRef("2"), 1.0),
        ]
        assert compute_margin(cands) == 0.0

    def test_positive_margin(self):
        cands = [
            make_candidate(SubjectRef("1"), 1.0),
            make_candidate(SubjectRef("2"), 0.7),
        ]
        assert compute_margin(cands) == 0.3


class TestAdaptEpisodeRowIntegration:
    """_store._adapt_episode_row 落地验证（C2 消费点）

    该方法 docstring 声称「将 Archive 行适配为 BangumiApi 返回结构」，
    此前实现是 ``return row``——契约从未被强制执行。
    """

    @staticmethod
    def _adapt(row):
        from app.utils.bangumi_archive._store import ArchiveStore

        return ArchiveStore._adapt_episode_row(row)

    def test_passes_through_valid_row(self):
        row = {"id": 1, "subject_id": 2, "sort": 3, "ep": 3, "type": 0, "name": "E"}
        out = self._adapt(row)
        assert out["id"] == 1
        assert out["ep"] == 3
        assert out["sort"] == 3

    def test_missing_ep_not_synthesized(self):
        """多季合并时 ep 缺失应保持缺失，由上层 _synthesize_ep_field 负责"""
        out = self._adapt({"id": 1, "subject_id": 2, "sort": 8, "type": 0})
        assert "ep" not in out

    def test_none_sort_coerced(self):
        """sort 为 None 时归一为数字，避免下游比较出错"""
        out = self._adapt({"id": 1, "subject_id": 2, "sort": None, "type": 0})
        assert out["sort"] == 0.0
