"""集成验证：自定义映射 → 匹配 → 执行 的完整链路（issue #267 场景）

覆盖单测之间的接缝：
1. 匹配阶段 CustomMappingStep 的产物是否正确经 trace 传达到执行阶段
2. 集数分段映射换算出的目标集号是否真的被用于解析
3. 显式绑定找不到集数时是否真的不改选（而不是只在 step 单测里成立）
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.models.sync import CustomItem
from app.services.mapping_service import MappingService, set_mapping_service
from app.services.sync_service.match_trace import MatchTrace


def _make_service(tmp_path, monkeypatch, config):
    """构建真实 MappingService 并注入为全局单例"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bangumi_mapping.json").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8"
    )
    svc = MappingService()
    set_mapping_service(svc)
    return svc


ISSUE_267_CONFIG = {
    "mappings": {
        # 用户诉求：媒体库 JOJO S06 的 E03 起对应 Bangumi 条目 639938 的 E02
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
    },
    "rules": [],
}

# 单季番的默认形态：界面里用户不填「季度」，媒体源报 season=1。
# 这是最容易踩中「episode_resolve 抢先改选」的形态（见下方完整链路用例）。
SINGLE_SEASON_SEGMENT_CONFIG = {
    "mappings": {
        "JOJO的奇妙冒险": {
            "subject_id": "639938",
            "segments": [
                {
                    "season": 1,
                    "from": 1,
                    "to": None,
                    "subject_id": "639938",
                    "offset": 1,
                }
            ],
        }
    },
    "rules": [],
}


