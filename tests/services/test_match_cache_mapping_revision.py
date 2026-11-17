"""匹配缓存键必须跟随自定义映射文件的变化（PR #292 review）

``config_version`` 只覆盖 config.ini；``bangumi_mapping.json`` 是按文件 mtime
热读的，改映射不会抬升 ``config_version``。若缓存键里不含映射修订标识，同番
5 分钟内的后续集数会命中旧缓存、跳过整个 MatchPipeline（含 CustomMapping），
用户刚改的映射最多要等 TTL 300s 才生效。

这里锁住三件事：
1. 映射没动时键稳定（否则记忆化失效，收益归零）
2. 映射文件改动后键必须变化
3. 端到端：映射改动后旧缓存不得命中，管线必须重跑
"""

import json
import os

import pytest

from app.models.sync import CustomItem
from app.services.mapping_service import MappingService, set_mapping_service
from app.services.sync_service import SyncService


def _write_mapping(path, mappings: dict[str, str]) -> None:
    path.write_text(
        json.dumps({"mappings": mappings, "rules": []}, ensure_ascii=False),
        encoding="utf-8",
    )


def _bump_mtime(path, seconds: int = 10) -> None:
    """显式推进 mtime_ns

    同一秒内连写两次时，部分文件系统的时间戳可能不变；显式 utime 让「文件
    已改动」这一事实确定可见，避免用例变成偶发失败。
    """
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 10**9))


def _item(episode: int = 1) -> CustomItem:
    return CustomItem(
        title="甲",
        season=1,
        episode=episode,
        release_date="",
        user_name="u",
        media_type="episode",
    )


@pytest.fixture
def mapping_file(tmp_path, monkeypatch):
    """把 cwd 切到临时目录，让 _MAPPING_FILE_PATHS 命中临时映射文件"""
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "bangumi_mapping.json"
    _write_mapping(path, {"甲": "1"})
    set_mapping_service(MappingService())
    return path


def test_key_is_stable_while_mapping_unchanged(mapping_file):
    item = _item()
    assert SyncService._match_cache_key(item) == SyncService._match_cache_key(item)


def test_key_changes_when_mapping_file_changes(mapping_file):
    item = _item()
    before = SyncService._match_cache_key(item)

    _write_mapping(mapping_file, {"甲": "2"})
    _bump_mtime(mapping_file)

    assert SyncService._match_cache_key(item) != before


def test_wildcard_key_also_follows_mapping(mapping_file):
    """集无关键同样要带修订标识，否则它才是最容易被复用的那条"""
    item = _item(episode=5)
    before = SyncService._match_cache_key(item, wildcard_episode=True)

    _write_mapping(mapping_file, {"甲": "2"})
    _bump_mtime(mapping_file)

    assert SyncService._match_cache_key(item, wildcard_episode=True) != before


def test_pipeline_reruns_after_mapping_change(mapping_file, monkeypatch):
    """端到端：改映射后既不能命中精确键，也不能命中集无关键"""
    from app.services.matching.pipeline import MatchPipeline
    from app.services.matching.result import MatchResult

    calls: list[str] = []

    def fake_run(self, ctx):
        calls.append(ctx.item.title)
        return MatchResult(
            subject_id="1",
            bgm_se_id=None,
            bgm_ep_id=None,
            bgm_title="甲",
            is_season_matched_id=False,
            trace=ctx.trace,
        )

    monkeypatch.setattr(MatchPipeline, "run", fake_run)

    # 只用到 _match_cache 与 config_manager / mapping_service，跳过重型构造
    svc = object.__new__(SyncService)
    svc._match_cache = {}

    assert svc._find_subject_id(_item(1))[0] == "1"
    # 同一标题的下一集走集无关键，不应重跑管线（记忆化的核心收益）
    assert svc._find_subject_id(_item(2))[0] == "1"
    assert len(calls) == 1

    _write_mapping(mapping_file, {"甲": "2"})
    _bump_mtime(mapping_file)

    assert svc._find_subject_id(_item(3))[0] == "1"
    assert len(calls) == 2, "映射改动后仍命中旧缓存，刚改的映射要等 TTL 才生效"
