"""配置段元数据注册表（SectionMeta Registry）

集中登记所有 INI 配置段的元信息，作为配置页 TOC、敏感字段白名单、调度器联动、
通知系统关联的单一真相源。新增配置段只需在此处登记一行，自动覆盖：

- 前端配置页 TOC 与卡片排序
- 敏感字段加密白名单（替代散落的 is_sensitive_ini_field 分支）
- 非多账号段白名单（替代 _BANGUMI_NON_ACCOUNT_SECTIONS）
- 多实例段标记（webhook-N / email-N / summary-*）
- 关联调度器 id（供 SchedulerRegistry 联动）
- 关联通知类型（供 NotificationRegistry 联动）
- env 覆盖映射（替代硬编码 env_overrides 字典）
- 字段默认值与布尔语义（替代前端散落的 CONFIG_DEFAULTS / DEFAULT_TRUE_FIELDS / STRING_TRUE_FIELDS）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FieldMeta:
    """单个配置字段的元数据

    用于驱动前端表单的默认值回填与布尔字段语义。仅在字段需要非空默认值或
    特殊布尔语义时登记，未登记的字段按空字符串/普通 checkbox 处理。
    """

    name: str  # option 名（INI 中的 key），如 "cache_ttl_days"
    # 默认值：populateForm/saveConfig 在字段缺失或空字符串时回填。
    # 类型应与 INI 中存储类型一致（数值用 int/float，字符串用 str）。
    default: Any = None
    # “默认 true”语义：仅当显式 false 时取消勾选（替代 DEFAULT_TRUE_FIELDS）。
    # 与 default=True 的区别：default_true 时 undefined 也视为 true。
    default_true: bool = False
    # 字符串 'true' 兼容：INI 中布尔值可能存为字符串，需宽松匹配（替代 STRING_TRUE_FIELDS）。
    # 与 default_true 互斥：loose_true 时 undefined 视为 false。
    loose_true: bool = False


@dataclass(frozen=True)
class SectionMeta:
    """单个 INI 配置段的元数据"""

    name: str  # 段名，如 "bangumi-archive"
    display_name: str  # 前端展示名，如 "Bangumi Archive"
    order: int = 100  # 前端 TOC 排序权重，越小越靠前

    # 多实例段：section 名带动态后缀（webhook-1 / email-2 / summary-foo）
    is_multi_instance: bool = False

    # 多账号段：bangumi-{username}，保存多账号时会被重建
    is_account_section: bool = False

    # 敏感字段：这些 option 写入 INI 时加密，读取时解密
    sensitive_fields: frozenset[str] = field(default_factory=frozenset)

    # 关联调度器 id（供 SchedulerRegistry 在配置保存后联动）
    scheduler_id: str | None = None

    # 关联通知类型（供 NotificationRegistry 联动，如 summary 任务对应 watching_summary_*）
    notification_types: tuple[str, ...] = ()

    # env 覆盖映射：{option: env_var_name}
    env_overrides: dict[str, str] = field(default_factory=dict)

    # 是否在配置页展示（某些系统段如 bangumi-mapping 不直接展示）
    visible_in_ui: bool = True

    # 字段级元数据：仅登记需要默认值或特殊布尔语义的字段
    fields: tuple[FieldMeta, ...] = ()


# ── 段元数据注册表 ────────────────────────────────────────────────────────
# 注意：多账号段 bangumi-{username} 用 is_account_section=True 标记，
# 保存多账号时通过此标记区分哪些段需重建。

SECTIONS: dict[str, SectionMeta] = {
    # ── 核心配置（order 10-90）──
    "bangumi": SectionMeta(
        name="bangumi",
        display_name="Bangumi 账号",
        order=10,
        sensitive_fields=frozenset({"access_token", "refresh_token"}),
        env_overrides={
            "username": "BANGUMI_USERNAME",
            "access_token": "BANGUMI_ACCESS_TOKEN",
            "media_server_username": "SINGLE_USERNAME",
            "private": "BANGUMI_PRIVATE",
        },
    ),
    "bangumi-oauth": SectionMeta(
        name="bangumi-oauth",
        display_name="Bangumi OAuth 应用",
        order=11,
        # 应用凭证（client_id/client_secret）用于完成 Bangumi 官方 OAuth 授权流。
        # 该段非多账号段，需从账号段探测中排除。
        sensitive_fields=frozenset({"client_secret"}),
        visible_in_ui=False,
        env_overrides={
            "client_id": "BANGUMI_OAUTH_CLIENT_ID",
            "client_secret": "BANGUMI_OAUTH_CLIENT_SECRET",
        },
        fields=(
            FieldMeta(name="client_id", default=""),
            FieldMeta(name="client_secret", default=""),
            FieldMeta(name="redirect_uri", default=""),
        ),
    ),
    "sync": SectionMeta(
        name="sync",
        display_name="同步设置",
        order=20,
        fields=(
            FieldMeta(name="movie_playback_start_mark_watching", default_true=True),
            FieldMeta(name="movie_mark_subject_completed", default_true=True),
            # 模糊匹配置信度阈值（0~1）：低于该相似度的 Bangumi API 匹配
            # 不会自动采用，而是沉淀到待审队列由用户在 Web 界面人工确认。
            FieldMeta(name="match_confidence_threshold", default=0.6),
        ),
    ),
    "auth": SectionMeta(
        name="auth",
        display_name="Web 认证",
        order=30,
        sensitive_fields=frozenset({"webhook_key"}),
        fields=(
            FieldMeta(name="username", default="admin"),
            FieldMeta(name="session_timeout", default=3600),
            FieldMeta(name="max_login_attempts", default=5),
            FieldMeta(name="lockout_duration", default=900),
            FieldMeta(name="enabled", default_true=True),
        ),
    ),
    "web": SectionMeta(
        name="web",
        display_name="Web / 反向代理",
        order=40,
        env_overrides={"base_path": "APPLICATION_ROOT"},
    ),
    "dev": SectionMeta(
        name="dev",
        display_name="开发与代理",
        order=50,
        env_overrides={
            "script_proxy": "HTTP_PROXY",
            "debug": "DEBUG_MODE",
            "log_level": "LOG_LEVEL",
        },
        fields=(
            FieldMeta(name="sync_records_retention_days", default=0),
            FieldMeta(name="ssl_verify", default_true=True),
            FieldMeta(name="log_level", default="INFO"),
            FieldMeta(name="ech_mode", default="off"),
            FieldMeta(name="ech_doh_url", default="https://dns.alidns.com/resolve"),
            FieldMeta(name="ech_doh_use_proxy", loose_true=True),
            FieldMeta(
                name="ech_hosts",
                default="bgm.tv,chii.in,next.bgm.tv,lain.bgm.tv",
            ),
            FieldMeta(name="ech_ech_config", default=""),
        ),
    ),
    # ── 媒体源驱动（order 100-199）──
    "feiniu": SectionMeta(
        name="feiniu",
        display_name="飞牛影视",
        order=100,
        scheduler_id="feiniu",
        env_overrides={"db_path": "FEINIU_DB_PATH"},
        fields=(
            FieldMeta(name="enabled", loose_true=True),
            FieldMeta(name="min_percent", default=85),
            FieldMeta(name="limit", default=100),
            FieldMeta(name="user_filter", default="all"),
            FieldMeta(name="time_range", default="all"),
            FieldMeta(name="sync_interval", default="*/15 * * * *"),
        ),
    ),
    "fongmi": SectionMeta(
        name="fongmi",
        display_name="fongmi 局域网轮询",
        order=110,
        scheduler_id="fongmi",
        env_overrides={
            "enabled": "FONGMI_ENABLED",
            "devices": "FONGMI_DEVICES",
            "subnet": "FONGMI_SUBNET",
            "auto_scan": "FONGMI_AUTO_SCAN",
            "sync_interval": "FONGMI_SYNC_INTERVAL",
            "min_percent": "FONGMI_MIN_PERCENT",
        },
        fields=(
            FieldMeta(name="enabled", loose_true=True),
            FieldMeta(name="auto_scan", loose_true=True),
            FieldMeta(name="min_percent", default=80),
            FieldMeta(name="sync_interval", default="*/3 * * * *"),
        ),
    ),
    "trakt": SectionMeta(
        name="trakt",
        display_name="Trakt 同步",
        order=120,
        sensitive_fields=frozenset({"client_secret"}),
        # trakt 调度器为 instance 类型，配置变更需重启或通过专用 API 生效
    ),
    # ── Bangumi 容灾（order 200-299）──
    "bangumi-data": SectionMeta(
        name="bangumi-data",
        display_name="Bangumi Data 离线匹配",
        order=200,
        fields=(
            FieldMeta(name="enabled", default_true=True),
            FieldMeta(name="use_cache", default_true=True),
            FieldMeta(name="cache_ttl_days", default=7),
            FieldMeta(
                name="data_url",
                default="https://unpkg.com/bangumi-data@0.3/dist/data.json",
            ),
            FieldMeta(name="local_cache_path", default="./bangumi_data_cache.json"),
        ),
    ),
    "bangumi-mapping": SectionMeta(
        name="bangumi-mapping",
        display_name="自定义映射",
        order=210,
        visible_in_ui=False,  # 通过 /mappings 页面单独管理
    ),
    "bangumi-archive": SectionMeta(
        name="bangumi-archive",
        display_name="Bangumi Archive",
        order=220,
        scheduler_id="bangumi_archive",
        fields=(
            FieldMeta(name="enabled", loose_true=True),
            FieldMeta(name="ssl_verify", default_true=True),
            FieldMeta(name="update_cron", default="0 8 * * 3"),
            FieldMeta(name="data_dir", default="./data/archive"),
            FieldMeta(name="min_disk_space_mb", default=3000),
            # BK-tree 模糊匹配开关：默认关闭，开启后对归档标题构建 BK-tree
            # 索引以支持编辑距离模糊查询，提升形近/缺字标题的召回率。
            FieldMeta(name="use_bktree", loose_true=True),
        ),
    ),
    "bangumi-replay": SectionMeta(
        name="bangumi-replay",
        display_name="Bangumi Replay 补发",
        order=230,
        scheduler_id="bangumi_replay",
        fields=(
            FieldMeta(name="enabled", default_true=True),
            FieldMeta(name="api_probe_interval", default=300),
            FieldMeta(name="replay_cron", default="*/10 * * * *"),
            FieldMeta(name="replay_batch_size", default=20),
            FieldMeta(name="max_attempts", default=50),
        ),
    ),
    # ── 通知配置（order 500-599，多实例）──
    "notify-webhook": SectionMeta(
        name="notify-webhook",
        display_name="Webhook 通知",
        order=500,
        is_multi_instance=True,
    ),
    "notify-email": SectionMeta(
        name="notify-email",
        display_name="邮件通知",
        order=510,
        is_multi_instance=True,
        sensitive_fields=frozenset({"smtp_password"}),
    ),
    "notify-wecom": SectionMeta(
        name="notify-wecom",
        display_name="企业微信通知",
        order=520,
        is_multi_instance=True,
        sensitive_fields=frozenset({"key"}),
    ),
    "notify-dingtalk": SectionMeta(
        name="notify-dingtalk",
        display_name="钉钉通知",
        order=530,
        is_multi_instance=True,
        sensitive_fields=frozenset({"access_token", "secret"}),
    ),
    "notify-in-app": SectionMeta(
        name="notify-in-app",
        display_name="站内信",
        order=535,
        fields=(FieldMeta(name="in_app_notification", default_true=True),),
    ),
    "notify-rule": SectionMeta(
        name="notify-rule",
        display_name="通知规则",
        order=540,
        is_multi_instance=True,
    ),
    "notify-airing-today": SectionMeta(
        name="notify-airing-today",
        display_name="今日放送提醒",
        order=545,
        scheduler_id="airing_today",
        # 该段驱动的通知类型：成功放送提醒 + 任务失败告警
        notification_types=("airing_today", "scheduler_job_failed"),
        fields=(
            FieldMeta(name="enabled", default_true=True),
            FieldMeta(name="cron", default="0 9 * * *"),
            FieldMeta(name="only_watching", default_true=True),
        ),
    ),
    # ── AI 总结（order 600-699，多实例）──
    "summary": SectionMeta(
        name="summary",
        display_name="AI 追番总结",
        order=600,
        is_multi_instance=True,
        # summary 调度器为 instance 类型，配置联动由 summary_jobs API 直调
    ),
    "llm": SectionMeta(
        name="llm",
        display_name="LLM 配置",
        order=610,
        sensitive_fields=frozenset({"api_key"}),
        fields=(
            FieldMeta(name="provider", default="openai_compat"),
            FieldMeta(name="max_tokens", default=2000),
            FieldMeta(name="temperature", default=0.7),
            FieldMeta(name="timeout", default=60),
            FieldMeta(name="thinking_level", default="off"),
        ),
    ),
    # ── 调度器全局（order 900）──
    "scheduler": SectionMeta(
        name="scheduler",
        display_name="调度器全局",
        order=900,
    ),
}


# ── 派生查询接口 ──────────────────────────────────────────────────────────


def get_section_meta(name: str) -> SectionMeta | None:
    """按段名查元数据"""
    return SECTIONS.get(name)


def all_sections() -> list[SectionMeta]:
    """所有已注册段（按 order 排序）"""
    return sorted(SECTIONS.values(), key=lambda s: s.order)


def ui_visible_sections() -> list[SectionMeta]:
    """配置页可见段（按 order 排序）"""
    return [s for s in all_sections() if s.visible_in_ui]


def multi_instance_prefixes() -> tuple[str, ...]:
    """所有多实例段前缀（webhook / email / summary）"""
    return tuple(s.name for s in SECTIONS.values() if s.is_multi_instance)


def account_section_prefixes() -> tuple[str, ...]:
    """所有多账号段前缀（当前仅 bangumi-）

    注意：bangumi- 同时被 bangumi-data / bangumi-mapping / bangumi-archive /
    bangumi-replay 使用，这些不是多账号段，通过 is_account_section=False 区分。
    """
    return tuple(s.name for s in SECTIONS.values() if s.is_account_section)


def non_account_bangumi_sections() -> tuple[str, ...]:
    """以 bangumi- 开头但非多账号的系统功能段（替代 _BANGUMI_NON_ACCOUNT_SECTIONS）

    用于 get_bangumi_configs / get_all_config 收集多账号时排除这些段。
    """
    return tuple(
        s.name
        for s in SECTIONS.values()
        if s.name.startswith("bangumi-") and not s.is_account_section
    )


def all_sensitive_fields() -> dict[str, frozenset[str]]:
    """所有段的敏感字段映射 {section: {field1, field2, ...}}

    多实例段（webhook/email）的敏感字段适用于所有 webhook-N / email-N 段。
    """
    return {s.name: s.sensitive_fields for s in SECTIONS.values() if s.sensitive_fields}


def all_env_overrides() -> dict[tuple[str, str], str]:
    """所有 env 覆盖映射 {（section, option): env_var_name}"""
    result: dict[tuple[str, str], str] = {}
    for s in SECTIONS.values():
        for option, env_var in s.env_overrides.items():
            result[(s.name, option)] = env_var
    return result


def scheduler_id_for_section(section: str) -> str | None:
    """按段名查关联的调度器 id"""
    meta = SECTIONS.get(section)
    return meta.scheduler_id if meta else None


def sections_for_scheduler(scheduler_id: str) -> list[str]:
    """按调度器 id 反查关联的配置段名"""
    return [s.name for s in SECTIONS.values() if s.scheduler_id == scheduler_id]


def is_sensitive_field(section: str, option: str) -> bool:
    """判断某字段是否敏感（替代 is_sensitive_ini_field 的散落分支）

    支持多实例段：webhook-1 / email-2 等通过前缀匹配父段 sensitive_fields。
    """
    # 直接命中
    meta = SECTIONS.get(section)
    if meta and option in meta.sensitive_fields:
        return True
    # 多实例段前缀匹配：webhook-1 → webhook, email-2 → email
    for prefix in multi_instance_prefixes():
        if (
            section.startswith(f"{prefix}-")
            and option in SECTIONS[prefix].sensitive_fields
        ):
            return True
    # 多账号段 bangumi-{username} → bangumi
    if section.startswith("bangumi-") and section not in non_account_bangumi_sections():
        if option in SECTIONS["bangumi"].sensitive_fields:
            return True
    return False


# ── 字段级元数据查询 / 序列化 ───────────────────────────────────────────────


def _normalize_section_name(section: str) -> str:
    """将段名中的连字符替换为下划线，匹配前端 form name 中的 section 部分。

    后端 INI 段名用连字符（bangumi-data），前端 form name 用下划线
    （bangumi_data.cache_ttl_days），序列化给前端时需统一为下划线。
    """
    return section.replace("-", "_")


def field_meta(section: str, option: str) -> FieldMeta | None:
    """按段名 + option 名查字段元数据

    支持多实例段：notify-webhook-1 → notify-webhook。
    """
    meta = SECTIONS.get(section)
    if meta:
        for f in meta.fields:
            if f.name == option:
                return f
    # 多实例段前缀匹配
    for prefix in multi_instance_prefixes():
        if section.startswith(f"{prefix}-"):
            parent = SECTIONS.get(prefix)
            if parent:
                for f in parent.fields:
                    if f.name == option:
                        return f
    return None


def field_default(section: str, option: str) -> Any:
    """按段名 + option 名查默认值，无登记返回 None"""
    fm = field_meta(section, option)
    return fm.default if fm else None


def default_true_fields() -> list[str]:
    """所有 default_true 字段，返回 "section.option" 路径列表（section 用下划线形式）

    替代前端散落的 DEFAULT_TRUE_FIELDS 字典。
    """
    result: list[str] = []
    for s in SECTIONS.values():
        for f in s.fields:
            if f.default_true:
                result.append(f"{_normalize_section_name(s.name)}.{f.name}")
    return result


def loose_true_fields() -> list[str]:
    """所有 loose_true 字段，返回 "section.option" 路径列表（section 用下划线形式）

    替代前端散落的 STRING_TRUE_FIELDS 字典。
    """
    result: list[str] = []
    for s in SECTIONS.values():
        for f in s.fields:
            if f.loose_true:
                result.append(f"{_normalize_section_name(s.name)}.{f.name}")
    return result


def config_defaults() -> dict[str, dict[str, Any]]:
    """所有字段默认值映射 {section: {option: default}}（section 用下划线形式）

    替代前端散落的 CONFIG_DEFAULTS 字典。仅包含显式登记 default（非 None）的字段。
    """
    result: dict[str, dict[str, Any]] = {}
    for s in SECTIONS.values():
        for f in s.fields:
            if f.default is not None and not f.default_true and not f.loose_true:
                key = _normalize_section_name(s.name)
                result.setdefault(key, {})[f.name] = f.default
    return result


def serialize_schema() -> dict[str, Any]:
    """将 SectionMeta 注册表序列化为前端可消费的 JSON 结构

    返回结构：
    ```
    {
        "sections": [
            {
                "name": "bangumi-data",          # 原始段名（连字符）
                "name_key": "bangumi_data",      # 下划线形式，匹配前端 form name
                "display_name": "Bangumi Data 离线匹配",
                "order": 200,
                "is_multi_instance": false,
                "is_account_section": false,
                "scheduler_id": null,
                "visible_in_ui": true,
                "sensitive_fields": [],
                "fields": {
                    "cache_ttl_days": {"default": 7, "default_true": false, "loose_true": false},
                    "enabled": {"default": null, "default_true": true, "loose_true": false},
                    ...
                }
            },
            ...
        ],
        "config_defaults": {"bangumi_data": {"cache_ttl_days": 7, ...}, ...},
        "default_true_fields": ["bangumi_data.enabled", ...],
        "loose_true_fields": ["feiniu.enabled", ...],
    }
    ```
    """
    sections = []
    for s in all_sections():
        sections.append(
            {
                "name": s.name,
                "name_key": _normalize_section_name(s.name),
                "display_name": s.display_name,
                "order": s.order,
                "is_multi_instance": s.is_multi_instance,
                "is_account_section": s.is_account_section,
                "scheduler_id": s.scheduler_id,
                "visible_in_ui": s.visible_in_ui,
                "sensitive_fields": sorted(s.sensitive_fields),
                "fields": {
                    f.name: {
                        "default": f.default,
                        "default_true": f.default_true,
                        "loose_true": f.loose_true,
                    }
                    for f in s.fields
                },
            }
        )
    return {
        "sections": sections,
        "config_defaults": config_defaults(),
        "default_true_fields": default_true_fields(),
        "loose_true_fields": loose_true_fields(),
    }
