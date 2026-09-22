"""屏蔽关键词（统一黑名单）端到端测试

背景：历史上存在两套屏蔽机制 —— INI 的 ``[sync] blocked_keywords``（按标题
关键词，匹配前生效）与 DB 的 ``title_blacklist``（按 subject_id，匹配后否决）。
现已合并为**单一 DB 表 + 标题关键词判定 + 匹配前生效**（方案 1）。

本文件验证合并后的行为：
1. 拒绝待确认候选时，候选**标题**被记入屏蔽关键词（不再记 subject_id）
2. 拒绝不存在的记录安全返回 False
3. 标题命中屏蔽词 → `_is_title_blocked` 为 True（匹配前拦截）
4. **自定义映射优先**：命中映射时即使标题含屏蔽词也放行
5. `_find_subject_id` 不再做事后 veto（原实现对 trace 有一处不一致）
"""

from types import SimpleNamespace
from unittest.mock import patch

from app.core.database import database_manager
from app.models.sync import CustomItem
from app.services.sync_service import SyncService, sync_service
from app.services.sync_service.match_trace import MatchTrace

# 测试用关键词（避免与真实数据冲突，统一前缀）
# 这些词会写进真实 DB，故用明显不可能出现在番剧标题里的串
DISTINCT_KW = "zz-屏蔽测试-拒绝记录-7788"


def _clear():
    """清理本测试写入的关键词。

    所有测试词统一带 ``ZZ`` 标记（或用中文标记），确保清理能覆盖全部 ——
    早期版本只匹配中文标记，漏掉了 ``Candidate Alpha`` 这类英文词，
    导致后续「迁移要求空表」的用例被污染。
    """
    for item in database_manager.list_blocked_keywords():
        kw = item.get("keyword") or ""
        low = kw.lower()
        if (
            kw.startswith("ZZ-")
            or "zz-" in low
            or "屏蔽测试" in kw
            or "拒绝测试" in kw
            or kw.startswith("Candidate ")
        ):
            database_manager.remove_blocked_keyword(kw)


class TestRejectWritesBlockedKeyword:
    TITLE = "拒绝测试番剧-2255"

    def teardown_method(self):
        _clear()

    def test_reject_records_candidate_titles(self):
        """拒绝候选 → 候选标题与请求标题都进屏蔽关键词"""
        _clear()
        cid = database_manager.log_pending_candidate(
            request_title=self.TITLE,
            request_season=1,
            user_name="u1",
            source="plex",
            candidates=[
                {"subject_id": "555", "name": "ZZ-Cand-Alpha", "score": 0.9},
                {"subject_id": "666", "name": "ZZ-Cand-Beta", "score": 0.8},
            ],
            trace={"steps": []},
        )
        assert cid

        ok, _msg = sync_service.reject_pending_candidate(cid)
        assert ok is True

        keywords = {i["keyword"] for i in database_manager.list_blocked_keywords()}
        # 请求标题进列表（保证同一标题下次直接被拦）
        assert self.TITLE in keywords
        # 候选自身标题也进列表（覆盖候选条目的正式名）
        assert "ZZ-Cand-Alpha" in keywords
        assert "ZZ-Cand-Beta" in keywords

        # 来源标注为 reject，供 UI 区分"系统记的"与"我填的"
        rec = next(
            i
            for i in database_manager.list_blocked_keywords()
            if i["keyword"] == self.TITLE
        )
        assert rec["source"] == "reject"

    def test_reject_missing_record(self):
        ok, _msg = sync_service.reject_pending_candidate(999999)
        assert ok is False


