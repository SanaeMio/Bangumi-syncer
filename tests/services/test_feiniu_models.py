"""飞牛 dataclass 模型构造。"""

from app.services.feiniu.models import FeiniuUser, FeiniuWatchRecord


def test_feiniu_user_frozen():
    u = FeiniuUser(guid="g", username="n")
    assert u.guid == "g"
    assert u.username == "n"


def test_feiniu_watch_record_optional_original_title():
    r = FeiniuWatchRecord(
        item_guid="i",
        user_guid="u",
        username="un",
        display_title="D",
        original_title=None,
        season=1,
        episode=2,
        release_date="2024-01-01",
        update_time_ms=1,
    )
    assert r.original_title is None
    assert r.season == 1
