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
"""

from __future__ import annotations

from dataclasses import dataclass, field


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


# ── 段元数据注册表 ────────────────────────────────────────────────────────
# 注意：多账号段 bangumi-{username} 用 is_account_section=True 标记，
# 保存多账号时通过此标记区分哪些段需重建。

SECTIONS: dict[str, SectionMeta] = {
    # ── 核心配置（order 10-90）──
    "bangumi": SectionMeta(
        name="bangumi",
        display_name="Bangumi 账号",
        order=10,
        sensitive_fields=frozenset({"access_token"}),
        env_overrides={
            "username": "BANGUMI_USERNAME",
            "access_token": "BANGUMI_ACCESS_TOKEN",
            "media_server_username": "SINGLE_USERNAME",
            "private": "BANGUMI_PRIVATE",
        },
    ),
    "sync": SectionMeta(
        name="sync",
        display_name="同步设置",
        order=20,
    ),
    "auth": SectionMeta(
        name="auth",
        display_name="Web 认证",
        order=30,
        sensitive_fields=frozenset({"webhook_key"}),
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
        },
    ),
    # ── 媒体源驱动（order 100-199）──
    "feiniu": SectionMeta(
        name="feiniu",
        display_name="飞牛影视",
        order=100,
        scheduler_id="feiniu",
        env_overrides={"db_path": "FEINIU_DB_PATH"},
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
    ),
    "trakt": SectionMeta(
        name="trakt",
        display_name="Trakt 同步",
        order=120,
        sensitive_fields=frozenset({"client_secret"}),
        scheduler_id="trakt",
    ),
    # ── Bangumi 容灾（order 200-299）──
    "bangumi-data": SectionMeta(
        name="bangumi-data",
        display_name="Bangumi Data 离线匹配",
        order=200,
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
    ),
    "bangumi-replay": SectionMeta(
        name="bangumi-replay",
        display_name="Bangumi Replay 补发",
        order=230,
        scheduler_id="bangumi_replay",
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
    # ── AI 总结（order 600-699，多实例）──
    "summary": SectionMeta(
        name="summary",
        display_name="AI 追番总结",
        order=600,
        is_multi_instance=True,
        scheduler_id="summary",
    ),
    "llm": SectionMeta(
        name="llm",
        display_name="LLM 配置",
        order=610,
        sensitive_fields=frozenset({"api_key"}),
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