class TestIsTitleBlocked:
    """标题命中判定（匹配前生效）"""

    def teardown_method(self):
        _clear()

    def test_blocks_when_keyword_matches(self):
        database_manager.add_blocked_keyword(DISTINCT_KW)
        try:
            assert sync_service._is_title_blocked(f"某番剧 {DISTINCT_KW}") is True
        finally:
            database_manager.remove_blocked_keyword(DISTINCT_KW)

    def test_matches_original_title_too(self):
        database_manager.add_blocked_keyword(DISTINCT_KW)
        try:
            assert (
                sync_service._is_title_blocked("普通标题", f"原文 {DISTINCT_KW}")
                is True
            )
        finally:
            database_manager.remove_blocked_keyword(DISTINCT_KW)

    def test_case_insensitive(self):
        database_manager.add_blocked_keyword("ZZ-CaseTest-9911")
        try:
            assert sync_service._is_title_blocked("zz-casetest-9911") is True
        finally:
            database_manager.remove_blocked_keyword("ZZ-CaseTest-9911")

    def test_not_blocked_when_no_keyword(self):
        _clear()
        assert sync_service._is_title_blocked("任意标题") is False

    def test_custom_mapping_takes_priority(self, monkeypatch):
        """自定义映射优先：命中映射时即使标题含屏蔽词也放行"""
        database_manager.add_blocked_keyword(DISTINCT_KW)
        monkeypatch.setattr(
            "app.services.sync_service.mapping_service.find_mapping",
            lambda title, ori_title="", season=1: ("12345", "exact", "映射命中"),
        )
        try:
            assert sync_service._is_title_blocked(f"标题 {DISTINCT_KW}") is False
        finally:
            database_manager.remove_blocked_keyword(DISTINCT_KW)

    def test_mapping_lookup_failure_falls_back_to_blocking(self, monkeypatch):
        """映射查询异常时不应吞掉屏蔽判定（回退为按关键词判定）"""
        database_manager.add_blocked_keyword(DISTINCT_KW)

        def _boom(*a, **k):
            raise RuntimeError("mapping service down")

        monkeypatch.setattr(
            "app.services.sync_service.mapping_service.find_mapping", _boom
        )
        try:
            assert sync_service._is_title_blocked(f"标题 {DISTINCT_KW}") is True
        finally:
            database_manager.remove_blocked_keyword(DISTINCT_KW)


class TestSeasonAwareMappingPriority:
    """回归：`_is_title_blocked` 的「映射优先」必须带 season

    原实现把 season 写死为 1 调 `find_mapping`，而高级格式映射
    （``{"subject_id": "...", "season": N}``）只在 season 相符时命中。
    于是「第 1 季有映射 + 第 2 季请求」会被误判为命中映射而放行，
    绕过屏蔽词；反之映射匹配管线（CustomMappingStep）用的是真实 season，
    两处口径不一致。
    """

    def teardown_method(self):
        _clear()

    def test_other_season_mapping_does_not_whitelist(self):
        """第 2 季请求不应被第 1 季的映射放行"""
        database_manager.add_blocked_keyword(DISTINCT_KW)
        svc = SyncService()
        try:
            with (
                patch.object(svc, "_get_blocked_keyword", return_value=DISTINCT_KW),
                patch(
                    "app.services.sync_service.mapping_service.find_mapping",
                    side_effect=lambda title, ori_title="", season=1: (
                        ("12345", "season", "映射命中") if season == 1 else ("", "", "")
                    ),
                ),
            ):
                assert svc._is_title_blocked("某番剧", f"原文 {DISTINCT_KW}", 2) is True
        finally:
            database_manager.remove_blocked_keyword(DISTINCT_KW)

    def test_same_season_mapping_whitelists(self):
        """season 相符时映射仍然优先放行"""
        database_manager.add_blocked_keyword(DISTINCT_KW)
        svc = SyncService()
        try:
            with (
                patch.object(svc, "_get_blocked_keyword", return_value=DISTINCT_KW),
                patch(
                    "app.services.sync_service.mapping_service.find_mapping",
                    side_effect=lambda title, ori_title="", season=1: (
                        ("12345", "season", "映射命中") if season == 1 else ("", "", "")
                    ),
                ),
            ):
                assert (
                    svc._is_title_blocked("某番剧", f"原文 {DISTINCT_KW}", 1) is False
                )
        finally:
            database_manager.remove_blocked_keyword(DISTINCT_KW)

    def test_call_sites_pass_item_season(self):
        """两个调用处都必须把 item.season 传下去（防止再次写死）"""
        svc = SyncService()
        calls: list[int] = []

        def _spy(title, ori_title=None, season=None):
            calls.append(season)
            return False

        item = CustomItem(
            title="测试番剧",
            season=3,
            episode=1,
            release_date="",
            user_name="u",
        )
        with patch.object(svc, "_check_user_permission", return_value=(True, "")):
            with patch.object(svc, "_is_title_blocked", side_effect=_spy):
                assert svc._normalize_custom_item_params(item) is None
        assert calls == [3], "_normalize_custom_item_params 应传 item.season"

        calls.clear()
        movie = CustomItem(
            media_type="movie",
            title="测试剧场版",
            season=1,
            episode=1,
            release_date="",
            user_name="u",
        )
        with (
            patch("app.services.sync_service.config_manager") as mock_config,
            patch("app.services.sync_service.notification_service"),
            patch.object(svc, "_check_user_permission", return_value=(True, "")),
            patch.object(svc, "_is_title_blocked", side_effect=_spy),
            # 断言点在 _is_title_blocked 的调用参数上，后续匹配短路即可
            patch.object(svc, "_find_matching_subject", return_value=(None,) * 4),
        ):
            mock_config.get.return_value = True
            svc.sync_movie_watching(movie, source="custom")
        assert calls == [1], "sync_movie_watching 应传 item.season"


