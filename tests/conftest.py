"""
Trakt 测试配置和 fixture
"""

import asyncio
import os
import sqlite3
import tempfile
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.core.config import config_manager
from app.core.database import database_manager
from app.models.trakt import TraktConfig


@pytest.fixture
def test_db():
    """创建测试数据库连接"""
    # 创建临时数据库文件
    temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    temp_db.close()

    # 连接数据库
    conn = sqlite3.connect(temp_db.name)

    # 创建测试表结构
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

    # 创建 trakt_sync_history 表
    cursor.execute("""
        CREATE TABLE trakt_sync_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            trakt_item_id TEXT NOT NULL,
            media_type TEXT NOT NULL,
            watched_at INTEGER NOT NULL,
            synced_at INTEGER NOT NULL,
            task_id TEXT,
            UNIQUE(user_id, trakt_item_id, watched_at)
        )
    """)

    # 创建 sync_records 表（模拟现有表）
    cursor.execute("""
        CREATE TABLE sync_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            ori_title TEXT,
            season INTEGER,
            episode INTEGER,
            user_name TEXT,
            source TEXT,
            status TEXT,
            message TEXT,
            created_at INTEGER
        )
    """)

    conn.commit()

    yield temp_db.name

    # 清理
    conn.close()
    os.unlink(temp_db.name)


@pytest.fixture
def mock_database_manager(test_db):
    """模拟 database_manager 使用测试数据库"""
    with patch.object(database_manager, "db_path", test_db):
        # 添加测试方法到 database_manager
        def get_trakt_config(user_id):
            conn = sqlite3.connect(test_db)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trakt_config WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            conn.close()

            if row:
                return {
                    "id": row[0],
                    "user_id": row[1],
                    "access_token": row[2],
                    "refresh_token": row[3],
                    "expires_at": row[4],
                    "enabled": bool(row[5]),
                    "sync_interval": row[6],
                    "last_sync_time": row[7],
                    "created_at": row[8],
                    "updated_at": row[9],
                }
            return None

        def save_trakt_config(config):
            conn = sqlite3.connect(test_db)
            cursor = conn.cursor()

            now = int(time.time())
            if "id" in config:
                # 更新
                cursor.execute(
                    """
                    UPDATE trakt_config SET
                        access_token = ?,
                        refresh_token = ?,
                        expires_at = ?,
                        enabled = ?,
                        sync_interval = ?,
                        last_sync_time = ?,
                        updated_at = ?
                    WHERE id = ?
                """,
                    (
                        config["access_token"],
                        config.get("refresh_token"),
                        config.get("expires_at"),
                        1 if config.get("enabled", True) else 0,
                        config.get("sync_interval", "0 */6 * * *"),
                        config.get("last_sync_time"),
                        now,
                        config["id"],
                    ),
                )
            else:
                # 插入
                cursor.execute(
                    """
                    INSERT INTO trakt_config (
                        user_id, access_token, refresh_token,
                        expires_at, enabled, sync_interval,
                        last_sync_time, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        config["user_id"],
                        config["access_token"],
                        config.get("refresh_token"),
                        config.get("expires_at"),
                        1 if config.get("enabled", True) else 0,
                        config.get("sync_interval", "0 */6 * * *"),
                        config.get("last_sync_time"),
                        now,
                        now,
                    ),
                )

            conn.commit()
            conn.close()

        def add_trakt_sync_history(history_data):
            conn = sqlite3.connect(test_db)
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO trakt_sync_history (
                    user_id, trakt_item_id, media_type,
                    watched_at, synced_at, task_id
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    history_data["user_id"],
                    history_data["trakt_item_id"],
                    history_data["media_type"],
                    history_data["watched_at"],
                    history_data["synced_at"],
                    history_data.get("task_id"),
                ),
            )

            conn.commit()
            conn.close()

        def get_trakt_sync_history(user_id, limit=100):
            conn = sqlite3.connect(test_db)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT * FROM trakt_sync_history
                WHERE user_id = ?
                ORDER BY synced_at DESC
                LIMIT ?
            """,
                (user_id, limit),
            )

            rows = cursor.fetchall()
            conn.close()

            records = []
            for row in rows:
                records.append(
                    {
                        "id": row[0],
                        "user_id": row[1],
                        "trakt_item_id": row[2],
                        "media_type": row[3],
                        "watched_at": row[4],
                        "synced_at": row[5],
                        "task_id": row[6],
                    }
                )

            return {"records": records, "total": len(records)}

        # 临时替换方法
        database_manager.get_trakt_config = get_trakt_config
        database_manager.save_trakt_config = save_trakt_config
        database_manager.add_trakt_sync_history = add_trakt_sync_history
        database_manager.get_trakt_sync_history = get_trakt_sync_history

        yield database_manager