class TestIssue267EndToEnd:
    def test_matching_produces_target_episode(self, tmp_path, monkeypatch):
        """匹配阶段：S06E03 → 条目 639938 + 目标集号 2"""
        _make_service(tmp_path, monkeypatch, ISSUE_267_CONFIG)

        from app.services.matching.context import MatchContext
        from app.services.matching.steps.custom_mapping import CustomMappingStep

        for media_ep, expect_target in ((3, 2), (4, 3), (10, 9)):
            item = CustomItem(
                media_type="episode",
                title="JOJO的奇妙冒险",
                ori_title=None,
                season=6,
                episode=media_ep,
                release_date="2024-01-15",
                user_name="u",
                source="emby",
            )
            ctx = MatchContext(
                item=item, bgm=None, trace=MatchTrace(), service=MagicMock()
            )
            outcome = CustomMappingStep().execute(ctx)

            assert outcome.status == "hit"
            assert ctx.subject_id == "639938"
            assert ctx.mapping_target_episode == expect_target, (
                f"媒体 E{media_ep} 应换算为目标 E{expect_target}"
            )
            assert ctx.mapping_is_explicit is True
            assert ctx.is_season_matched_id is True

    def test_target_episode_reaches_execution_via_trace(self, tmp_path, monkeypatch):
        """接缝验证：trace 上的字段能被执行阶段读到并用于解析"""
        _make_service(tmp_path, monkeypatch, ISSUE_267_CONFIG)

        from app.services.matching.context import MatchContext
        from app.services.matching.steps.custom_mapping import CustomMappingStep
        from app.services.sync_service.context import ExecutionContext
        from app.services.sync_service.steps.episode_resolve import EpisodeResolveStep

        item = CustomItem(
            media_type="episode",
            title="JOJO的奇妙冒险",
            ori_title=None,
            season=6,
            episode=3,
            release_date="2024-01-15",
            user_name="u",
            source="emby",
        )
        trace = MatchTrace()
        match_ctx = MatchContext(item=item, bgm=None, trace=trace, service=MagicMock())
        CustomMappingStep().execute(match_ctx)

        # 模拟 _find_subject_id 的 trace 回填（与生产代码同一行）
        trace.mapping_is_explicit = match_ctx.mapping_is_explicit
        trace.mapping_target_episode = match_ctx.mapping_target_episode

        bgm = MagicMock()
        bgm.get_target_season_episode_id.return_value = (639938, 999888)
        service = MagicMock()
        service._resolve_season_episode.side_effect = (
            lambda b, i, sid, is_season, target_episode=None, mapping_is_explicit=False: (
                bgm.get_target_season_episode_id(
                    subject_id=sid,
                    target_season=i.season,
                    target_ep=target_episode,
                    allow_chain_fallback=not mapping_is_explicit,
                )
            )
        )

        exec_ctx = ExecutionContext(
            item=item,
            bgm=bgm,
            trace=trace,
            service=service,
            actual_source="emby",
            subject_id=match_ctx.subject_id,
            is_season_matched_id=match_ctx.is_season_matched_id,
            mapping_is_explicit=trace.mapping_is_explicit,
            mapping_target_episode=trace.mapping_target_episode,
        )
        outcome = EpisodeResolveStep().execute(exec_ctx)

        assert outcome.status == "hit"
        # 关键：解析用的是换算后的 2，不是媒体集号 3
        assert bgm.get_target_season_episode_id.call_args[1]["target_ep"] == 2
        assert outcome.outputs["episode_id"] == "999888"

    def test_explicit_binding_no_reselect_on_miss(self, tmp_path, monkeypatch):
        """端到端：显式绑定 + 集数不存在 → 不跨季改选，而是报错"""
        _make_service(tmp_path, monkeypatch, ISSUE_267_CONFIG)

        from app.services.sync_service.context import ExecutionContext
        from app.services.sync_service.steps.cross_season import CrossSeasonStep

        item = CustomItem(
            media_type="episode",
            title="JOJO的奇妙冒险",
            ori_title=None,
            season=6,
            episode=3,
            release_date="",
            user_name="u",
            source="emby",
        )
        bgm = MagicMock()
        bgm.find_episode_across_seasons.return_value = (111, "222")

        exec_ctx = ExecutionContext(
            item=item,
            bgm=bgm,
            trace=MatchTrace(),
            service=MagicMock(),
            actual_source="emby",
            subject_id="639938",
            is_season_matched_id=True,
            mapping_is_explicit=True,
            mapping_target_episode=2,
        )
        outcome = CrossSeasonStep().execute(exec_ctx, prev=None)

        assert outcome.status == "miss"
        assert outcome.is_terminal is True
        assert outcome.outputs["mapping_explicit_no_reselect"] is True
        # 这才是 issue #267 的关键：绝不改选到 111
        bgm.find_episode_across_seasons.assert_not_called()

    def test_full_pipeline_explicit_mapping_never_reselects(
        self, tmp_path, monkeypatch
    ):
        """**完整执行链**（episode_resolve → cross_season）下也不得改选条目

        这是上面那条 step 单测覆盖不到的接缝，也是 issue #267 真正复现的路径：
        ``CrossSeasonStep`` 的守卫只管得住自己那一步，可真正改选条目的是**先跑的**
        ``episode_resolve`` —— ``get_target_season_episode_id`` 在
        ``is_season_subject_id=True`` 时只把「条目内按集号定位」当**快路径**，
        找不到并不 return，而是继续回退到 ``_find_season_one_episode`` /
        ``_try_resolve_sequel_by_airdate`` / ``_find_multi_season_episode``，
        三者都会顺着续集链返回**另一个 subject**。

        因此本用例必须驱动真实的 ``SyncPipeline``，并让真实 ``BangumiApi``
        （仅打桩 I/O）走到那条回退分支，断言最终 subject 仍是映射指定的条目。
        """
        _make_service(tmp_path, monkeypatch, SINGLE_SEASON_SEGMENT_CONFIG)

        from app.services.sync_service.context import ExecutionContext
        from app.services.sync_service.pipeline import SyncPipeline
        from app.services.sync_service.steps.cross_season import CrossSeasonStep
        from app.services.sync_service.steps.episode_resolve import EpisodeResolveStep
        from app.utils.bangumi_api import BangumiApi

        MAPPED, OTHER = "639938", "777"

        # 真实 BangumiApi（跳过 httpx 构造），只打桩网络 I/O
        api = BangumiApi.__new__(BangumiApi)
        api.config_manager = MagicMock()

        # 映射条目只有 2 集；续集 777 有 12 集（含第 5 集）——即「会改选成功」的诱因
        def _find_by_sort(sid, ep):
            sid = int(sid)
            if sid == int(MAPPED):
                return {"id": 900000 + ep} if ep <= 2 else None
            if sid == int(OTHER):
                return {"id": 90000 + ep} if ep <= 12 else None
            return None

        api._find_episode_by_sort = _find_by_sort
        api.get_related_subjects = lambda sid: [
            {"id": int(OTHER), "type": 2, "relation": "续集", "name": "JOJO part2"}
        ]
        api.get_subject = lambda sid: {"id": int(sid), "type": 2, "platform": "TV"}
        # 三条回退分支全部指向「别的条目」，任一被走到都会暴露改选
        api._find_season_one_episode = lambda sid, ep, *a, **k: (int(OTHER), 90000 + ep)
        api._try_resolve_sequel_by_airdate = lambda *a, **k: (int(OTHER), 90005)
        api._find_multi_season_episode = lambda *a, **k: (int(OTHER), 90005)

        item = CustomItem(
            media_type="episode",
            title="JOJO的奇妙冒险",
            ori_title=None,
            season=1,
            episode=5,
            release_date="",
            user_name="u",
            source="emby",
        )

        trace = MatchTrace()
        trace.mapping_is_explicit = True
        trace.mapping_target_episode = 5

        from app.services.sync_service import SyncService

        svc = SyncService()
        svc._get_bangumi_api_for_user = lambda *a, **k: api

        exec_ctx = ExecutionContext(
            item=item,
            bgm=api,
            trace=trace,
            service=svc,
            actual_source="emby",
            subject_id=MAPPED,
            is_season_matched_id=True,
            mapping_is_explicit=True,
            mapping_target_episode=5,
        )

        stage, outcome = SyncPipeline([EpisodeResolveStep(), CrossSeasonStep()]).run(
            exec_ctx
        )

        # 核心断言：绝不被续集链改选成 777。
        # 正确结果是「终态失败」——条目内没有该集就报错，让用户去改映射，
        # 而不是悄悄把这一集标记到另一部番上。
        assert outcome is not None, "显式映射找不到集数时必须终止并报错"
        assert stage == "cross_season"
        assert outcome.status == "miss"
        assert outcome.is_terminal is True
        assert outcome.outputs["mapping_explicit_no_reselect"] is True

        # 任何产物里都不得出现被改选后的 OTHER
        assert OTHER not in json.dumps(exec_ctx.current_outputs, ensure_ascii=False)
        assert OTHER not in str(getattr(exec_ctx.trace, "final_subject_id", "") or "")

        # 报错信息要能指导用户去修映射，而不是笼统的「集数过多」
        err = str(exec_ctx.current_outputs.get("error") or "")
        assert "639938" in err
        assert "映射" in err

    def test_plain_mapping_still_falls_back(self, tmp_path, monkeypatch):
        """回归守护：普通映射（无 segments）仍走原有「填主条目往后找」语义"""
        _make_service(
            tmp_path,
            monkeypatch,
            {"mappings": {"某番": "43558"}, "rules": []},
        )

        from app.services.matching.context import MatchContext
        from app.services.matching.steps.custom_mapping import CustomMappingStep

        item = CustomItem(
            media_type="episode",
            title="某番",
            ori_title=None,
            season=2,
            episode=5,
            release_date="",
            user_name="u",
            source="emby",
        )
        ctx = MatchContext(item=item, bgm=None, trace=MatchTrace(), service=MagicMock())
        outcome = CustomMappingStep().execute(ctx)

        assert outcome.status == "hit"
        assert ctx.subject_id == "43558"
        # 非显式：保持沿续集链查找的老语义
        assert ctx.mapping_is_explicit is False
        assert ctx.is_season_matched_id is False
        assert ctx.mapping_target_episode is None


