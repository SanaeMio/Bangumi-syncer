"""Test SummaryJobConfig dataclass and from_config_dict constructor."""

from app.services.summary.models import SummaryJobConfig

# ── from_config_dict ──────────────────────────────────────────────────


def test_from_config_dict_full():
    """All fields provided — instance matches exactly."""
    data = {
        "id": "3",
        "enabled": "true",
        "name": "每日追番总结",
        "cron": "0 8 * * *",
        "lookback_days": "7",
        "user_name": "testuser",
        "system_prompt": "custom prompt",
        "max_records": "150",
    }
    cfg = SummaryJobConfig.from_config_dict(data)
    assert cfg.id == 3
    assert cfg.enabled is True
    assert cfg.name == "每日追番总结"
    assert cfg.cron == "0 8 * * *"
    assert cfg.lookback_days == 7
    assert cfg.user_name == "testuser"
    assert cfg.system_prompt == "custom prompt"
    assert cfg.max_records == 150


def test_from_config_dict_defaults_empty():
    """Empty dict produces an instance with all defaults."""
    cfg = SummaryJobConfig.from_config_dict({})
    assert cfg.id == 0
    assert cfg.enabled is True
    assert cfg.name == ""
    assert cfg.cron == "0 21 * * *"
    assert cfg.lookback_days == 1
    assert cfg.user_name == ""
    assert cfg.system_prompt == SummaryJobConfig.system_prompt  # type: ignore[misc]
    assert cfg.max_records == 200


def test_from_config_dict_defaults_minimal():
    """Only id provided — remaining fields take defaults."""
    cfg = SummaryJobConfig.from_config_dict({"id": "5"})
    assert cfg.id == 5
    assert cfg.enabled is True
    assert cfg.name == ""
    assert cfg.cron == "0 21 * * *"
    assert cfg.lookback_days == 1
    assert cfg.user_name == ""
    assert cfg.system_prompt == SummaryJobConfig.system_prompt  # type: ignore[misc]
    assert cfg.max_records == 200


# ── bool coercion ─────────────────────────────────────────────────────


def test_enabled_bool_coercion_true_variants():
    for value in ("true", "True", "TRUE", "1"):
        cfg = SummaryJobConfig.from_config_dict({"id": "1", "enabled": value})
        assert cfg.enabled is True, f"enabled={value!r} should be True"


def test_enabled_bool_coercion_false_variants():
    for value in ("false", "False", "FALSE", "0"):
        cfg = SummaryJobConfig.from_config_dict({"id": "1", "enabled": value})
        assert cfg.enabled is False, f"enabled={value!r} should be False"


def test_enabled_already_bool():
    """Bool values pass through without error."""
    cfg_true = SummaryJobConfig.from_config_dict({"id": "1", "enabled": True})
    assert cfg_true.enabled is True
    cfg_false = SummaryJobConfig.from_config_dict({"id": "1", "enabled": False})
    assert cfg_false.enabled is False


# ── int coercion ──────────────────────────────────────────────────────


def test_lookback_days_coercion():
    cfg = SummaryJobConfig.from_config_dict({"id": "1", "lookback_days": "14"})
    assert cfg.lookback_days == 14
    assert isinstance(cfg.lookback_days, int)


def test_max_records_coercion():
    cfg = SummaryJobConfig.from_config_dict({"id": "1", "max_records": "500"})
    assert cfg.max_records == 500
    assert isinstance(cfg.max_records, int)


# ── system_prompt default ─────────────────────────────────────────────


def test_system_prompt_default_value():
    """Default system_prompt contains key phrases from the spec."""
    expected = (
        "你是一个轻松有趣的追番助手。用户会给你一段指定时间范围内的观影记录，请你用亲切自然的中文生成追番总结。\n\n"
        "规则：\n"
        '1. 如果记录为 0 条，告知用户"这段时间还没有追番记录哦~"\n'
        '2. 按番剧分组，简要描述观看进度（如"《芙莉莲》追到 S1E10"）\n'
        "3. 如果涉及多用户（记录中 user_name 不同），按用户分开描述\n"
        "4. 加一两句轻松评论，语气像朋友聊天，不要太正式\n"
        "5. 限制在 300 字以内"
    )
    assert SummaryJobConfig.system_prompt == expected  # type: ignore[misc]


# ── user_prompt_template is NOT a dataclass field ─────────────────────


def test_no_user_prompt_template_attribute():
    """user_prompt_template should not be an attribute of the dataclass."""
    cfg = SummaryJobConfig.from_config_dict({"id": "1"})
    assert not hasattr(cfg, "user_prompt_template")
