"""自定义映射：高级格式、segments 集数分段、写回安全性的回归测试。

这些用例补上了原先的覆盖空洞：既有测试**只用简单字符串格式**
（``{"番A": "111"}``），且 test_mapping_service.py 把 builtins.open 与
json.dump 都 mock 掉、只断言 dump 被调用一次，因此对「高级格式丢字段」
「非法规则被静默删除」「损坏文件被空配置覆盖」这类数据丢失完全无感。
"""

import json
import os

import pytest

from app.services.mapping_service import MappingService


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """在临时 cwd 下构建 MappingService 与可写配置文件"""
    monkeypatch.chdir(tmp_path)
    service = MappingService()

    def _write(data):
        (tmp_path / "bangumi_mapping.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

    service._write_config = _write
    return service


def _read(tmp_path):
    return json.loads((tmp_path / "bangumi_mapping.json").read_text(encoding="utf-8"))


def _capture_logs() -> list[str]:
    """注册监听器并返回捕获列表（收集 app 自定义 logger 的输出）。

    项目用的是 app.core.logging.Logger（不是 stdlib logging），pytest 的
    caplog 抓不到它；它自带 add_listener 回调接口，这里把日志行收进列表。
    """
    from app.core.logging import logger

    captured: list[str] = []
    logger.add_listener(lambda line, _level: captured.append(line))
    return captured


# --------------------------------------------------------------------------
# 季度感知格式：命中语义与显式标记
# --------------------------------------------------------------------------


class TestSeasonAwareMapping:
    def test_season_match_and_explicit_flag(self, svc):
        """声明了 season 的映射命中时，is_explicit 必须为 True"""
        svc._write_config({"mappings": {"某番": {"subject_id": "123", "season": 2}}})
        sid, mtype, _reason, explicit = svc.find_mapping("某番", "", 2)
        assert sid == "123"
        assert mtype == "season"
        assert explicit is True

    def test_season_mismatch_falls_through(self, svc):
        """season 不匹配时不应命中（落到未命中）"""
        svc._write_config({"mappings": {"某番": {"subject_id": "123", "season": 2}}})
        assert svc.find_mapping("某番", "", 3) == ("", "", "", False)

    def test_simple_format_is_not_explicit(self, svc):
        """简单格式保持历史语义（填主条目、程序往后找），is_explicit 为 False"""
        svc._write_config({"mappings": {"某番": "123"}})
        assert svc.find_mapping("某番", "", 1) == (
            "123",
            "exact",
            "自定义映射命中：某番=123",
            False,
        )

    def test_unparsable_season_does_not_raise(self, svc):
        """season 写成非数字时跳过季度条件，绝不抛异常打断同步"""
        svc._write_config({"mappings": {"某番": {"subject_id": "123", "season": "S2"}}})
        sid, mtype, _reason, explicit = svc.find_mapping("某番", "", 5)
        assert sid == "123"
        assert mtype == "season"
        # season 不可解析 → 视为未声明 → 不标记为显式
        assert explicit is False

    def test_dict_without_subject_id_is_skipped(self, svc):
        """缺 subject_id 的配置对象应被跳过，而不是崩在 str(None)"""
        svc._write_config({"mappings": {"某番": {"season": 2}}})
        assert svc.find_mapping("某番", "", 2) == ("", "", "", False)


# --------------------------------------------------------------------------
# segments 集数分段映射
# --------------------------------------------------------------------------


class TestSegmentMapping:
    # 媒体 S6 的 E1 归条目 A；E2 起归条目 B，且 B 的第 1 集从 E2 开始
    # （offset = 该段第一集对应的目标集号 = 1）
    CFG = {
        "mappings": {
            "JOJO": {
                "subject_id": "43558",
                "segments": [
                    {"season": 6, "from": 1, "to": 1, "subject_id": "A"},
                    {
                        "season": 6,
                        "from": 2,
                        "to": None,
                        "subject_id": "B",
                        "offset": 1,
                    },
                ],
            }
        }
    }

    def test_segment_boundaries(self, svc):
        """段边界：E1→A E1；E2→B E1；E3→B E2（offset 为首集目标集号）"""
        svc._write_config(self.CFG)
        assert svc.find_episode_mapping("JOJO", "", 6, 1)[:2] == ("A", 1)
        assert svc.find_episode_mapping("JOJO", "", 6, 2)[:2] == ("B", 1)
        assert svc.find_episode_mapping("JOJO", "", 6, 3)[:2] == ("B", 2)

    def test_issue_267_scenario(self, svc):
        """issue #267 场景：媒体 S06E03 → 目标条目 E02

        用户的诉求是「E3 起归条目 639938，且 E3 对应该条目的 E2」，
        即 from=3、offset=2。
        """
        svc._write_config(
            {
                "mappings": {
                    "JOJO的奇妙冒险": {
                        "subject_id": "43558",
                        "segments": [
                            {
                                "season": 6,
                                "from": 3,
                                "to": None,
                                "subject_id": "639938",
                                "offset": 2,
                            }
                        ],
                    }
                }
            }
        )
        assert svc.find_episode_mapping("JOJO的奇妙冒险", "", 6, 3)[:2] == (
            "639938",
            2,
        )
        assert svc.find_episode_mapping("JOJO的奇妙冒险", "", 6, 4)[:2] == (
            "639938",
            3,
        )

    def test_season_is_a_match_condition(self, svc):
        """段的 season 不匹配时不应命中（这就是 segments 需要 season 字段的原因）"""
        svc._write_config(self.CFG)
        assert svc.find_episode_mapping("JOJO", "", 5, 3) == ("", None, "")

    def test_offset_shifts_to_target_number(self, svc):
        """offset = 该段第一集对应的目标集号"""
        svc._write_config(
            {
                "mappings": {
                    "番": {
                        "subject_id": "9",
                        "segments": [{"season": 1, "from": 1, "to": None, "offset": 3}],
                    }
                }
            }
        )
        # from=1, offset=3 → E1→E3, E2→E4
        assert svc.find_episode_mapping("番", "", 1, 1)[:2] == ("9", 3)
        assert svc.find_episode_mapping("番", "", 1, 2)[:2] == ("9", 4)

    def test_episodes_of_overrides_offset(self, svc):
        """段内逐集例外优先于 offset"""
        svc._write_config(
            {
                "mappings": {
                    "番": {
                        "subject_id": "9",
                        "segments": [
                            {
                                "season": 1,
                                "from": 1,
                                "to": None,
                                "offset": 1,
                                "episodes_of": {"3": 9},
                            }
                        ],
                    }
                }
            }
        )
        assert svc.find_episode_mapping("番", "", 1, 2)[:2] == ("9", 2)
        assert svc.find_episode_mapping("番", "", 1, 3)[:2] == ("9", 9)

    def test_segment_without_subject_id_falls_back_to_top_level(self, svc):
        """段未指定 subject_id 时继承顶层 subject_id"""
        svc._write_config(
            {
                "mappings": {
                    "番": {
                        "subject_id": "top",
                        "segments": [{"season": 1, "from": 1, "to": None}],
                    }
                }
            }
        )
        assert svc.find_episode_mapping("番", "", 1, 1)[0] == "top"

    def test_overlapping_segments_take_first(self, svc):
        """重叠时取数组中靠前的一段（可预测）"""
        svc._write_config(
            {
                "mappings": {
                    "番": {
                        "subject_id": "9",
                        "segments": [
                            {"season": 1, "from": 1, "to": 5, "subject_id": "first"},
                            {"season": 1, "from": 3, "to": 9, "subject_id": "second"},
                        ],
                    }
                }
            }
        )
        assert svc.find_episode_mapping("番", "", 1, 4)[0] == "first"

    def test_gap_returns_empty_for_caller_fallback(self, svc):
        """区间空缺时返回空，由调用方回退顶层 subject_id"""
        svc._write_config(
            {
                "mappings": {
                    "番": {
                        "subject_id": "9",
                        "segments": [
                            {"season": 1, "from": 1, "to": 2, "subject_id": "A"}
                        ],
                    }
                }
            }
        )
        assert svc.find_episode_mapping("番", "", 1, 50) == ("", None, "")

    def test_season_omitted_matches_any_season(self, svc):
        """段省略 season 时对任意季生效（= 旧简单映射语义）"""
        svc._write_config(
            {
                "mappings": {
                    "番": {
                        "subject_id": "9",
                        "segments": [{"from": 1, "to": None, "subject_id": "any"}],
                    }
                }
            }
        )
        assert svc.find_episode_mapping("番", "", 7, 1)[0] == "any"

    def test_segments_makes_mapping_explicit(self, svc):
        """带 segments 的配置视为显式指定，命中映射时应被标记"""
        svc._write_config(self.CFG)
        _sid, _mtype, _reason, explicit = svc.find_mapping("JOJO", "", 6)
        assert explicit is True

    def test_empty_episode_returns_empty(self, svc):
        svc._write_config(self.CFG)
        assert svc.find_episode_mapping("JOJO", "", 6, 0) == ("", None, "")

    def test_illegal_segments_type_is_not_explicit(self, svc, caplog):
        """segments 类型非法（如字符串）不应被判为「显式绑定」

        此前用真值判断 `bool(entry.get("segments"))`：字符串 "oops" 为真 →
        explicit=True，但 find_episode_mapping 要求 isinstance(list) 会把它
        整个忽略，于是「宣称显式绑定、实际没有任何段可用」，下游会因此禁止
        跨季兜底却拿不到任何结果。必须要求非空 list 才算显式。
        """
        svc._write_config({"mappings": {"番": {"subject_id": "5", "segments": "oops"}}})
        _sid, _mtype, _reason, explicit = svc.find_mapping("番", "", 1)
        assert explicit is False
        # 该非法字段也确实不产生任何可命中的段
        assert svc.find_episode_mapping("番", "", 1, 3) == ("", None, "")

    def test_inverted_range_warns_and_never_matches(self, svc):
        """to < from 是反向区间，永远不可能命中，必须告警而不是静默失效

        两个边界是各自独立判断的，不做 lower<=upper 校验时该段会永久失效，
        再静默回退到顶层 subject_id —— 用户看到的是「配了分段却好像没生效」。
        """
        svc._write_config(
            {
                "mappings": {
                    "番": {
                        "subject_id": "TOP",
                        "segments": [
                            {
                                "season": 1,
                                "from": 10,
                                "to": 2,
                                "subject_id": "SEG",
                                "offset": 1,
                            }
                        ],
                    }
                }
            }
        )
        captured = _capture_logs()
        for ep in (1, 5, 10, 11):
            assert svc.find_episode_mapping("番", "", 1, ep) == ("", None, "")
        assert any("区间反向" in m for m in captured), captured

    def test_unparsable_episodes_of_warns_and_falls_back(self, svc):
        """episodes_of 的值不可解析时应告警并回退 offset，而不是静默忽略

        「键不存在」与「键存在但值非法」都会返回 None。若不区分，一个写错的
        例外值会悄悄回退成 offset 推算结果 —— 条目是对的、集号是错的，极难发现。
        """
        svc._write_config(
            {
                "mappings": {
                    "番": {
                        "subject_id": "1",
                        "segments": [
                            {
                                "season": 1,
                                "from": 1,
                                "to": None,
                                "subject_id": "2",
                                "offset": 1,
                                "episodes_of": {"2": "x", 3: 9},
                            }
                        ],
                    }
                }
            }
        )

        captured = _capture_logs()
        # 非法值 → 回退 offset（E2→2），并告警
        assert svc.find_episode_mapping("番", "", 1, 2)[:2] == ("2", 2)
        assert any("episodes_of" in m for m in captured), captured

        # 合法例外仍然生效（E3→9），且不告警
        captured.clear()
        assert svc.find_episode_mapping("番", "", 1, 3)[:2] == ("2", 9)
        assert not [m for m in captured if "episodes_of" in m], captured


class TestRegexRuleSeason:
    """正则规则的 season 限定

    原实现完全忽略 rules 里的 season 字段：用户对「同名不同季」的番剧写了
    season 也不报错、只是静默失效 —— 属于最难排查的一类问题。
    """

    CFG = {
        "mappings": {},
        "rules": [
            {
                "pattern": "^某番剧$",
                "subject_id": "200",
                "season": 2,
                "description": "仅第 2 季",
            },
            {"pattern": "^某番剧$", "subject_id": "100", "description": "兜底"},
        ],
    }

    def test_season_matches(self, svc):
        svc._write_config(self.CFG)
        sid, mtype, reason, _ = svc.find_mapping("某番剧", "", 2)
        assert (sid, mtype) == ("200", "regex")
        assert "season=2" in reason

    def test_season_mismatch_skips_to_next_rule(self, svc):
        """season 不符时跳过该规则，落到后续没有 season 的规则"""
        svc._write_config(self.CFG)
        sid, mtype, _reason, _ = svc.find_mapping("某番剧", "", 1)
        assert (sid, mtype) == ("100", "regex")

    def test_season_omitted_matches_any_season(self, svc):
        """省略 season 的规则对所有季生效（向后兼容）"""
        svc._write_config(
            {"mappings": {}, "rules": [{"pattern": "^番$", "subject_id": "7"}]}
        )
        for season in (1, 2, 5):
            sid, _, _, _ = svc.find_mapping("番", "", season)
            assert sid == "7"

    def test_unparsable_season_ignored_with_warning(self, svc):
        """season 不可解析时忽略该条件（而非抛异常炸穿匹配管线）"""
        svc._write_config(
            {
                "mappings": {},
                "rules": [
                    {"pattern": "^番$", "subject_id": "7", "season": "S2"},
                ],
            }
        )
        sid, _, _, _ = svc.find_mapping("番", "", 1)
        assert sid == "7"

    def test_rule_season_survives_round_trip(self, svc, tmp_path):
        """写回时不得丢掉规则上的 season 字段"""
        svc._write_config(self.CFG)
        assert svc.update_custom_mappings(svc.get_all_mappings()) is True
        rules = _read(tmp_path)["rules"]
        assert any(r.get("season") == 2 for r in rules)


# --------------------------------------------------------------------------
# 写回安全：不得静默丢数据
# --------------------------------------------------------------------------


class TestWriteRoundTrip:
    def test_malformed_rule_survives_round_trip(self, svc, tmp_path):
        """非法正则规则不得被静默删除（原实现会把过滤后的列表写回）"""
        svc._write_config(
            {
                "mappings": {"番": "1"},
                "rules": [
                    {"pattern": "^GOOD$", "subject_id": "1"},
                    {"pattern": "([unclosed", "subject_id": "2"},
                ],
            }
        )
        assert svc.update_custom_mappings(svc.get_all_mappings()) is True
        rules = _read(tmp_path)["rules"]
        assert len(rules) == 2
        assert any(r.get("pattern") == "([unclosed" for r in rules)

    def test_advanced_format_survives_round_trip(self, svc, tmp_path):
        """{subject_id, season, segments} 必须原样往返，字段不丢"""
        svc._write_config(
            {
                "mappings": {
                    "番": {
                        "subject_id": "123",
                        "season": 2,
                        "segments": [{"season": 2, "from": 1, "to": None, "offset": 5}],
                    }
                }
            }
        )
        assert svc.update_custom_mappings(svc.get_all_mappings()) is True
        entry = _read(tmp_path)["mappings"]["番"]
        assert entry["subject_id"] == "123"
        assert entry["season"] == 2
        assert entry["segments"][0]["offset"] == 5

    def test_unknown_top_level_keys_preserved(self, svc, tmp_path):
        """用户自行添加的顶层键不应被写回抹掉"""
        path = tmp_path / "bangumi_mapping.json"
        path.write_text(
            json.dumps(
                {"mappings": {}, "rules": [], "my_own_note": {"keep": "me"}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        assert svc.update_custom_mappings({"番": "1"}) is True
        assert _read(tmp_path)["my_own_note"] == {"keep": "me"}

    def test_corrupt_file_is_not_overwritten(self, svc, tmp_path):
        """文件损坏时必须拒绝写入，绝不能清空用户配置"""
        path = tmp_path / "bangumi_mapping.json"
        path.write_text("{not valid json", encoding="utf-8")
        assert svc.update_custom_mappings({"番": "1"}) is False
        # 原文件保持原样
        assert path.read_text(encoding="utf-8") == "{not valid json"

    def test_write_is_atomic_no_temp_left(self, svc, tmp_path):
        """写入后不应残留临时文件"""
        svc._write_config({"mappings": {"番": "1"}})
        assert svc.update_custom_mappings({"番": "2"}) is True
        leftovers = [
            p for p in os.listdir(tmp_path) if p.startswith(".bangumi_mapping")
        ]
        assert leftovers == []

    def test_upsert_preserves_segments(self, svc, tmp_path):
        """upsert 单条映射时不得抹掉已有的 segments（原实现重建整个值）"""
        svc._write_config(
            {
                "mappings": {
                    "番": {
                        "subject_id": "old",
                        "season": 2,
                        "segments": [{"season": 2, "from": 1, "to": None, "offset": 3}],
                    }
                }
            }
        )
        assert svc.upsert_single_mapping("番", "new", season=2) is True
        entry = _read(tmp_path)["mappings"]["番"]
        assert entry["subject_id"] == "new"
        assert entry["segments"][0]["offset"] == 3

    def test_upsert_new_title_uses_simple_format(self, svc, tmp_path):
        """season<=1 的新标题仍写简单格式（保持 confirm 流程原行为）"""
        svc._write_config({"mappings": {}})
        assert svc.upsert_single_mapping("新番", "555", season=1) is True
        assert _read(tmp_path)["mappings"]["新番"] == "555"


# --------------------------------------------------------------------------
# 文件不存在时的默认配置
# --------------------------------------------------------------------------


def test_default_file_created_with_global_note(tmp_path, monkeypatch):
    """首次运行创建的默认文件应说明「映射是全局配置」"""
    monkeypatch.chdir(tmp_path)
    svc = MappingService()
    svc.load_custom_mappings()
    data = _read(tmp_path)
    assert "全局" in data["_note"]
    assert "假面骑士加布" in data["mappings"]
