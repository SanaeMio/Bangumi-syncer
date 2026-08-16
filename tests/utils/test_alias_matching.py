"""测试 Archive 别名匹配（场景 U）

背景：真实库 bangumi_archive_a.db 的 subject.infobox 全部不含 别名/alias/中文名/
罗马音/假名 键（已核查：56539 条 infobox 0 个别名键），故大基准里的 R6「别名可达」
在真实数据上是死场景（aliases 池为空 → 0 用例）。本测试用合成库注入别名，验证
「查询别名 → 命中本体」的代码路径确实可达（_match_exact 读 infobox 别名 +
_collect_candidates_fts 用 subject_fts.aliases MATCH 召回候选）。

验证点：
- infobox 别名（别名 key）查询命中本体
- 中文名 key 亦视作别名（_ALIAS_KEYS 含 中文名）查询命中
- 多别名各自可达
- 无别名时原名/中文名路径仍正常（sanity）
"""

from pathlib import Path

import pytest

from app.utils.bangumi_archive._fts_query import archive_fts_query


@pytest.fixture
def alias_db(tmp_path):
    db = tmp_path / "alias.db"
    _make_db(db)
    yield db
    archive_fts_query.invalidate()


def _make_db(path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE subject ("
        "id INTEGER PRIMARY KEY, name TEXT, name_cn TEXT, infobox TEXT, date TEXT)"
    )
    rows = [
        # 1: 英文名本体 + 两个别名（含与本体差异大的别名）
        (
            1,
            "Attack on Titan",
            "進撃の巨人",
            "{{Infobox|别名=* AoT\n* 進撃外傳}}",
            None,
        ),
        # 2: 本体无 name_cn，靠 中文名 key（亦在 _ALIAS_KEYS）提供 CN 别名
        (
            2,
            "Naruto",
            "",
            "{{Infobox|中文名=火影}}",
            None,
        ),
        # 3: 罗马音别名
        (
            3,
            "Neon Genesis Evangelion",
            "新世紀エヴァンゲリオン",
            "{{Infobox|罗马音=Shinseiki Evangerion}}",
            None,
        ),
        # 4: 无别名（sanity：原名/中文名路径仍可达）
        (4, "One Piece", "ワンピース", "", None),
    ]
    conn.executemany("INSERT INTO subject VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def _setup(path: Path):
    q = archive_fts_query
    q._get_active_path = lambda: path
    q.invalidate()
    q._ensure_built()
    q.use_bktree = False  # 别名是精确路径，关 BK 避免后台构建
    return q


def test_alias_key_reachable(alias_db):
    q = _setup(alias_db)
    # 与本体差异大的别名也应命中（仅别名列匹配，非 name 子串）
    res = q.find_subject_ids_by_title("AoT")
    assert 1 in res, res
    res2 = q.find_subject_ids_by_title("進撃外傳")
    assert 1 in res2, res2


def test_cn_name_key_treated_as_alias(alias_db):
    q = _setup(alias_db)
    # 中文名 key 在 _ALIAS_KEYS 中，应作为别名可达
    res = q.find_subject_ids_by_title("火影")
    assert 2 in res, res


def test_romaji_alias_reachable(alias_db):
    q = _setup(alias_db)
    res = q.find_subject_ids_by_title("Shinseiki Evangerion")
    assert 3 in res, res


def test_multiple_aliases_each_reachable(alias_db):
    q = _setup(alias_db)
    # 同一条目多个别名各自可召回
    assert 1 in q.find_subject_ids_by_title("AoT")
    assert 1 in q.find_subject_ids_by_title("進撃外傳")


def test_no_alias_name_path_still_works(alias_db):
    q = _setup(alias_db)
    assert 4 in q.find_subject_ids_by_title("One Piece")
    assert 4 in q.find_subject_ids_by_title("ワンピース")
    # 无别名条目用别名查询不可达（符合预期，不误召回）
    assert 4 not in q.find_subject_ids_by_title("海贼王")
