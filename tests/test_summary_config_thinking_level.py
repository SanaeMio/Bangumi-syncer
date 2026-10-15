"""总结任务配置 thinking_level 字段的持久化/模型层测试。

覆盖场景：
1. 保存含 thinking_level="high" → 读回为 "high"
2. 保存不含该键 → 读回为 "off"（默认值）
3. 非法值回退 → get_summary_configs 解析为 "off"
4. config_schema 注册默认值
5. SummaryJobConfig 模型读取与容错
"""


def _cm_from_ini(tmp_path, ini_text: str):
    """构建一个指向临时 config.ini 的 ConfigManager，不运行 __init__。"""
    from app.core.config import ConfigManager

    p = tmp_path / "config.ini"
    p.write_text(ini_text, encoding="utf-8")
    cm = ConfigManager.__new__(ConfigManager)
    cm.platform = "Test"
    cm.cwd = tmp_path
    cm.config_paths = {
        "env": None,
        "mounted": tmp_path / "__no_mounted__.ini",
        "dev": tmp_path / "__no_dev__.ini",
        "default": p,
    }
    cm.active_config_path = p
    cm._config_cache = None
    cm._last_modified = 0
    cm._load_config()
    return cm


def _cm_with_manual_field(tmp_path, section: str, key: str, value: str):
    """写入 INI 后手工追加某字段（用于模拟非法值场景）。"""
    from configparser import ConfigParser

    from app.core.config import ConfigManager

    p = tmp_path / "config.ini"
    p.write_text("", encoding="utf-8")
    cm = ConfigManager.__new__(ConfigManager)
    cm.platform = "Test"
    cm.cwd = tmp_path
    cm.config_paths = {
        "env": None,
        "mounted": tmp_path / "__no_mounted__.ini",
        "dev": tmp_path / "__no_dev__.ini",
        "default": p,
    }
    cm.active_config_path = p
    cm._config_cache = None
    cm._last_modified = 0
    cm._load_config()

    # 手工追加字段到已有节（绕过 save_summary_config 的字段白名单）
    parser = ConfigParser()
    parser.read(str(p), encoding="utf-8")
    if not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, key, value)
    with open(str(p), "w", encoding="utf-8") as fh:
        parser.write(fh)

    cm._config_cache = None
    cm._last_modified = 0
    cm._load_config()
    return cm


_SAMPLE_CONFIG = {
    "name": "每日追番总结",
    "enabled": True,
    "cron": "0 21 * * *",
    "lookback_days": 1,
    "user_name": "",
    "system_prompt": "你是一个友好的追番助手。",
    "max_records": 200,
}


class TestSummaryConfigThinkingLevelPersistence:
    """场景 1-3：ConfigManager 持久化层测试。"""

    def test_save_with_thinking_level_high_then_read_back(self, tmp_path):
        """场景 1：save 含 thinking_level='high' 的配置 → 读回为 'high'。"""
        cm = _cm_from_ini(tmp_path, "[bangumi]\nusername = u\n")
        data = dict(_SAMPLE_CONFIG)
        data["thinking_level"] = "high"
        cm.save_summary_config(data)

        configs = cm.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["thinking_level"] == "high"

    def test_save_without_thinking_level_defaults_to_off(self, tmp_path):
        """场景 2：save 不含该键 → 读回为 'off'（默认值）。"""
        cm = _cm_from_ini(tmp_path, "[bangumi]\nusername = u\n")
        cm.save_summary_config(dict(_SAMPLE_CONFIG))

        configs = cm.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["thinking_level"] == "off"

    def test_invalid_thinking_level_falls_back_to_off(self, tmp_path):
        """场景 3：手工写入非法值 'turbo' → get_summary_configs 读回解析为 'off'。"""
        # 用 ConfigParser 直接追加非法值
        cm2 = _cm_with_manual_field(
            tmp_path, "summary-测试任务", "thinking_level", "turbo"
        )

        configs = cm2.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["thinking_level"] == "off"

    def test_thinking_level_roundtrip_all_valid_values(self, tmp_path):
        """所有合法值 off/low/medium/high 均可正确保存并读回。"""
        for level in ("off", "low", "medium", "high"):
            cm = _cm_from_ini(tmp_path, "[bangumi]\nusername = u\n")
            data = dict(_SAMPLE_CONFIG)
            data["name"] = f"任务-{level}"
            data["thinking_level"] = level
            cm.save_summary_config(data)

            configs = cm.get_summary_configs()
            assert len(configs) == 1
            assert configs[0]["name"] == f"任务-{level}"
            assert configs[0]["thinking_level"] == level

    def test_case_insensitive_thinking_level_low(self, tmp_path):
        """手工写入 'Low'（首字母大写）→ 归一化为 'low'，不再降级为 off。"""
        cm = _cm_with_manual_field(
            tmp_path, "summary-测试任务", "thinking_level", "Low"
        )
        configs = cm.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["thinking_level"] == "low"

    def test_case_insensitive_thinking_level_with_whitespace(self, tmp_path):
        """手工写入 ' Medium '（带空格+大写）→ 归一化为 'medium'。"""
        cm = _cm_with_manual_field(
            tmp_path, "summary-测试任务", "thinking_level", " Medium "
        )
        configs = cm.get_summary_configs()
        assert len(configs) == 1
        assert configs[0]["thinking_level"] == "medium"


class TestSummaryConfigSchema:
    """场景 4：config_schema 注册默认值。"""

    def test_summary_schema_thinking_level_default(self):
        """summary 段 schema 中 thinking_level 默认值为 'off'。"""
        from app.core import config_schema

        assert config_schema.field_default("summary", "thinking_level") == "off"


class TestSummaryJobConfigThinkingLevel:
    """场景 5：SummaryJobConfig 模型读取与容错。"""

    def test_from_config_dict_reads_thinking_level(self):
        """from_config_dict 正确读取 thinking_level 字段。"""
        from app.services.summary.models import SummaryJobConfig

        data = {
            "name": "测试",
            "thinking_level": "high",
        }
        cfg = SummaryJobConfig.from_config_dict(data)
        assert cfg.thinking_level == "high"

    def test_from_config_dict_defaults_to_off(self):
        """from_config_dict 未传 thinking_level 时默认为 'off'。"""
        from app.services.summary.models import SummaryJobConfig

        cfg = SummaryJobConfig.from_config_dict({"name": "测试"})
        assert cfg.thinking_level == "off"

    def test_from_config_dict_invalid_value_falls_back(self):
        """from_config_dict 非法值回落 'off'。"""
        from app.services.summary.models import SummaryJobConfig

        cfg = SummaryJobConfig.from_config_dict(
            {"name": "测试", "thinking_level": "turbo"}
        )
        assert cfg.thinking_level == "off"

    def test_dataclass_default_value(self):
        """SummaryJobConfig 实例化默认 thinking_level='off'。"""
        from app.services.summary.models import SummaryJobConfig

        cfg = SummaryJobConfig()
        assert cfg.thinking_level == "off"
