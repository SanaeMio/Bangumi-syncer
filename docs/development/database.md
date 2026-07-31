---
title: 🗄️ 数据库仓储层
order: 5
---

# 🗄️ 数据库仓储层

`app/core/database/` 是 SQLite 仓储层，**无 ORM**，直接用 `sqlite3` + 参数化 SQL。

## 技术栈

- **数据库**：SQLite（Python 标准库 `sqlite3`）
- **PRAGMA**：`journal_mode=WAL`、`synchronous=NORMAL`、`busy_timeout=5000`
- **迁移**：`ALTER TABLE` 增量迁移函数，无需手写迁移脚本
- **默认路径**：`data/sync_records.db`

---

## Repository 模式

每个表对应一个 Repository 类，继承 `base_repository.py` 的基类，共享一个 `DatabaseConnection`（单例 `database_manager`），通过 `_lock` 串行化写操作。

| 表 | Repository | 说明 |
| --- | --- | --- |
| `sync_records` | `sync_records.py` | 同步记录，含 `match_trace` JSON、`match_score`、`source` |
| `pending_candidates` | `pending_candidates.py` | 待确认候选（匹配失败时沉淀），部分唯一索引去重 |
| `pending_sync_queue` | `pending_sync_queue.py` | Replay 待同步队列，部分唯一索引去重 |
| `in_app_notifications` | `inbox.py` | 站内信 |
| `trakt_config` / `trakt_sync_history` | `trakt.py` | Trakt 多用户配置与同步历史 |
| `feiniu_sync_history` / `feiniu_meta` | `feiniu.py` | 飞牛同步历史与启动水位 |
| `announcement_read_state` / `llm_usage` | `inbox.py` / `llm_usage.py` | 公告已读状态 / LLM 调用计量 |

---

## 常见参数（PRAGMA）

```python
cursor.execute("PRAGMA journal_mode=WAL")       # WAL 模式，并发读写稳定
cursor.execute("PRAGMA synchronous=NORMAL")     # 平衡性能与安全
cursor.execute("PRAGMA busy_timeout=5000")      # 写锁等待 5 秒
```

---

## 表结构演进

表结构通过 `connection.py` 中的 `ALTER TABLE` 增量迁移函数实现，应用启动时自动检测并执行。新增字段只需：

1. 在 `_init_database()` 的 `CREATE TABLE` 中加字段（新库直接有）
2. 写一个 `_ensure_xxx_field()` 迁移函数（老库自动补）

```python
def _ensure_sync_records_media_type(conn) -> None:
    """老库补 media_type 字段"""
    cursor = conn.execute("PRAGMA table_info(sync_records)")
    columns = {row[1] for row in cursor.fetchall()}
    if "media_type" not in columns:
        conn.execute(
            "ALTER TABLE sync_records ADD COLUMN media_type TEXT DEFAULT 'episode'"
        )
```

---

## 常见场景

### 写记录

```python
from app.core.database import database_manager

database_manager.log_sync_record(
    user_name="alice",
    title="测试番剧",
    season=1, episode=1,
    status="success",
    source="emby",
    match_trace=trace.to_json(),
)
```

### 查询记录

```python
records = database_manager.get_sync_records(
    user_name="alice",
    source="emby",
    limit=20,
)
```

### 测试中 mock 数据库

用 `tmp_path` fixture 创建临时 SQLite，或用 `MonkeyPatch` 替换 `database_manager._conn`：

```python
def test_log_record(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    # 用临时库替换单例连接
    ...
```