class TestMappingFileSafetyEndToEnd:
    @pytest.mark.asyncio
    async def test_corrupt_file_survives_api_write_attempt(self, tmp_path, monkeypatch):
        """端到端：配置文件损坏时写入被拒绝，原内容不被清空

        经真实 HTTP 路由验证，确保「服务端拒绝写入」不会被 API 层重新包装成
        成功响应（原实现丢弃返回值、无条件回 success）。
        """
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api import deps, mappings as mappings_api

        monkeypatch.chdir(tmp_path)
        path = tmp_path / "bangumi_mapping.json"
        path.write_text('{"mappings": {"a": "1"}, BROKEN', encoding="utf-8")
        original = path.read_text(encoding="utf-8")

        svc = MappingService()
        set_mapping_service(svc)
        svc.load_custom_mappings()

        app = FastAPI()
        app.include_router(mappings_api.router)

        async def mock_get_current_user(request=None, credentials=None):
            return {"username": "testuser", "id": 1}

        app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

        with patch.object(mappings_api, "mapping_service", svc):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post("/api/mappings", json={"mappings": {"a": "2"}})

        # 必须报错，不能谎报成功
        assert resp.status_code == 500
        assert "写入失败" in resp.json()["detail"]
        assert path.read_text(encoding="utf-8") == original
