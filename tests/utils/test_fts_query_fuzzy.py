"""测试 ArchiveFTSQuery 模糊匹配：BK-tree 可开关 + OR/覆盖度兜底

验证点：
- BK-tree 开启时对编辑距离 ≤1 的 typo 有保证召回（含短查询 JW 打分救回）
- 关闭 BK-tree 后回退 trigram OR + 覆盖度预筛（无额外内存索引）
- _query_bktree 编辑距离保证 / _collect_candidates_coverage 覆盖度过滤
"""

from pathlib import Path

import pytest

from app.utils.bangumi_archive._fts_query import (
    ArchiveFTSQuery,
    _bktree_add,
    _collect_candidates_coverage,
    _query_bktree,
    archive_fts_query,
)


@pytest.fixture
def fuzzy_db(tmp_path):
    db = tmp_path / "fuzzy.db"
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
        (1, "Attack on Titan", "進撃の巨人", "", "2013-04-07"),
        (2, "Attack on Titan Season 2", "進撃の巨人 第2期", "", "2017-04-01"),
        (3, "Naruto", "ナルト", "", "2002-10-03"),
        (4, "One Piece", "ワンピース", "", "1999-10-20"),
        (5, "Spy x Family", "スパイファミリー", "", "2022-04-09"),
        (6, "Demon Slayer", "鬼滅の刃", "", "2019-04-06"),
        (9, "Lain", "", "", "1998-07-06"),
    ]
    conn.executemany("INSERT INTO subject VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def _setup(path: Path) -> ArchiveFTSQuery:
    q = archive_fts_query
    q._get_active_path = lambda: path
    q.invalidate()
    q._ensure_built()
    # 测试场景：显式开启 BK-tree 开关并同步构建索引，确保 BK 路径立即就绪
    q.use_bktree = True
    q._ensure_bktree(q._ensure_conn())
    return q


def test_bktree_enabled_recovers_typo(fuzzy_db):
    q = _setup(fuzzy_db)
    # 1 字符替换 typo（中段），归一化后仍共享大量 trigram
    res = q.find_subject_ids_fuzzy("Attak on Titan", threshold=80, limit=5)
    ids = [sid for sid, _ in res]
    assert 1 in ids, res


def test_bktree_recovers_short_query_typo_where_fallback_fails(fuzzy_db):
    q = _setup(fuzzy_db)
    # 短查询（≤5 字符）单字错位：BK 编辑距离保证召回 + JW 打分救回
    res_on = q.find_subject_ids_fuzzy("Lian", threshold=80, limit=5)
    ids_on = [sid for sid, _ in res_on]
    assert 9 in ids_on, res_on

    # 关闭 BK-tree：短查询错位共享不到 trigram，OR/覆盖度兜底也救不回
    q.set_bktree_enabled(False)
    res_off = q.find_subject_ids_fuzzy("Lian", threshold=80, limit=5)
    ids_off = [sid for sid, _ in res_off]
    assert 9 not in ids_off, res_off


def test_bktree_disabled_falls_back_to_substring(fuzzy_db):
    q = _setup(fuzzy_db)
    q.set_bktree_enabled(False)
    # 子串匹配：BK（编辑距离）不会命中，但 OR/覆盖度兜底能命中
    res = q.find_subject_ids_fuzzy("Titan", threshold=80, limit=5)
    ids = [sid for sid, _ in res]
    assert 1 in ids, res


def test_set_bktree_enabled_toggle(fuzzy_db):
    q = _setup(fuzzy_db)
    q.set_bktree_enabled(False)
    assert q.use_bktree is False
    q.set_bktree_enabled(True)
    assert q.use_bktree is True


def test_query_bktree_edit_distance_guarantee():
    # 手搓小树：验证「编辑距离 ≤1 必召回、>1 不召回」
    root = {"key": "attackontitan", "sids": {1}, "children": {}}
    _bktree_add(root, "naruto", 2)
    _bktree_add(root, "onepiece", 3)

    res = _query_bktree(root, "attakontitan", max_dist=1)
    assert 1 in res
    assert 2 not in res and 3 not in res

    # 完全不相关、编辑距离 >1 的查询不应召回任何节点
    assert _query_bktree(root, "zzzzzzzz", max_dist=1) == []


def test_collect_candidates_coverage(fuzzy_db):
    q = _setup(fuzzy_db)
    conn = q._ensure_conn()
    # 精确 key 应与自身共享全部 trigram（覆盖度过滤不丢）
    cov = _collect_candidates_coverage(conn, "attackontitan")
    assert 1 in cov
    # 无关短串不应召回（覆盖度不足）
    cov2 = _collect_candidates_coverage(conn, "zzzzzz")
    assert 1 not in cov2


def test_bktree_off_does_not_build_cache(fuzzy_db):
    """BK-tree 关闭时绝不构建缓存（源头保证）

    验证用户诉求：关闭（config use_bktree=false 或 set_bktree_enabled(False)）
    时，即便调用 _maybe_get_bktree / find_subject_ids_fuzzy，也**不**启动后台
    构建线程、不置 building 标志、不持有任何 BK-tree 缓存，直接走 OR+覆盖度兜底。
    """
    q = archive_fts_query
    q._get_active_path = lambda: fuzzy_db
    q.invalidate()
    q._ensure_built()
    # 复位为默认关闭（use_bktree=None → 读配置，默认 false）；
    # invalidate 不重置运行时覆盖值，需显式复位以模拟配置关
    q.use_bktree = None
    assert q.use_bktree is None
    assert q._resolve_use_bktree() is False

    # 调用 _maybe_get_bktree：应直接返回 None，不触发任何构建
    assert q._maybe_get_bktree() is None
    assert q._bk_tree is None, "关闭时不应持有 BK-tree 缓存"
    assert q._bk_building is False, "关闭时不应置构建中标志（未启动线程）"

    # fuzzy 查询在关闭状态下也不应触发构建
    res = q.find_subject_ids_fuzzy("Attack on Titan", threshold=80, limit=5)
    assert isinstance(res, list)
    assert q._bk_tree is None, "关闭态 fuzzy 查询后不应出现 BK-tree 缓存"
    assert q._bk_building is False

    # set_bktree_enabled(False) 在曾开启后也应释放缓存
    q.set_bktree_enabled(True)
    q._ensure_bktree(q._ensure_conn())
    assert q._bk_tree is not None
    q.set_bktree_enabled(False)
    assert q._bk_tree is None, "关闭应释放已缓存的 BK-tree"
    assert q._bk_building is False


def test_bktree_async_build_does_not_block_first_query(fuzzy_db):
    """后台异步构建不应阻塞首次查询：首次走兜底返回，构建完成后命中 BK 路径

    验证 _maybe_get_bktree 的非阻塞语义：首次 fuzzy 查询在树就绪前立即返回
    （OR+覆盖度兜底），后台 daemon 线程构建完成后下一次查询即命中 BK-tree
    （编辑距离保证召回单字符 typo）。
    """
    import time

    q = _setup(fuzzy_db)
    # 模拟「首次查询前索引未就绪」：丢弃已同步建好的树，强制走异步路径
    q._bk_tree = None
    q._bk_built_path = None
    # 首次查询必须非阻塞（BK 在后台线程构建），本次走 OR+覆盖度兜底返回
    res = q.find_subject_ids_fuzzy("Attack on Titan", threshold=80, limit=5)
    assert isinstance(res, list)
    # 轮询等待后台构建完成（小库毫秒级），然后 typo 查询应命中 BK 路径
    for _ in range(100):
        if q._bk_tree is not None:
            break
        time.sleep(0.05)
    assert q._bk_tree is not None, "后台 BK-tree 构建超时未完成"
    res2 = q.find_subject_ids_fuzzy("Attak on Titan", threshold=80, limit=5)
    ids2 = [sid for sid, _ in res2]
    assert 1 in ids2, res2


def _make_db_empty_shell(path: Path) -> None:
    """构造「空壳 FTS」：subject 有真实数据，但 subject_fts 行数正常、内容全空。

    复刻线上坏状态（FTS 建在空数据上，之后 subject 被填充却未重建）。
    旧逻辑只查 COUNT(*) > 0，会误判为空壳 FTS 就绪 → 查询静默失效且永不重建。
    """
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE subject ("
        "id INTEGER PRIMARY KEY, name TEXT, name_cn TEXT, infobox TEXT, date TEXT)"
    )
    rows = [
        (1, "Attack on Titan", "進撃の巨人", "", "2013"),
        (2, "Attack on Titan Season 2", "進撃の巨人 第2期", "", "2017"),
        (3, "Naruto", "ナルト", "", "2002"),
        (4, "One Piece", "ワンピース", "", "1999"),
        (5, "Spy x Family", "スパイファミリー", "", "2022"),
    ]
    conn.executemany("INSERT INTO subject VALUES (?,?,?,?,?)", rows)
    conn.execute(
        "CREATE VIRTUAL TABLE subject_fts USING fts5("
        "name, name_cn, aliases, content='', tokenize='trigram')"
    )
    ids = [r[0] for r in conn.execute("SELECT id FROM subject")]
    # 关键：插入全空内容（复刻空壳），而非从 subject 抽取
    conn.executemany(
        "INSERT INTO subject_fts(rowid, name, name_cn, aliases) VALUES (?,?,?,?)",
        [(i, "", "", "") for i in ids],
    )
    conn.execute("INSERT INTO subject_fts(subject_fts) VALUES('optimize')")
    conn.commit()
    conn.close()


def test_empty_shell_fts_reported_not_ready(tmp_path):
    """空壳 FTS 必须被 _check_fts_table_exists 判为未就绪（内容探针命中 0）。

    这是修复的核心：旧实现只看 COUNT(*)>0，空壳会被误判就绪。
    """
    import sqlite3

    db = tmp_path / "shell.db"
    _make_db_empty_shell(db)
    conn = sqlite3.connect(str(db))
    try:
        assert ArchiveFTSQuery._check_fts_table_exists(conn) is False
    finally:
        conn.close()


def test_empty_shell_fts_self_heals_on_query(tmp_path):
    """查询守卫改用 _ensure_built：空壳 FTS 在首次查询时同步重建并命中。

    修复前：查询只读缓存 _fts_ready（被空壳误置 True），返回 [] 且永不重建。
    修复后：_ensure_built 检测到未就绪 → 从 subject 重建 FTS → 命中。
    """
    db = tmp_path / "shell.db"
    _make_db_empty_shell(db)
    q = archive_fts_query
    q._get_active_path = lambda: db
    q.invalidate()
    try:
        # 首次查询触发重建并命中（"Attack on Titan" → id=1）
        ids = q.find_subject_ids_by_title("Attack on Titan")
        assert 1 in ids
        # 二次查询走缓存就绪路径，仍正常
        assert 1 in q.find_subject_ids_by_title("Attack on Titan")
    finally:
        q.invalidate()


def _make_db_year_disambig(path: Path) -> None:
    """构造同名多年版测试库：3 个同名「銀魂」subject 首播年不同"""
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE subject ("
        "id INTEGER PRIMARY KEY, name TEXT, name_cn TEXT, infobox TEXT, date TEXT)"
    )
    # id 1=2003 版（最早）/ id 2=2006 TV 版 / id 3=2017 真人版
    rows = [
        (1, "銀魂", "银魂", "", "2003-10-04"),
        (2, "銀魂", "银魂", "", "2006-04-04"),
        (3, "銀魂", "银魂", "", "2017-07-14"),
    ]
    conn.executemany("INSERT INTO subject VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_year_disambiguation_orders_matching_year_first(tmp_path):
    """year 消歧：同名多年版 subject，传入 year 时相符者置顶（不丢召回）"""
    db = tmp_path / "year.db"
    _make_db_year_disambig(db)
    q = archive_fts_query
    q._get_active_path = lambda: db
    q.invalidate()
    try:
        q._ensure_built()
        # 先触发一次查询让 key_index 懒构建（短查询「銀魂」2 字符依赖 key_index）
        q.find_subject_ids_by_title("銀魂")
        # year=2006：2006 版应置顶，其余同名候选仍保留
        ids = q.find_subject_ids_by_title("銀魂", year=2006)
        assert ids, f"应命中同名候选，实际: {ids}"
        assert ids[0] == 2, f"2006 版应置顶，实际顺序: {ids}"
        assert set(ids) == {1, 2, 3}, "不应丢失同名候选"
        # year=2003：2003 版应置顶
        ids_2003 = q.find_subject_ids_by_title("銀魂", year=2003)
        assert ids_2003[0] == 1
        # year=2017：2017 版应置顶
        ids_2017 = q.find_subject_ids_by_title("銀魂", year=2017)
        assert ids_2017[0] == 3
    finally:
        q.invalidate()


def test_key_index_empty_table_triggers_rebuild(tmp_path):
    """key_index 损坏（表存在但为空）应触发重建，而非静默返回空

    修复前：_ensure_key_index 仅查表名存在，空表返回 True 导致
    _collect_candidates_key_index 返回 []（非 None）不回退 FTS。
    修复后：COUNT 探针检测空表，触发重建。
    """
    db = tmp_path / "keyidx.db"
    _make_db(db)
    q = archive_fts_query
    q._get_active_path = lambda: db
    q.invalidate()
    try:
        q._ensure_built()
        from app.utils.bangumi_archive._fts_query import _KEY_INDEX_TABLE

        # 先触发一次查询让 key_index 懒构建（_ensure_built 只构建 FTS5，不构建 key_index）
        q.find_subject_ids_by_title("Attack on Titan")
        conn = q._ensure_conn()
        assert conn is not None
        # 确认 key_index 已构建
        cnt = conn.execute(f"SELECT COUNT(*) FROM {_KEY_INDEX_TABLE}").fetchone()
        assert cnt and cnt[0] > 0, "key_index 应已构建且非空"
        # 手动清空 key_index 模拟「构建中断残留空表」
        conn.execute("PRAGMA query_only=OFF")
        conn.execute(f"DELETE FROM {_KEY_INDEX_TABLE}")
        conn.commit()
        conn.execute("PRAGMA query_only=ON")
        # 损坏后查询应触发重建并命中（COUNT 探针检测空表）
        ids = q.find_subject_ids_by_title("Attack on Titan")
        assert 1 in ids, "空表应触发重建并命中"
    finally:
        q.invalidate()
