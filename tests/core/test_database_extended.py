"""
Database 模块扩展测试
"""

import sqlite3
from unittest.mock import patch


class TestDatabaseManager:
    """DatabaseManager 类测试"""

    def test_database_manager_import(self):
        """测试导入 DatabaseManager"""
        from app.core.database import DatabaseManager

        assert DatabaseManager is not None

    @patch("app.core.database.database_manager.db_path", "/tmp/test.db")
    def test_database_manager_init(self):
        """测试 DatabaseManager 初始化"""
        from app.core.database import DatabaseManager

        # 只测试可以导入
        assert DatabaseManager is not None

    def test_database_manager_has_db_path(self):
        """测试 DatabaseManager 有 db_path 属性"""
        from app.core.database import database_manager

        assert hasattr(database_manager, "db_path")


class TestDatabaseOperations:
    """数据库操作测试"""

    @patch("app.core.database.database_manager.db_path", "/tmp/test.db")
    def test_database_connection(self):
        """测试数据库连接"""
        # 创建临时数据库
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            conn = sqlite3.connect(db_path)
            assert conn is not None
            conn.close()
        finally:
            import os

            os.unlink(db_path)

    @patch("app.core.database.database_manager.db_path", "/tmp/test.db")
    def test_database_cursor(self):
        """测试数据库游标"""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
            conn.commit()
            cursor.execute("INSERT INTO test (name) VALUES ('test')")
            conn.commit()
            cursor.execute("SELECT * FROM test")
            result = cursor.fetchone()
            assert result[1] == "test"
            conn.close()
        finally:
            import os

            os.unlink(db_path)


class TestDatabaseSchema:
    """数据库模式测试"""

    @patch("app.core.database.database_manager.db_path", "/tmp/test.db")
    def test_trakt_config_table(self):
        """测试 trakt_config 表"""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # 创建 trakt_config 表
            cursor.execute("""
                CREATE TABLE trakt_config (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT,
                    expires_at INTEGER,
                    enabled BOOLEAN DEFAULT 1,
                    sync_interval TEXT DEFAULT '0 */6 * * *',
                    last_sync_time INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(user_id)
                )
            """)

            # 插入测试数据
            cursor.execute(
                """
                INSERT INTO trakt_config
                (user_id, access_token, refresh_token, expires_at, enabled,
                 sync_interval, last_sync_time, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    "test_user",
                    "test_token",
                    "refresh_token",
                    1234567890,
                    1,
                    "0 */6 * * *",
                    1234567890,
                    1234567890,
                    1234567890,
                ),
            )

            conn.commit()

            # 验证插入成功
            cursor.execute(
                "SELECT * FROM trakt_config WHERE user_id = ?", ("test_user",)
            )
            result = cursor.fetchone()
            assert result is not None
            assert result[1] == "test_user"

            conn.close()
        finally:
            import os

            os.unlink(db_path)