@pytest.fixture
def mock_config_manager():
    """模拟配置管理器"""
    with patch.object(config_manager, "get_trakt_config") as mock_get_config:
        mock_get_config.return_value = {
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
            "redirect_uri": "http://localhost:8000/api/trakt/auth/callback",
        }
        yield config_manager


@pytest.fixture
def mock_httpx_client():
    """模拟 httpx.AsyncClient"""
    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = AsyncMock()
    mock_response.headers = {}

    mock_client.request = AsyncMock(return_value=mock_response)
    mock_client.aclose = AsyncMock()

    with patch("httpx.AsyncClient", return_value=mock_client):
        yield mock_client


@pytest.fixture
def sample_trakt_history():
    """创建示例 Trakt 观看历史数据"""
    return {
        "id": 123456,
        "watched_at": "2024-01-15T20:30:00.000Z",
        "action": "scrobble",
        "type": "episode",
        "episode": {"season": 1, "number": 5, "title": "Pilot", "ids": {"trakt": 123}},
        "show": {
            "title": "Example Show",
            "original_title": "Example Show Original",
            "ids": {"trakt": 456},
        },
    }


@pytest.fixture
def sample_trakt_config():
    """创建示例 Trakt 配置"""
    now = int(time.time())
    future = now + 3600  # 1小时后过期

    return TraktConfig(
        user_id="test_user",
        access_token="test_access_token",
        refresh_token="test_refresh_token",
        expires_at=future,
        enabled=True,
        sync_interval="0 */6 * * *",
        last_sync_time=now - 86400,  # 1天前
    )


@pytest.fixture
def mock_sync_service():
    """模拟同步服务"""
    mock_service = AsyncMock()
    mock_service.sync_custom_item_async = AsyncMock(return_value="test_task_id")
    mock_service.sync_custom_item = Mock()

    with patch("app.services.trakt.sync_service.sync_service", mock_service):
        yield mock_service


@pytest.fixture
def event_loop():
    """为异步测试创建事件循环"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture
async def mock_trakt_client():
    """创建模拟的 Trakt 客户端"""
    from app.services.trakt.client import TraktClient

    client = TraktClient(access_token="test_token")

    # 模拟内部客户端
    client._client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = AsyncMock(return_value=[])
    mock_response.headers = {}
    client._client.request = AsyncMock(return_value=mock_response)

    yield client

    # 清理
    client._client = None


@pytest.fixture
def mock_time():
    """模拟时间函数"""
    with patch("time.time") as mock_time_func:
        mock_time_func.return_value = 1700000000  # 固定时间戳
        yield mock_time_func


@pytest.fixture(scope="session", autouse=True)
def clean_proxy_env():
    """
    在测试会话期间强制移除代理设置，防止本地开发环境污染测试。
    """
    proxies = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ]
    stash = {}

    # 备份并移除
    for key in proxies:
        if key in os.environ:
            stash[key] = os.environ.pop(key)

    yield

    # 恢复（可选，如果 scope 是 function 则必须恢复）
    for key, value in stash.items():
        os.environ[key] = value
