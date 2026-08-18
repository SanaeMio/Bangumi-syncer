"""SectionMeta 注册表派生查询的回归测试

验证散落白名单（_BANGUMI_NON_ACCOUNT_SECTIONS / env_overrides /
is_sensitive_ini_field）在改造为从 SectionMeta 派生后语义不变。
"""

from app.core import config_schema
from app.core.config_secret_crypto import is_sensitive_ini_field


class TestNonAccountBangumiSections:
    """_BANGUMI_NON_ACCOUNT_SECTIONS 派生正确性"""

    def test_includes_all_system_bangumi_sections(self):
        """系统功能段全部包含"""
        result = set(config_schema.non_account_bangumi_sections())
        assert result == {
            "bangumi-data",
            "bangumi-mapping",
            "bangumi-archive",
            "bangumi-replay",
            "bangumi-oauth",
        }

    def test_excludes_account_sections(self):
        """多账号段不在其中"""
        result = config_schema.non_account_bangumi_sections()
        # bangumi-{username} 形式不应出现（这些不是注册段，是运行时动态段）
        for sec in result:
            assert not sec.startswith("bangumi-user")  # 示例用户名


class TestEnvOverrides:
    """env_overrides 映射派生正确性"""

    def test_includes_bangumi_username(self):
        overrides = config_schema.all_env_overrides()
        assert overrides[("bangumi", "username")] == "BANGUMI_USERNAME"

    def test_includes_bangumi_access_token(self):
        overrides = config_schema.all_env_overrides()
        assert overrides[("bangumi", "access_token")] == "BANGUMI_ACCESS_TOKEN"

    def test_includes_feiniu_db_path(self):
        overrides = config_schema.all_env_overrides()
        assert overrides[("feiniu", "db_path")] == "FEINIU_DB_PATH"

    def test_includes_fongmi_all_fields(self):
        overrides = config_schema.all_env_overrides()
        assert overrides[("fongmi", "enabled")] == "FONGMI_ENABLED"
        assert overrides[("fongmi", "devices")] == "FONGMI_DEVICES"
        assert overrides[("fongmi", "subnet")] == "FONGMI_SUBNET"
        assert overrides[("fongmi", "auto_scan")] == "FONGMI_AUTO_SCAN"
        assert overrides[("fongmi", "sync_interval")] == "FONGMI_SYNC_INTERVAL"
        assert overrides[("fongmi", "min_percent")] == "FONGMI_MIN_PERCENT"

    def test_includes_dev_and_web(self):
        overrides = config_schema.all_env_overrides()
        assert overrides[("dev", "script_proxy")] == "HTTP_PROXY"
        assert overrides[("dev", "debug")] == "DEBUG_MODE"
        assert overrides[("dev", "log_level")] == "LOG_LEVEL"
        assert overrides[("web", "base_path")] == "APPLICATION_ROOT"

    def test_count_matches_original(self):
        """原硬编码共 14 条映射，新增 bangumi-oauth 的 client_id/client_secret 共 2 条、log_level 共 1 条"""
        overrides = config_schema.all_env_overrides()
        assert len(overrides) == 17


