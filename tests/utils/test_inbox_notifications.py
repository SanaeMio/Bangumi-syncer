"""收件箱通知聚合工具测试。"""

from app.utils.inbox_notifications import (
    aggregate_notification_rows,
    aggregated_notification_title,
    notification_group_key,
)


def test_notification_group_key():
    assert notification_group_key("同步失败：鬼灭之刃 S1E5") == "鬼灭之刃"
    assert notification_group_key("同步失败：剧场 剧场版") == "剧场"
    assert notification_group_key("其他标题") == "其他标题"


def test_aggregate_notification_rows():
    rows = [
        {
            "id": 2,
            "type": "sync_failed",
            "title": "同步失败：A S1E2",
            "body": "b2",
            "ref_id": 2,
            "created_at": "t2",
            "read_at": None,
        },
        {
            "id": 1,
            "type": "sync_failed",
            "title": "同步失败：A S1E1",
            "body": "b1",
            "ref_id": 1,
            "created_at": "t1",
            "read_at": None,
        },
    ]
    out = aggregate_notification_rows(rows)
    assert len(out) == 1
    assert out[0]["count"] == 2
    assert out[0]["notification_ids"] == [2, 1]
    assert out[0]["title"] == aggregated_notification_title("同步失败：A S1E2", 2)
