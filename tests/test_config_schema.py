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
        assert overrides[("web", "base_path")] == "APPLICATION_ROOT"

    def test_count_matches_original(self):
        """原硬编码共 14 条映射"""
        overrides = config_schema.all_env_overrides()
        assert len(overrides) == 14


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

    def test_trakt_links_to_scheduler(self):
        assert config_schema.scheduler_id_for_section("trakt") == "trakt"

    def test_summary_links_to_scheduler(self):
        assert config_schema.scheduler_id_for_section("summary") == "summary"

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
