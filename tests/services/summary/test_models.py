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
    assert cfg.max_records == -1
    assert cfg.memory_limit == 0  # 默认关闭（0=关）
    assert cfg.related_limit == 0


def test_from_config_dict_defaults_minimal():
    """仅提供名称 —— 其余字段取默认值。"""
    cfg = SummaryJobConfig.from_config_dict({"name": "test"})
    assert cfg.name == "test"
    assert cfg.enabled is True
    assert cfg.cron == "0 21 * * *"
    assert cfg.lookback_days == 1
    assert cfg.user_name == ""
    assert cfg.system_prompt == SummaryJobConfig.system_prompt
    assert cfg.max_records == -1


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


# ── memory_limit / related_limit（配置透传，0=关）────────────────────


def test_memory_config_parsed():
    """[summary-xxx] memory_limit=3、related_limit=2 → 透传。"""
    cfg = SummaryJobConfig.from_config_dict(
        {"name": "daily", "memory_limit": "3", "related_limit": "2"}
    )
    assert cfg.memory_limit == 3
    assert cfg.related_limit == 2


def test_memory_defaults_closed():
    """缺省 = 0（记忆特性关闭），不隐式开启。"""
    cfg = SummaryJobConfig.from_config_dict({"name": "t"})
    assert cfg.memory_limit == 0
    assert cfg.related_limit == 0


def test_memory_limit_scale_0_to_1000():
    """0=关、1–1000 生效；负值/超 1000/非法回落 0。"""
    assert (
        SummaryJobConfig.from_config_dict(
            {"name": "t", "memory_limit": "0"}
        ).memory_limit
        == 0
    )
    assert (
        SummaryJobConfig.from_config_dict(
            {"name": "t", "memory_limit": "1000"}
        ).memory_limit
        == 1000
    )
    assert (
        SummaryJobConfig.from_config_dict(
            {"name": "t", "memory_limit": "1001"}
        ).memory_limit
        == 1000
    )
    assert (
        SummaryJobConfig.from_config_dict(
            {"name": "t", "memory_limit": "-5"}
        ).memory_limit
        == 0
    )
    assert (
        SummaryJobConfig.from_config_dict(
            {"name": "t", "memory_limit": "abc"}
        ).memory_limit
        == 0
    )


def test_related_limit_scale_0_to_1000():
    assert (
        SummaryJobConfig.from_config_dict(
            {"name": "t", "related_limit": "1001"}
        ).related_limit
        == 1000
    )
    assert (
        SummaryJobConfig.from_config_dict(
            {"name": "t", "related_limit": "-1"}
        ).related_limit
        == 0
    )


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
    """默认 system_prompt 覆盖规范关键规则（独立语义锚点，不整段复制实现文案）。

    刻意逐条校验关键子句而非整段相等：文案微调（标点/措辞）不应误报，但语义
    规则被删改（角色、0 条告知、分组、字数上限）必须红。
    """
    prompt = SummaryJobConfig.system_prompt
    assert "追番助手" in prompt, "应保留角色设定"
    assert "追番总结" in prompt, "应说明产出目标"
    assert "还没有追番记录" in prompt, "应保留 0 条记录告知规则"
    assert "按番剧分组" in prompt, "应保留按番剧分组规则"
    assert "300 字以内" in prompt, "应保留 300 字字数上限"


# ── thinking_level ────────────────────────────────────────────────────


def test_thinking_level_valid_values_passthrough():
    """合法 thinking_level（含大小写/空白）归一化后透传。"""
    assert (
        SummaryJobConfig.from_config_dict(
            {"name": "t", "thinking_level": "medium"}
        ).thinking_level
        == "medium"
    )
    assert (
        SummaryJobConfig.from_config_dict(
            {"name": "t", "thinking_level": "  HIGH  "}
        ).thinking_level
        == "high"
    )


def test_thinking_level_invalid_falls_back_off():
    """非法 thinking_level 回落 'off'（坏配置不得拖垮调度注册）。"""
    for value in ("bogus", "highest", "", "   "):
        cfg = SummaryJobConfig.from_config_dict({"name": "t", "thinking_level": value})
        assert cfg.thinking_level == "off", f"thinking_level={value!r} 应回落 off"


# ── user_prompt_template is NOT a dataclass field ─────────────────────


def test_no_user_prompt_template_attribute():
    """user_prompt_template 不应该是 dataclass 的属性。"""
    cfg = SummaryJobConfig.from_config_dict({"name": "t"})
    assert not hasattr(cfg, "user_prompt_template")


def test_bad_lookback_days_does_not_crash():
    """非法 lookback_days/max_records 不抛异常（坏配置不拖垮调度注册）。"""
    cfg = SummaryJobConfig.from_config_dict(
        {"name": "t", "lookback_days": "abc", "max_records": "1.5"}
    )
    assert cfg.lookback_days == 1  # 回落默认
    assert cfg.max_records == -1  # 回落默认


def test_bad_max_records_negative_ok():
    """max_records=-1 合法；'abc' 回落 -1。"""
    cfg = SummaryJobConfig.from_config_dict({"name": "t", "max_records": "-1"})
    assert cfg.max_records == -1
    cfg = SummaryJobConfig.from_config_dict({"name": "t", "max_records": "abc"})
    assert cfg.max_records == -1
