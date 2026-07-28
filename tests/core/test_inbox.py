"""
InboxRepository 测试 — insert_notification。
"""

import tempfile
from pathlib import Path


def _make_repo(db_path=":memory:"):
    """创建一个指向临时 SQLite 文件的 InboxRepository。

    返回 (repo, db_conn)：db_conn 为 DatabaseConnection，
    调用方需在测试结束时显式 close() 以释放文件句柄，
    避免 Windows 上 TemporaryDirectory 清理失败（WinError 267）。
    """
    from app.core.database.connection import DatabaseConnection
    from app.core.database.inbox import InboxRepository

    db_conn = DatabaseConnection(db_path)
    return InboxRepository(db_conn), db_conn


class TestInsertNotification:
    """insert_notification 测试。"""

    def test_creates_unread_record(self):
        """写入后可通过 list_in_app_notifications 查询到未读记录。"""
        with tempfile.TemporaryDirectory() as td:
            repo, db_conn = _make_repo(str(Path(td) / "inbox.db"))
            try:
                repo.insert_notification(
                    notif_type="summary_llm_failed",
                    title="追番总结失败：测试任务",
                    body="LLM 返回空内容",
                )

                items = repo.list_in_app_notifications(limit=10)
                assert len(items) == 1
                item = items[0]
                assert item["type"] == "summary_llm_failed"
                assert item["title"] == "追番总结失败：测试任务"
                assert item["body"] == "LLM 返回空内容"
                assert item["read_at"] is None  # 未读
            finally:
                db_conn.close()

    def test_writes_summary_job_failed_type(self):
        """summary_job_failed 类型正确写入并可查询。"""
        with tempfile.TemporaryDirectory() as td:
            repo, db_conn = _make_repo(str(Path(td) / "inbox.db"))
            try:
                repo.insert_notification(
                    notif_type="summary_job_failed",
                    title="追番总结异常：异常任务",
                    body="Task config error",
                )

                items = repo.list_in_app_notifications(limit=10)
                assert len(items) == 1
                assert items[0]["type"] == "summary_job_failed"
                assert items[0]["title"] == "追番总结异常：异常任务"
            finally:
                db_conn.close()

    def test_multiple_inserts_preserve_order(self):
        """多条通知按创建时间排序（最新在前）。"""
        with tempfile.TemporaryDirectory() as td:
            repo, db_conn = _make_repo(str(Path(td) / "inbox.db"))
            try:
                repo.insert_notification(notif_type="t1", title="first")
                repo.insert_notification(notif_type="t2", title="second")
                repo.insert_notification(notif_type="t3", title="third")

                items = repo.list_in_app_notifications(limit=10)
                assert len(items) == 3
                # 默认按 created_at DESC
                assert items[0]["title"] == "third"
                assert items[2]["title"] == "first"
            finally:
                db_conn.close()

    def test_unread_only_filter(self):
        """unread_only=True 时仅返回未读记录。"""
        with tempfile.TemporaryDirectory() as td:
            repo, db_conn = _make_repo(str(Path(td) / "inbox.db"))
            try:
                repo.insert_notification(notif_type="a", title="unread")
                repo.insert_notification(notif_type="b", title="will be read")

                items = repo.list_in_app_notifications(limit=10)
                read_id = items[0]["id"]
                repo.mark_notification_read(read_id)

                unread = repo.list_in_app_notifications(limit=10, unread_only=True)
                assert len(unread) == 1
                assert unread[0]["title"] == "unread"
            finally:
                db_conn.close()

    def test_db_error_does_not_crash(self):
        """数据库连接断开时 insert_notification 不抛异常（容错）。"""
        with tempfile.TemporaryDirectory() as td:
            repo, db_conn = _make_repo(str(Path(td) / "inbox.db"))
            try:
                # 关闭底层连接模拟数据库故障
                db_conn.close()

                # 不应抛出异常
                repo.insert_notification(notif_type="test", title="should not crash")
            finally:
                db_conn.close()
