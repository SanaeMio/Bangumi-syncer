"""CooldownPolicy 回归测试：多账号场景下各账号通知互不拦截。

覆盖 notification_service.CooldownPolicy 在条目级（item_level）类型上按
``bgm_username`` 区分冷却 key，使非首选账号的通知不会被首选账号的冷却拦截
（真实日志复现：双账号同步只收到首选账号一条 mark_success 通知）。
"""

from app.services.notification_service import CooldownPolicy


def _item_data(bgm_username: str = "") -> dict:
    return {
        "title": "尼古喵喵",
        "season": 1,
        "episode": 11,
        "bgm_username": bgm_username,
    }


def test_item_level_cooldown_distinguishes_accounts():
    # 同一条目、同一渠道与类型，不同账号应互不拦截（多账号各自发送）
    policy = CooldownPolicy(cooldown_seconds=60)
    assert (
        policy.allow("notify-webhook-1", "mark_success", _item_data("621538")) is True
    )
    assert (
        policy.allow("notify-webhook-1", "mark_success", _item_data("944646")) is True
    )


def test_item_level_cooldown_same_account_throttled():
    # 同一账号短时间内重复通知同一条目应被冷却拦截（既有行为保留）
    policy = CooldownPolicy(cooldown_seconds=60)
    data = _item_data("621538")
    assert policy.allow("notify-webhook-1", "mark_success", data) is True
    assert policy.allow("notify-webhook-1", "mark_success", data) is False


def test_item_level_cooldown_no_username_throttled():
    # 不带 bgm_username 的同条目重复通知仍按原逻辑拦截
    policy = CooldownPolicy(cooldown_seconds=60)
    data = _item_data("")
    assert policy.allow("notify-webhook-1", "mark_success", data) is True
    assert policy.allow("notify-webhook-1", "mark_success", data) is False


def test_item_level_key_includes_username():
    policy = CooldownPolicy()
    key_a = policy._key("c1", "mark_success", _item_data("621538"))
    key_b = policy._key("c1", "mark_success", _item_data("944646"))
    assert key_a != key_b
    assert "621538" in key_a
    assert "944646" in key_b