class TestIsSensitiveField:
    """is_sensitive_field 派生正确性（替代 is_sensitive_ini_field 分支）"""

    @staticmethod
    def _legacy_is_sensitive(section: str, option: str) -> bool:
        """改造前的原逻辑（已知 bug：bangumi-archive/replay 段 access_token 被误判为敏感）

        改造后通过 SectionMeta 修复：archive/replay 在 non_account_bangumi_sections() 内，
        不再被视为多账号段，故 access_token 不敏感。
        """
        _EXCLUDED = frozenset({"bangumi-data", "bangumi-mapping"})
        if option == "access_token":
            if section == "bangumi":
                return True
            if section.startswith("bangumi-") and section not in _EXCLUDED:
                return True
            return False
        if section == "auth" and option == "webhook_key":
            return True
        if section.startswith("notify-email-") and option == "smtp_password":
            return True
        if section == "trakt" and option == "client_secret":
            return True
        if section == "llm" and option == "api_key":
            return True
        return False

    def test_bangumi_access_token(self):
        assert config_schema.is_sensitive_field("bangumi", "access_token")
        assert is_sensitive_ini_field("bangumi", "access_token")

    def test_bangumi_user_access_token(self):
        """多账号段 access_token 敏感"""
        assert config_schema.is_sensitive_field("bangumi-user1", "access_token")
        assert is_sensitive_ini_field("bangumi-user1", "access_token")

    def test_bangumi_data_access_token_not_sensitive(self):
        """系统段 bangumi-data 不被视为多账号段"""
        assert not config_schema.is_sensitive_field("bangumi-data", "access_token")
        assert not is_sensitive_ini_field("bangumi-data", "access_token")

    def test_bangumi_archive_access_token_not_sensitive(self):
        assert not config_schema.is_sensitive_field("bangumi-archive", "access_token")
        assert not is_sensitive_ini_field("bangumi-archive", "access_token")

    def test_auth_webhook_key(self):
        assert config_schema.is_sensitive_field("auth", "webhook_key")
        assert is_sensitive_ini_field("auth", "webhook_key")

    def test_email_smtp_password(self):
        """多实例段 email-N 的 smtp_password 敏感"""
        assert config_schema.is_sensitive_field("notify-email-1", "smtp_password")
        assert is_sensitive_ini_field("notify-email-1", "smtp_password")
        assert config_schema.is_sensitive_field("notify-email-2", "smtp_password")

    def test_trakt_client_secret(self):
        assert config_schema.is_sensitive_field("trakt", "client_secret")
        assert is_sensitive_ini_field("trakt", "client_secret")

    def test_llm_api_key(self):
        assert config_schema.is_sensitive_field("llm", "api_key")
        assert is_sensitive_ini_field("llm", "api_key")

    def test_non_sensitive_fields(self):
        """普通字段不敏感"""
        assert not config_schema.is_sensitive_field("sync", "mode")
        assert not config_schema.is_sensitive_field("bangumi", "username")
        assert not config_schema.is_sensitive_field("feiniu", "db_path")
        assert not config_schema.is_sensitive_field("notify-webhook-1", "url")

    def test_legacy_parity(self):
        """与改造前原逻辑对比：常见场景结果一致

        注：bangumi-archive / bangumi-replay 在改造前因 _EXCLUDED 不含它们，
        access_token 会被误判为敏感（bug）。改造后修复为不敏感。
        此处仅对比无 bug 的场景，archive/replay 的修复在 test_bangumi_archive_* 单独验证。
        """
        cases = [
            ("bangumi", "access_token", True),
            ("bangumi-user1", "access_token", True),
            ("bangumi-data", "access_token", False),
            ("bangumi-mapping", "access_token", False),
            ("auth", "webhook_key", True),
            ("notify-email-1", "smtp_password", True),
            ("trakt", "client_secret", True),
            ("llm", "api_key", True),
            ("sync", "mode", False),
            ("bangumi", "username", False),
        ]
        for section, option, expected in cases:
            assert self._legacy_is_sensitive(section, option) == expected, (
                f"legacy mismatch: {section}.{option}"
            )
            assert config_schema.is_sensitive_field(section, option) == expected, (
                f"new mismatch: {section}.{option}"
            )
            assert is_sensitive_ini_field(section, option) == expected, (
                f"compat mismatch: {section}.{option}"
            )