class TestKeywordRepository:
    """仓储层行为（幂等、大小写归一、来源保留）"""

    def teardown_method(self):
        _clear()

    def test_add_is_idempotent_case_insensitive(self):
        assert database_manager.add_blocked_keyword("ZZ-Idem-4433") is True
        assert database_manager.add_blocked_keyword("zz-idem-4433") is False
        database_manager.remove_blocked_keyword("ZZ-Idem-4433")

    def test_bulk_add_skips_existing(self):
        database_manager.add_blocked_keyword("ZZ-Bulk-5501")
        added = database_manager.bulk_add_blocked_keywords(
            ["ZZ-Bulk-5501", "ZZ-Bulk-5502"]
        )
        assert added == 1
        _clear()

    def test_remove_returns_false_for_missing(self):
        assert database_manager.remove_blocked_keyword("ZZ-不存在-0000") is False

    def test_empty_keyword_rejected(self):
        assert database_manager.add_blocked_keyword("") is False
        assert database_manager.add_blocked_keyword("   ") is False

    def test_api_replace_preserves_reject_source(self, monkeypatch):
        """整体保存（配置页）不应删掉 reject 自动记录的词

        直接验证 service 层语义：REPLACE 只清理 manual，保留 reject。
        这里通过 facade 手工复现标签页保存的行为，避免依赖 HTTP 栈。
        """
        _clear()
        database_manager.add_blocked_keyword("ZZ-Manual-6601", source="manual")
        database_manager.add_blocked_keyword("ZZ-Reject-6602", source="reject")

        # 模拟 PUT /blocked-keywords 的实现：只删 manual，再批量写入手填
        for item in database_manager.list_blocked_keywords():
            if item.get("source") != "reject":
                database_manager.remove_blocked_keyword(item["keyword"])
        database_manager.bulk_add_blocked_keywords(["ZZ-Manual-6603"], source="manual")

        remaining = {i["keyword"] for i in database_manager.list_blocked_keywords()}
        assert "ZZ-Reject-6602" in remaining, "reject 记录应保留"
        assert "ZZ-Manual-6603" in remaining, "新提交的手填词应写入"
        assert "ZZ-Manual-6601" not in remaining, "旧手填词应被覆盖"
        _clear()


