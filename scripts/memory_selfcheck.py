"""记忆功能自检脚本（只读，不触发 LLM 调用）。

用于手工端到端验收 Phase 2（记忆三件套）——输出验收所需的全部事实：
表结构健康度、最近记忆条目、消费标记一致率、llm_usage 归属统计、
FTS 关键词命中演示。全部只读，可反复执行。

用法：
    uv run python scripts/memory_selfcheck.py                     # 基础检查
    uv run python scripts/memory_selfcheck.py --keywords 芙莉莲   # 附带 FTS 演示
    uv run python scripts/memory_selfcheck.py --titles 芙莉莲 葬送的芙莉莲  # 同剧关联演示
    uv run python scripts/memory_selfcheck.py --db /path/to.db    # 指定库路径

退出码：0 = 检查通过；1 = 表结构缺失等问题。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB_DEFAULT = Path("data/sync_records.db")

WARN = "\033[33m"
OK = "\033[32m"
END = "\033[0m"


def _check(db: sqlite3.Connection) -> bool:
    ok = True

    def fail(msg: str) -> None:
        nonlocal ok
        ok = False
        print(f"  {WARN}[FAIL]{END} {msg}")

    print("== 1. 表结构与索引 ==")
    tables = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }
    for t in (
        "agent_working_memory",
        "agent_working_memory_archive",
        "agent_memory_fts",
        "sync_records_consumed",
    ):
        if t in tables:
            print(f"  {OK}[OK]{END} 表 {t} 存在")
        else:
            fail(f"表 {t} 缺失")
    if "idx_sync_records_consumed_run_id" not in {
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }:
        fail("索引 idx_sync_records_consumed_run_id 缺失")
    cols = {r[1] for r in db.execute("PRAGMA table_info(sync_records)")}
    if "consumed_run_id" in cols:
        fail("sync_records.consumed_run_id 旧列应已删除（迁移为关联表）")

    print("== 2. FTS 分词器 ==")
    try:
        db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS _selfcheck_fts USING fts5(x, tokenize='trigram')"
        )
        db.execute("DROP TABLE _selfcheck_fts")
        print("  [OK] trigram 可用（中文子串检索正常）")
    except sqlite3.OperationalError:
        print(
            f"  {WARN}[WARN]{END} 当前 SQLite {sqlite3.sqlite_version} 不支持 "
            f"trigram——agent_memory_fts 已降级，中文关键词检索受限"
        )

    print("== 3. 最近记忆条目（热层） ==")
    rows = db.execute(
        "SELECT task_id, run_id, outcome, tokens_used, substr(summary,1,40), "
        "created_at FROM agent_working_memory ORDER BY id DESC LIMIT 8"
    ).fetchall()
    if not rows:
        print("  (空——尚未执行过带记忆的总结任务，先 trigger 一次 job 再回来)")
    for r in rows:
        print(f"  {r[0]:<24} {r[1][:8]:<10} {r[2]:<6} tok={r[3]:<5} {r[5]}  {r[4]}...")

    print("== 4. 冷层归档规模 ==")
    n = db.execute("SELECT COUNT(*) FROM agent_working_memory_archive").fetchone()[0]
    print(
        f"  {n} 条归档（prune 下沉产物，已参与 related 联表反查；search_archive 留给 Phase 4 全量查史）"
    )

    print("== 5. 消费标记一致性（关联表 sync_records_consumed） ==")
    marked = db.execute(
        "SELECT COUNT(DISTINCT sync_record_id) FROM sync_records_consumed"
    ).fetchone()[0]
    orphans = db.execute(
        """SELECT COUNT(*) FROM sync_records_consumed c
           LEFT JOIN agent_working_memory m ON c.run_id = m.run_id
           LEFT JOIN agent_working_memory_archive a ON c.run_id = a.run_id
           WHERE m.run_id IS NULL AND a.run_id IS NULL"""
    ).fetchone()[0]
    print(f"  {marked} 条记录已消费，{orphans} 条悬空（应为 0）")
    if orphans:
        fail("存在悬空消费标记（run_id 已不在主表与归档表）")

    print("== 6. LLM 用量归属（W2 验证：主调用+摘要调用同 job_name） ==")
    if "llm_usage_logs" in tables:
        rows = db.execute(
            "SELECT job_name, status, COUNT(*), SUM(total_tokens) FROM llm_usage_logs "
            "GROUP BY job_name, status ORDER BY job_name"
        ).fetchall()
        for r in rows:
            print(
                f"  {r[0] or '(空)'!r:<24} {r[1]:<8} {r[2]:>3} 次  {r[3] or 0:>7} tokens"
            )
    else:
        print(f"  {WARN}(llm_usage_logs 表不存在，跳过——首次 LLM 调用后自动建表){END}")

    print("== 7. 记忆条数分布 ==")
    for r in db.execute(
        "SELECT task_id, COUNT(*) FROM agent_working_memory GROUP BY task_id"
    ):
        print(f"  {r[0]:<24} {r[1]} 条热记忆")

    return ok


def _keywords_demo(db: sqlite3.Connection, keywords: list[str]) -> None:
    print(f"== 8. FTS 关键词演示（{', '.join(keywords)}） ==")
    print(
        f"  {WARN}(deprecated：FTS 检索已停用，相关回忆由联表反查承担；"
        f"本段仅验证物理索引健康，见 closeout §5){END}"
    )
    terms = ['"' + t.replace('"', '""') + '"' for t in keywords if len(t.strip()) >= 3]
    if not terms:
        print("  （关键词都短于 3 字符，trigram 无法命中）")
        return
    match = " OR ".join(terms)
    rows = db.execute(
        """SELECT m.task_id, m.run_id, substr(m.summary,1,50), m.created_at
           FROM agent_memory_fts f
           JOIN agent_working_memory m ON m.id = f.rowid
           WHERE agent_memory_fts MATCH ? ORDER BY rank LIMIT 5""",
        (match,),
    ).fetchall()
    for r in rows:
        print(f"  {r[0]:<20} {r[1][:8]:<10} {r[3]}  {r[2]}...")
    if not rows:
        print(f"  {WARN}(无命中——先确认该关键词出现在某条 summary 摘要中){END}")


def _titles_demo(db: sqlite3.Connection, titles: list[str]) -> None:
    """联表反查演示（线上等价于 get_related_titles）：同步记录消费标记 → 摘要。"""
    print(f"== 9. 同剧关联演示（{', '.join(titles)}） ==")
    placeholders = ",".join("?" * len(titles))
    rows = db.execute(
        f"""SELECT m.task_id, m.run_id, substr(m.summary,1,60), '热层' AS layer, m.created_at
            FROM agent_working_memory m
            JOIN sync_records_consumed c ON c.run_id = m.run_id
            JOIN sync_records s ON s.id = c.sync_record_id
            WHERE s.bgm_title IN ({placeholders}) GROUP BY m.id
           UNION
           SELECT m.task_id, m.run_id, substr(m.summary,1,60), '冷层' AS layer, m.created_at
            FROM agent_working_memory_archive m
            JOIN sync_records_consumed c ON c.run_id = m.run_id
            JOIN sync_records s ON s.id = c.sync_record_id
            WHERE s.bgm_title IN ({placeholders}) GROUP BY m.id
           ORDER BY created_at DESC, id DESC LIMIT 5""",
        (*titles, *titles),
    ).fetchall()
    for r in rows:
        print(f"  [{r[3]}] {r[0]:<20} {r[1][:8]:<10} {r[4]}  {r[2]}...")
    if not rows:
        print(
            f"  {WARN}(无命中——需先有：同剧记录被某次总结消费过（关联表有消费标记）){END}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_DEFAULT), help="SQLite 库路径")
    parser.add_argument(
        "--keywords", nargs="*", default=[], help="FTS 演示关键词(deprecated)"
    )
    parser.add_argument("--titles", nargs="*", default=[], help="同剧关联演示（剧名）")
    args = parser.parse_args()

    path = Path(args.db)
    if not path.exists():
        print(
            f"{WARN}数据库不存在: {path}{END}\n"
            f"预期路径见 config.ini [database] 或默认 data/sync_records.db"
        )
        return 1

    db = sqlite3.connect(path)
    try:
        ok = _check(db)
        if args.keywords:
            _keywords_demo(db, args.keywords)
        if args.titles:
            _titles_demo(db, args.titles)
    finally:
        db.close()

    print(f"\n结论: {'通过 ✅' if ok else '存在异常，见上方 [FAIL] 项 ⚠️'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