class TestSchedulerLinkage:
    """段与调度器关联查询"""

    def test_archive_links_to_scheduler(self):
        assert (
            config_schema.scheduler_id_for_section("bangumi-archive")
            == "bangumi_archive"
        )

    def test_replay_links_to_scheduler(self):
        assert (
            config_schema.scheduler_id_for_section("bangumi-replay") == "bangumi_replay"
        )

    def test_feiniu_links_to_scheduler(self):
        assert config_schema.scheduler_id_for_section("feiniu") == "feiniu"

    def test_fongmi_links_to_scheduler(self):
        assert config_schema.scheduler_id_for_section("fongmi") == "fongmi"

    def test_trakt_no_scheduler(self):
        """trakt 调度器为 instance 类型，配置不联动"""
        assert config_schema.scheduler_id_for_section("trakt") is None

    def test_summary_no_scheduler(self):
        """summary 调度器为 instance 类型，配置联动由 summary_jobs API 直调"""
        assert config_schema.scheduler_id_for_section("summary") is None

    def test_sync_no_scheduler(self):
        assert config_schema.scheduler_id_for_section("sync") is None

    def test_reverse_lookup(self):
        """按调度器 id 反查段名"""
        assert "bangumi-archive" in config_schema.sections_for_scheduler(
            "bangumi_archive"
        )
        assert "bangumi-replay" in config_schema.sections_for_scheduler(
            "bangumi_replay"
        )


class TestMultiInstance:
    """多实例段标记"""

    def test_webhook_is_multi_instance(self):
        assert config_schema.SECTIONS["notify-webhook"].is_multi_instance

    def test_email_is_multi_instance(self):
        assert config_schema.SECTIONS["notify-email"].is_multi_instance

    def test_summary_is_multi_instance(self):
        assert config_schema.SECTIONS["summary"].is_multi_instance

    def test_bangumi_not_multi_instance(self):
        assert not config_schema.SECTIONS["bangumi"].is_multi_instance

    def test_prefixes(self):
        prefixes = set(config_schema.multi_instance_prefixes())
        assert {"notify-webhook", "notify-email", "summary"} <= prefixes


class TestUIVisibility:
    """配置页可见性"""

    def test_bangumi_mapping_hidden(self):
        assert not config_schema.SECTIONS["bangumi-mapping"].visible_in_ui

    def test_bangumi_visible(self):
        assert config_schema.SECTIONS["bangumi"].visible_in_ui

    def test_ui_visible_excludes_hidden(self):
        names = {s.name for s in config_schema.ui_visible_sections()}
        assert "bangumi-mapping" not in names
        assert "bangumi" in names


class TestFieldMeta:
    """字段级元数据（FieldMeta）注册与派生"""

    def test_bangumi_data_fields_registered(self):
        meta = config_schema.SECTIONS["bangumi-data"]
        names = {f.name for f in meta.fields}
        assert {
            "enabled",
            "use_cache",
            "cache_ttl_days",
            "data_url",
            "local_cache_path",
        } <= names

    def test_auth_default_username(self):
        assert config_schema.field_default("auth", "username") == "admin"

    def test_dev_ech_fields_registered(self):
        """[dev] 段 5 个 ECH 字段全部登记（前端表单依赖 schema 驱动回填）。"""
        meta = config_schema.SECTIONS["dev"]
        names = {f.name for f in meta.fields}
        assert {
            "ech_mode",
            "ech_doh_url",
            "ech_doh_use_proxy",
            "ech_hosts",
            "ech_ech_config",
        } <= names
        assert config_schema.field_default("dev", "ech_ech_config") == ""
        assert (
            config_schema.field_default("dev", "ech_doh_url")
            == "https://dns.alidns.com/resolve"
        )

    def test_auth_default_session_timeout(self):
        assert config_schema.field_default("auth", "session_timeout") == 3600

    def test_llm_defaults(self):
        assert config_schema.field_default("llm", "max_tokens") == 2000
        assert config_schema.field_default("llm", "temperature") == 0.7
        assert config_schema.field_default("llm", "timeout") == 60

    def test_feiniu_defaults(self):
        assert config_schema.field_default("feiniu", "min_percent") == 85
        assert config_schema.field_default("feiniu", "limit") == 100
        assert config_schema.field_default("feiniu", "sync_interval") == "*/15 * * * *"

    def test_fongmi_defaults(self):
        assert config_schema.field_default("fongmi", "min_percent") == 80
        assert config_schema.field_default("fongmi", "sync_interval") == "*/3 * * * *"

    def test_archive_defaults(self):
        assert (
            config_schema.field_default("bangumi-archive", "update_cron") == "0 8 * * 3"
        )
        assert (
            config_schema.field_default("bangumi-archive", "data_dir")
            == "./data/archive"
        )
        assert (
            config_schema.field_default("bangumi-archive", "min_disk_space_mb") == 3000
        )

    def test_replay_defaults(self):
        assert (
            config_schema.field_default("bangumi-replay", "api_probe_interval") == 300
        )
        assert config_schema.field_default("bangumi-replay", "replay_batch_size") == 20
        assert config_schema.field_default("bangumi-replay", "max_attempts") == 50

    def test_sync_match_confidence_threshold_default(self):
        """功能三：置信度阈值默认 0.6（0~1 小数）。"""
        assert config_schema.field_default("sync", "match_confidence_threshold") == 0.6

    def test_dev_retention_default(self):
        assert config_schema.field_default("dev", "sync_records_retention_days") == 0

    def test_unregistered_field_returns_none(self):
        assert config_schema.field_default("sync", "nonexistent") is None
        assert config_schema.field_meta("sync", "nonexistent") is None

    def test_field_meta_multi_instance_lookup(self):
        """多实例段前缀匹配查 FieldMeta"""
        # notify-webhook 段当前未登记字段，但应能通过前缀匹配返回 None 而非 KeyError
        assert config_schema.field_meta("notify-webhook-1", "url") is None