class TestMigrationFromConfig:
    """历史 [sync] blocked_keywords 迁移"""

    def teardown_method(self):
        _clear()

    def test_migrates_when_table_empty(self):
        _clear()
        assert database_manager.blocked_keyword_count() == 0, (
            "迁移测试要求空表；失败说明 _clear() 未覆盖残留关键词"
        )
        n = database_manager.migrate_blocked_keywords_from_config(
            "ZZ-Mig-A,ZZ-Mig-B，ZZ-Mig-C"
        )
        assert n == 3
        keywords = {i["keyword"] for i in database_manager.list_blocked_keywords()}
        assert {"ZZ-Mig-A", "ZZ-Mig-B", "ZZ-Mig-C"} <= keywords

    def test_skips_when_table_not_empty(self):
        """表非空时不迁移 —— 否则用户删掉的关键词会被加回来"""
        _clear()
        database_manager.add_blocked_keyword("ZZ-Mig-Existing")
        n = database_manager.migrate_blocked_keywords_from_config("ZZ-Mig-ShouldNotAdd")
        assert n == 0
        keywords = {i["keyword"] for i in database_manager.list_blocked_keywords()}
        assert "ZZ-Mig-ShouldNotAdd" not in keywords
        _clear()

    def test_handles_empty_input(self):
        _clear()
        assert database_manager.migrate_blocked_keywords_from_config("") == 0
        assert database_manager.migrate_blocked_keywords_from_config("   ") == 0


class TestCollectCandidates:
    """`_collect_candidates_from_trace`：去重 + 按分数降序

    注：`exclude_subject_ids` 参数已随黑名单统一而移除（排除改为比较候选标题，
    见 `_sediment_pending_candidate` / `_maybe_notify_match_ambiguous`）。
    """

    def test_dedups_and_sorts_by_score(self):
        cand_a = SimpleNamespace(
            subject_id="1", to_dict=lambda: {"subject_id": "1", "score": 0.5}
        )
        cand_b = SimpleNamespace(
            subject_id="2", to_dict=lambda: {"subject_id": "2", "score": 0.9}
        )
        cand_c = SimpleNamespace(
            subject_id="3", to_dict=lambda: {"subject_id": "3", "score": 0.7}
        )
        trace = SimpleNamespace(
            steps=[
                SimpleNamespace(candidates=[cand_a, cand_b]),
                SimpleNamespace(candidates=[cand_c, cand_a]),  # a 重复
            ]
        )
        result = sync_service._collect_candidates_from_trace(trace)
        ids = [c["subject_id"] for c in result]
        assert ids == ["2", "3", "1"]  # 按 score 降序，1 只出现一次

    def test_skips_empty_subject_id(self):
        empty = SimpleNamespace(subject_id="", to_dict=lambda: {"subject_id": ""})
        ok = SimpleNamespace(
            subject_id="9", to_dict=lambda: {"subject_id": "9", "score": 1.0}
        )
        trace = SimpleNamespace(steps=[SimpleNamespace(candidates=[empty, ok])])
        result = sync_service._collect_candidates_from_trace(trace)
        assert [c["subject_id"] for c in result] == ["9"]


class TestVetoRemoved:
    """回归守护：`_find_subject_id` 不再做"事后按 subject_id 否决"

    合并后屏蔽在匹配前完成，匹配成功后不应再被清空 —— 否则会出现
    「DB 无 subject_id 但 trace 有命中」的不一致（原 veto 的缺陷）。
    """

    def test_matched_result_not_vetoed(self, monkeypatch):
        from app.services.matching.result import MatchResult

        title = "ZZ-Veto-回归-7700"
        database_manager.add_blocked_keyword(title)
        try:
            item = SimpleNamespace(title="完全不同的标题", ori_title="")
            trace = MatchTrace(
                request_title="完全不同的标题",
                request_season=1,
                request_episode=1,
                request_user_name="u",
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
            # 标题不含屏蔽词 → 即便命中的 subject 曾"被拒绝"也不应被否决
            assert sid == "999"
            assert detail == ""
        finally:
            database_manager.remove_blocked_keyword(title)
