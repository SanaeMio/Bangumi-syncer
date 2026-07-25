"""测试 SummaryJobConfig 数据类和 from_config_dict 构造函数。"""

from app.services.summary.models import SummaryJobConfig

# ── from_config_dict ──────────────────────────────────────────────────


def test_from_config_dict_full():
    """提供所有字段时 —— 实例完全匹配。"""
    data = {
        "name": "每日追番总结",
        "enabled": "true",
        "cron": "0 8 * * *",
        "lookback_days": "7",
        "user_name": "testuser",
        "system_prompt": "custom prompt",
        "max_records": "150",
    }
    cfg = SummaryJobConfig.from_config_dict(data)
    assert cfg.name == "每日追番总结"
    assert cfg.enabled is True
    assert cfg.cron == "0 8 * * *"
    assert cfg.lookback_days == 7
    assert cfg.user_name == "testuser"
    assert cfg.system_prompt == "custom prompt"
    assert cfg.max_records == 150


def test_from_config_dict_defaults_empty():
    """空字典使用全部默认值创建实例。"""
    cfg = SummaryJobConfig.from_config_dict({})
    assert cfg.name == ""
    assert cfg.enabled is True
    assert cfg.cron == "0 21 * * *"
    assert cfg.lookback_days == 1
    assert cfg.user_name == ""
    assert cfg.system_prompt == SummaryJobConfig.system_prompt
    assert cfg.max_records == 200


def test_from_config_dict_defaults_minimal():
    """仅提供名称 —— 其余字段取默认值。"""
    cfg = SummaryJobConfig.from_config_dict({"name": "test"})
    assert cfg.name == "test"
    assert cfg.enabled is True
    assert cfg.cron == "0 21 * * *"
    assert cfg.lookback_days == 1
    assert cfg.user_name == ""
    assert cfg.system_prompt == SummaryJobConfig.system_prompt
    assert cfg.max_records == 200


# ── bool coercion ─────────────────────────────────────────────────────


def test_enabled_bool_coercion_true_variants():
    for value in ("true", "True", "TRUE", "1"):
        cfg = SummaryJobConfig.from_config_dict({"name": "t", "enabled": value})
        assert cfg.enabled is True, f"enabled={value!r} should be True"


def test_enabled_bool_coercion_false_variants():
    for value in ("false", "False", "FALSE", "0"):
        cfg = SummaryJobConfig.from_config_dict({"name": "t", "enabled": value})
        assert cfg.enabled is False, f"enabled={value!r} should be False"


def test_enabled_already_bool():
    """布尔值直接通过，不报错。"""
    cfg_true = SummaryJobConfig.from_config_dict({"name": "t", "enabled": True})
    assert cfg_true.enabled is True
    cfg_false = SummaryJobConfig.from_config_dict({"name": "t", "enabled": False})
    assert cfg_false.enabled is False


# ── int coercion ──────────────────────────────────────────────────────


def test_lookback_days_coercion():
    cfg = SummaryJobConfig.from_config_dict({"name": "t", "lookback_days": "14"})
    assert cfg.lookback_days == 14
    assert isinstance(cfg.lookback_days, int)


def test_max_records_coercion():
    cfg = SummaryJobConfig.from_config_dict({"name": "t", "max_records": "500"})
    assert cfg.max_records == 500
    assert isinstance(cfg.max_records, int)


# ── system_prompt default ─────────────────────────────────────────────


def test_system_prompt_default_value():
    """默认 system_prompt 包含规范中的关键短语。"""
    expected = (
        "你是一个轻松有趣的追番助手。用户会给你一段指定时间范围内的观影记录，请你用亲切自然的中文生成追番总结。\n\n"
        "规则：\n"
        '1. 如果记录为 0 条，告知用户"这段时间还没有追番记录哦~"\n'
        '2. 按番剧分组，简要描述观看进度（如"《芙莉莲》追到 S1E10"）\n'
        "3. 如果涉及多用户（记录中 user_name 不同），按用户分开描述\n"
        "4. 加一两句轻松评论，语气像朋友聊天，不要太正式\n"
        "5. 限制在 300 字以内"
    )
    assert SummaryJobConfig.system_prompt == expected


# ── user_prompt_template is NOT a dataclass field ─────────────────────


def test_no_user_prompt_template_attribute():
    """user_prompt_template 不应该是 dataclass 的属性。"""
    cfg = SummaryJobConfig.from_config_dict({"name": "t"})
    assert not hasattr(cfg, "user_prompt_template")