class TestDefaultTrueFields:
    """default_true 字段派生（替代 DEFAULT_TRUE_FIELDS）"""

    def test_includes_bangumi_data_enabled(self):
        assert "bangumi_data.enabled" in config_schema.default_true_fields()

    def test_includes_bangumi_data_use_cache(self):
        assert "bangumi_data.use_cache" in config_schema.default_true_fields()

    def test_includes_archive_ssl_verify(self):
        assert "bangumi_archive.ssl_verify" in config_schema.default_true_fields()

    def test_includes_replay_enabled(self):
        assert "bangumi_replay.enabled" in config_schema.default_true_fields()

    def test_includes_auth_enabled(self):
        assert "auth.enabled" in config_schema.default_true_fields()

    def test_uses_underscore_section_name(self):
        """所有路径用下划线形式（匹配前端 form name）"""
        for path in config_schema.default_true_fields():
            assert "-" not in path.split(".")[0], f"路径含连字符: {path}"

    def test_count_matches_legacy(self):
        """default_true 字段：原 5 个 + sync.movie_*(2) + dev.ssl_verify + bangumi-data.*(2) + bangumi-archive.ssl_verify + bangumi-replay.enabled + notify-in-app.in_app_notification + notify-airing-today.enabled + notify-airing-today.only_watching"""
        assert len(config_schema.default_true_fields()) == 11


class TestLooseTrueFields:
    """loose_true 字段派生（替代 STRING_TRUE_FIELDS）"""

    def test_includes_feiniu_enabled(self):
        assert "feiniu.enabled" in config_schema.loose_true_fields()

    def test_includes_fongmi_enabled(self):
        assert "fongmi.enabled" in config_schema.loose_true_fields()

    def test_includes_fongmi_auto_scan(self):
        assert "fongmi.auto_scan" in config_schema.loose_true_fields()

    def test_includes_archive_enabled(self):
        assert "bangumi_archive.enabled" in config_schema.loose_true_fields()

    def test_includes_archive_use_bktree(self):
        assert "bangumi_archive.use_bktree" in config_schema.loose_true_fields()

    def test_uses_underscore_section_name(self):
        for path in config_schema.loose_true_fields():
            assert "-" not in path.split(".")[0], f"路径含连字符: {path}"

    def test_count_matches_legacy(self):
        """原硬编码 4 个 loose_true 字段；ECH 改造新增 dev.ech_doh_use_proxy 第 5 个；
        archive BK-tree 开关新增 bangumi_archive.use_bktree 第 6 个"""
        assert len(config_schema.loose_true_fields()) == 6


