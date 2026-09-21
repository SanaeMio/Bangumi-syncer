"""reject 负样本黑名单端到端测试

验证：
1. 拒绝待确认候选时，其候选 subject_id 被记入该标题黑名单
2. 拒绝不存在的记录安全返回 False
3. _find_subject_id 命中黑名单 subject 时降级为漏标（custom_mapping 不受影响）
4. _collect_candidates_from_trace 能按黑名单剔除候选
"""

from types import SimpleNamespace

from app.core.database import database_manager
from app.services.matching.result import MatchResult
from app.services.sync_service import sync_service
from app.services.sync_service.match_trace import MatchTrace


class TestRejectWritesBlacklist:
    TITLE = "拒绝测试番剧"

    def teardown_method(self):
        database_manager.clear_title_blacklist(self.TITLE)

    def test_reject_records_blacklist(self):
        database_manager.clear_title_blacklist(self.TITLE)
        cid = database_manager.log_pending_candidate(
            request_title=self.TITLE,
            request_season=1,
            user_name="u1",
            source="plex",
            candidates=[
                {"subject_id": "555", "name": "A", "score": 0.9},
                {"subject_id": "666", "name": "B", "score": 0.8},
            ],
            trace={"steps": []},
        )
        assert cid

        ok, msg = sync_service.reject_pending_candidate(cid)
        assert ok is True
        assert database_manager.get_title_blacklist(self.TITLE) == {"555", "666"}

    def test_reject_missing_record(self):
        ok, msg = sync_service.reject_pending_candidate(999999)
        assert ok is False


class TestFindSubjectExcludesBlacklist:
    TITLE = "黑名单命中测试"

    def teardown_method(self):
        database_manager.clear_title_blacklist(self.TITLE)

    def test_blacklisted_result_downgraded(self, monkeypatch):
        database_manager.clear_title_blacklist(self.TITLE)
        database_manager.bulk_add_title_blacklist(self.TITLE, ["999"])

        item = SimpleNamespace(title=self.TITLE)
        trace = MatchTrace(
            request_title=self.TITLE,
            request_ori_title="",
            request_season=1,
            request_episode=1,
            request_media_type="episode",
            request_release_date="",
            request_user_name="u",
            request_platform_hint="plex",
        )

        class FakePipeline:
            def __init__(self, steps):
                self._steps = steps

            def run(self, ctx):
                return MatchResult(
                    subject_id="999",
                    bgm_se_id=None,
                    bgm_ep_id=None,
                    bgm_title="X",
                    is_season_matched_id=True,
                    trace=ctx.trace,
                    failure_detail="",
                )

        monkeypatch.setattr(
            "app.services.matching.pipeline.MatchPipeline", FakePipeline
        )
        sid, _, detail = sync_service._find_subject_id(item, trace=trace)
        assert sid is None
        assert "排除" in detail


class TestCollectExcludesBlacklist:
    def test_excludes_blocked_and_dedups(self):
        cand_a = SimpleNamespace(subject_id="1", to_dict=lambda: {"subject_id": "1"})
        cand_b = SimpleNamespace(subject_id="2", to_dict=lambda: {"subject_id": "2"})
        cand_c = SimpleNamespace(subject_id="3", to_dict=lambda: {"subject_id": "3"})
        trace = SimpleNamespace(
            steps=[
                SimpleNamespace(candidates=[cand_a, cand_b]),
                SimpleNamespace(candidates=[cand_c, cand_a]),  # a 重复
            ]
        )
        result = sync_service._collect_candidates_from_trace(
            trace, exclude_subject_ids={"2"}
        )
        ids = [c["subject_id"] for c in result]
        assert ids == ["1", "3"]  # 2 被剔除，1 去重