class TestConfigDefaults:
    """config_defaults() 派生（替代 CONFIG_DEFAULTS）"""

    def test_includes_bangumi_data_defaults(self):
        cd = config_schema.config_defaults()
        assert cd["bangumi_data"]["cache_ttl_days"] == 7
        assert cd["bangumi_data"]["data_url"].startswith("https://")
        assert cd["bangumi_data"]["local_cache_path"] == "./bangumi_data_cache.json"

    def test_includes_feiniu_defaults(self):
        cd = config_schema.config_defaults()
        assert cd["feiniu"]["min_percent"] == 85
        assert cd["feiniu"]["limit"] == 100
        assert cd["feiniu"]["sync_interval"] == "*/15 * * * *"

    def test_includes_llm_defaults(self):
        cd = config_schema.config_defaults()
        assert cd["llm"]["max_tokens"] == 2000
        assert cd["llm"]["temperature"] == 0.7
        assert cd["llm"]["timeout"] == 60

    def test_excludes_default_true_fields(self):
        """default_true 字段不应出现在 config_defaults 中（避免双重回填）"""
        cd = config_schema.config_defaults()
        # bangumi_data.enabled 是 default_true，不应在 config_defaults
        assert "enabled" not in cd.get("bangumi_data", {})
        # auth.enabled 是 default_true，不应在 config_defaults
        assert "enabled" not in cd.get("auth", {})

    def test_excludes_loose_true_fields(self):
        """loose_true 字段不应出现在 config_defaults 中"""
        cd = config_schema.config_defaults()
        # feiniu.enabled 是 loose_true，不应在 config_defaults
        assert "enabled" not in cd.get("feiniu", {})
        # bangumi_archive.enabled 是 loose_true，不应在 config_defaults
        assert "enabled" not in cd.get("bangumi_archive", {})
        # bangumi_archive.use_bktree 是 loose_true，不应在 config_defaults
        assert "use_bktree" not in cd.get("bangumi_archive", {})

    def test_uses_underscore_section_keys(self):
        """所有 section 键用下划线形式（匹配前端 form name）"""
        for section in config_schema.config_defaults().keys():
            assert "-" not in section, f"段名含连字符: {section}"


class TestSerializeSchema:
    """serialize_schema() 完整序列化"""

    def test_returns_required_top_level_keys(self):
        schema = config_schema.serialize_schema()
        assert "sections" in schema
        assert "config_defaults" in schema
        assert "default_true_fields" in schema
        assert "loose_true_fields" in schema

    def test_sections_sorted_by_order(self):
        schema = config_schema.serialize_schema()
        orders = [s["order"] for s in schema["sections"]]
        assert orders == sorted(orders)

    def test_section_has_name_key_underscore(self):
        """每个段同时提供 name（连字符）和 name_key（下划线）"""
        schema = config_schema.serialize_schema()
        for s in schema["sections"]:
            assert "name" in s
            assert "name_key" in s
            assert "-" not in s["name_key"]

    def test_section_fields_structure(self):
        schema = config_schema.serialize_schema()
        bangumi_data = next(
            s for s in schema["sections"] if s["name"] == "bangumi-data"
        )
        assert "cache_ttl_days" in bangumi_data["fields"]
        field = bangumi_data["fields"]["cache_ttl_days"]
        assert field["default"] == 7
        assert field["default_true"] is False
        assert field["loose_true"] is False

    def test_section_sensitive_fields_sorted(self):
        schema = config_schema.serialize_schema()
        auth = next(s for s in schema["sections"] if s["name"] == "auth")
        assert auth["sensitive_fields"] == ["webhook_key"]

    def test_config_defaults_consistent_with_helper(self):
        """serialize_schema 的 config_defaults 与 config_defaults() 函数一致"""
        schema = config_schema.serialize_schema()
        assert schema["config_defaults"] == config_schema.config_defaults()

    def test_default_true_fields_consistent_with_helper(self):
        schema = config_schema.serialize_schema()
        assert schema["default_true_fields"] == config_schema.default_true_fields()

    def test_loose_true_fields_consistent_with_helper(self):
        schema = config_schema.serialize_schema()
        assert schema["loose_true_fields"] == config_schema.loose_true_fields()
