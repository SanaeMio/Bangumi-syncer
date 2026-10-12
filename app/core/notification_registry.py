"""通知类型注册表（NotificationTypeRegistry）

集中登记所有通知类型的元信息，作为类型展示、前端复选框、站内信映射、
冷却分桶的单一真相源。新增通知类型只需在此处登记一行，自动覆盖：

- 前端配置页类型复选框（通过 /api/notification/types 动态加载）
- 邮件 HTML 的颜色/图标/标题（type_config）
- 邮件标题模板（subjects）
- 纯文本描述（type_descriptions）
- 站内信 type 映射（mark_failed ↔ sync_failed）
- 冷却分桶（is_item_level）

watching_summary_{name} 作为动态类型处理：Registry 注册通配元数据，
channel 层通过 ``watching_summary`` 前缀匹配。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationTypeMeta:
    """单个通知类型的元数据"""

    id: str  # 类型标识，如 "mark_failed"
    display_name: str  # 展示名，如 "同步失败"
    icon: str  # emoji 图标，如 "❌"
    color: str  # 颜色 hex，如 "#dc3545"
    description: str  # 纯文本描述
    is_item_level: bool = False  # 是否按 item 维度冷却（title+season+episode）
    category: str = ""  # 分类标识，如 "sync_flow" / "match_quality" / "data_source"

    # 站内信映射：该类型触发站内信时使用的 type 和标题模板
    # None 表示不触发站内信；如 "sync_failed" 表示写站内信时 type=sync_failed
    in_app_type: str | None = None
    in_app_title_template: str | None = None  # 如 "同步失败：{title} {ep_label}"

    # 该类型在 webhook 默认模板中的额外字段（占位符 → data 键名）。
    # 用于补足通用模板无法表达的、类型特有的信息（如追番总结的 summary）。
    # 由 default_payload_fields() 汇总给模板渲染器，避免类型知识散落在
    # 各渠道实现里。
    payload_fields: tuple[tuple[str, str], ...] = ()

    # 该类型 payload / 邮件主题标题的模板（占位符形如 {job_name}）。
    # 留空时回退为 "{icon} {display_name}"。需要在标题里区分同一类型的
    # 多个实例时（如多个追番总结任务），在此声明。
    title_template: str | None = None

    # 是否在配置页类型选择列表中展示（某些内部类型如 sync_queued 也可展示）
    visible_in_ui: bool = True

    def default_payload_fields(self) -> dict[str, str]:
        """该类型附加到 webhook payload 的字段（占位符名 → data 键名）。"""
        return dict(self.payload_fields)

    def default_title(self) -> str:
        """默认标题模板（含图标），用于 webhook payload 的 title 字段。"""
        tpl = self.title_template or "{type_display_name}"
        return f"{self.icon} {tpl}"


# ── 事件分类 ──────────────────────────────────────────────────────────────

CATEGORIES: dict[str, str] = {
    "sync_flow": "同步流程",
    "match_quality": "匹配质量",
    "data_source": "数据源",
    "scheduler": "调度任务",
    "bangumi_api": "Bangumi API",
    "system": "系统运维",
}


# ── 动态类型前缀 ──────────────────────────────────────────────────────────

WATCHING_SUMMARY_PREFIX = "watching_summary"

# watching_summary_{name} 的通配元数据
_WATCHING_SUMMARY_META = NotificationTypeMeta(
    id=WATCHING_SUMMARY_PREFIX,
    display_name="追番总结",
    icon="📊",
    color="#6c8ebf",
    description="成功时发送总结正文，失败时发送失败提示",
    is_item_level=False,
    # 通配类型不进入静态列表；具体任务类型 watching_summary_{name}
    # 由 /api/notification/types 端点按 summary 任务动态附加
    visible_in_ui=False,
    category="scheduler",
    # 总结正文是这类通知的核心内容，通用模板无法表达，必须随类型携带
    payload_fields=(
        ("job_name", "job_name"),
        ("summary", "summary_text"),
        ("date_range", "date_range"),
        ("record_count", "record_count"),
        ("model", "model"),
        ("tokens_used", "tokens_used"),
    ),
    # 同一类型的多个总结任务靠 job_name 区分，必须出现在标题里
    title_template="追番总结 - {job_name}",
)


# ── 类型元数据注册表 ──────────────────────────────────────────────────────

_TYPES: dict[str, NotificationTypeMeta] = {
    # ═════════════════ 同步流程 ═════════════════
    "request_received": NotificationTypeMeta(
        id="request_received",
        display_name="收到同步请求",
        icon="📥",
        color="#0d6efd",
        description="媒体库推送的播放事件到达",
        is_item_level=True,
        category="sync_flow",
        title_template="收到同步请求 - {title} {ep_label}",
    ),
    "bangumi_id_found": NotificationTypeMeta(
        id="bangumi_id_found",
        display_name="匹配到番剧",
        icon="🔍",
        color="#198754",
        description="成功匹配到 Bangumi 条目",
        is_item_level=True,
        category="sync_flow",
        payload_fields=(("subject_id", "subject_id"), ("bgm_title", "bgm_title")),
        title_template="匹配到番剧 - {title}",
    ),
    "mark_success": NotificationTypeMeta(
        id="mark_success",
        display_name="同步成功",
        icon="✅",
        color="#198754",
        description="已标记为已看",
        is_item_level=True,
        category="sync_flow",
        payload_fields=(
            ("subject_id", "subject_id"),
            ("episode_id", "episode_id"),
            ("bgm_title", "bgm_title"),
            ("bgm_username", "bgm_username"),
        ),
        title_template="同步成功 - {title} {ep_label}",
    ),
    "mark_failed": NotificationTypeMeta(
        id="mark_failed",
        display_name="同步失败",
        icon="❌",
        color="#dc3545",
        description="标记失败或处理异常",
        is_item_level=True,
        category="sync_flow",
        in_app_type="sync_failed",
        in_app_title_template="同步失败：{title} {ep_label}",
        payload_fields=(
            ("error_type", "error_type"),
            ("additional_info", "additional_info"),
        ),
        title_template="同步失败 - {title} {ep_label}",
    ),
    "mark_skipped": NotificationTypeMeta(
        id="mark_skipped",
        display_name="已看过跳过",
        icon="⏭️",
        color="#6c757d",
        description="重复标记，已看过不再处理",
        is_item_level=True,
        category="sync_flow",
        payload_fields=(
            ("subject_id", "subject_id"),
            ("episode_id", "episode_id"),
            ("bgm_title", "bgm_title"),
            ("bgm_username", "bgm_username"),
        ),
        title_template="已看过 - {title} {ep_label}",
    ),
    "sync_queued": NotificationTypeMeta(
        id="sync_queued",
        display_name="API不可达入队",
        icon="📋",
        color="#6c8ebf",
        description="Bangumi API 不可达，暂存待补发",
        is_item_level=True,
        category="sync_flow",
        payload_fields=(
            ("subject_id", "subject_id"),
            ("episode_id", "episode_id"),
            ("bgm_title", "bgm_title"),
        ),
        title_template="API不可达入队 - {title} {ep_label}",
    ),
    "sync_replayed": NotificationTypeMeta(
        id="sync_replayed",
        display_name="队列补发成功",
        icon="📤",
        color="#198754",
        description="API 恢复后补发成功",
        is_item_level=True,
        category="sync_flow",
        payload_fields=(
            ("subject_id", "subject_id"),
            ("episode_id", "episode_id"),
            ("mark_status", "mark_status"),
        ),
        title_template="队列补发成功 - {title} {ep_label}",
    ),
    # ═════════════════ 匹配质量 ═════════════════
    "anime_not_found": NotificationTypeMeta(
        id="anime_not_found",
        display_name="未找到番剧",
        icon="🔍",
        color="#fd7e14",
        description="搜索无结果，无法匹配",
        is_item_level=False,
        category="match_quality",
        in_app_type="sync_failed",
        in_app_title_template="同步失败：{title} {ep_label}",
        title_template="未找到番剧 - {title}",
    ),
    "episode_not_found": NotificationTypeMeta(
        id="episode_not_found",
        display_name="未找到剧集",
        icon="📺",
        color="#fd7e14",
        description="番剧已匹配但指定剧集不存在",
        is_item_level=False,
        category="match_quality",
        in_app_type="sync_failed",
        in_app_title_template="同步失败：{title} {ep_label}",
        title_template="未找到剧集 - {title} {ep_label}",
    ),
    "pending_candidate": NotificationTypeMeta(
        id="pending_candidate",
        display_name="候选待确认",
        icon="📝",
        color="#fd7e14",
        description="匹配失败但有候选，需手动确认",
        is_item_level=True,
        category="match_quality",
        payload_fields=(
            ("candidates_count", "candidates_count"),
            ("top_candidate_id", "top_candidate_id"),
            ("top_candidate_name", "top_candidate_name"),
        ),
        title_template="候选待确认 - {title} {ep_label}",
    ),
    "match_ambiguous": NotificationTypeMeta(
        id="match_ambiguous",
        display_name="匹配歧义",
        icon="🤔",
        color="#fd7e14",
        description="候选分数接近，已选最高分但存疑",
        is_item_level=True,
        category="match_quality",
        payload_fields=(
            ("final_subject_id", "final_subject_id"),
            ("top1_name", "top1_name"),
            ("top1_subject_id", "top1_subject_id"),
            ("top1_score", "top1_score"),
            ("top2_name", "top2_name"),
            ("top2_subject_id", "top2_subject_id"),
            ("top2_score", "top2_score"),
            ("score_diff", "score_diff"),
        ),
        title_template="匹配歧义 - {title} {ep_label}",
    ),
    # ═════════════════ 数据源 ═════════════════
    "source_fetch_failed": NotificationTypeMeta(
        id="source_fetch_failed",
        display_name="数据源拉取失败",
        icon="🚫",
        color="#dc3545",
        description="Trakt/飞牛/FongMi 拉取观看记录失败",
        is_item_level=False,
        category="data_source",
    ),
    "source_fetch_empty": NotificationTypeMeta(
        id="source_fetch_empty",
        display_name="数据源返回空",
        icon="📭",
        color="#fd7e14",
        description="拉取到 0 条记录，可能配置错误",
        is_item_level=False,
        category="data_source",
    ),
    # ═════════════════ 调度任务 ═════════════════
    "scheduler_job_failed": NotificationTypeMeta(
        id="scheduler_job_failed",
        display_name="定时任务失败",
        icon="⏰",
        color="#dc3545",
        description="archive/replay/feiniu/fongmi/trakt/summary 异常",
        is_item_level=False,
        category="scheduler",
        payload_fields=(("driver", "driver"), ("is_timeout", "is_timeout")),
    ),
    "batch_sync_summary": NotificationTypeMeta(
        id="batch_sync_summary",
        display_name="批量同步汇总",
        icon="📦",
        color="#0d6efd",
        description="一批同步结束的成功/失败/跳过统计",
        is_item_level=False,
        category="scheduler",
        payload_fields=(
            ("total", "total"),
            ("succeeded", "succeeded"),
            ("failed", "failed"),
            ("skipped", "skipped"),
        ),
    ),
    "queue_size_warning": NotificationTypeMeta(
        id="queue_size_warning",
        display_name="队列堆积预警",
        icon="📨",
        color="#ffc107",
        description="待同步队列超过阈值（默认 100 条）",
        is_item_level=False,
        category="scheduler",
        payload_fields=(("pending_count", "pending_count"), ("threshold", "threshold")),
    ),
    "airing_today": NotificationTypeMeta(
        id="airing_today",
        display_name="今日放送提醒",
        icon="📺",
        color="#0d6efd",
        description="每日定时汇总当日放送的番剧章节(archive启用才可用)",
        is_item_level=False,
        category="scheduler",
        # 写站内信，标题带放送日期与集数（"我的追番"定位下的简洁提示）
        in_app_type="airing_today",
        in_app_title_template="今日放送 {total} 集（{airdate}）",
        payload_fields=(("airdate", "airdate"), ("total", "total")),
    ),
    # ═════════════════ Bangumi API ═════════════════
    "api_error": NotificationTypeMeta(
        id="api_error",
        display_name="API错误",
        icon="🌐",
        color="#dc3545",
        description="Bangumi API 返回 5xx 或 429",
        is_item_level=False,
        category="bangumi_api",
        payload_fields=(("status_code", "status_code"),),
    ),
    "api_auth_error": NotificationTypeMeta(
        id="api_auth_error",
        display_name="API认证失败",
        icon="🔑",
        color="#dc3545",
        description="Bangumi API 返回 401",
        is_item_level=False,
        category="bangumi_api",
        payload_fields=(("status_code", "status_code"),),
    ),
    "api_retry_failed": NotificationTypeMeta(
        id="api_retry_failed",
        display_name="API重试失败",
        icon="🔄",
        color="#dc3545",
        description="重试耗尽仍失败",
        is_item_level=False,
        category="bangumi_api",
        payload_fields=(
            ("status_code", "status_code"),
            ("url", "url"),
            ("method", "method"),
            ("retry_count", "retry_count"),
        ),
    ),
    "bangumi_token_expired": NotificationTypeMeta(
        id="bangumi_token_expired",
        display_name="Token已过期",
        icon="🔑",
        color="#dc3545",
        description="access_token 过期，需更新",
        is_item_level=False,
        category="bangumi_api",
        payload_fields=(
            ("user_name", "user_name"),
            ("status_code", "status_code"),
        ),
    ),
    # ═════════════════ 系统运维 ═════════════════
    "config_error": NotificationTypeMeta(
        id="config_error",
        display_name="配置错误",
        icon="⚙️",
        color="#ffc107",
        description="配置项缺失或无效，同步无法继续",
        is_item_level=False,
        category="system",
    ),
    "ip_locked": NotificationTypeMeta(
        id="ip_locked",
        display_name="IP被锁定",
        icon="🔒",
        color="#dc3545",
        description="登录失败次数过多，IP 临时锁定",
        is_item_level=False,
        category="system",
        payload_fields=(
            ("ip", "ip"),
            ("locked_until", "locked_until"),
            ("attempt_count", "attempt_count"),
            ("max_attempts", "max_attempts"),
        ),
    ),
    "auth_login_failed": NotificationTypeMeta(
        id="auth_login_failed",
        display_name="Web登录失败",
        icon="🔒",
        color="#dc3545",
        description="用户名或密码错误",
        is_item_level=False,
        category="system",
        payload_fields=(("ip", "ip"),),
    ),
    "app_upgrade_available": NotificationTypeMeta(
        id="app_upgrade_available",
        display_name="新版本可用",
        icon="🆕",
        color="#198754",
        description="检测到 Bangumi-syncer 新版本",
        is_item_level=False,
        category="system",
        payload_fields=(
            ("current_version", "current_version"),
            ("latest_version", "latest_version"),
        ),
    ),
    "archive_build_failed": NotificationTypeMeta(
        id="archive_build_failed",
        display_name="档案库构建失败",
        icon="🗄️",
        color="#dc3545",
        description="番组档案库导入或索引构建失败",
        is_item_level=False,
        category="system",
        payload_fields=(("task_id", "task_id"),),
    ),
    "archive_disk_warning": NotificationTypeMeta(
        id="archive_disk_warning",
        display_name="档案库磁盘预警",
        icon="💾",
        color="#ffc107",
        description="档案库磁盘可用空间接近阈值",
        is_item_level=False,
        category="system",
        payload_fields=(
            ("available_mb", "available_mb"),
            ("required_mb", "required_mb"),
            ("warning_threshold_mb", "warning_threshold_mb"),
        ),
    ),
    # ═════════════════ 站内信专用（不在前端选择列表展示）═════════════════
    "sync_failed": NotificationTypeMeta(
        id="sync_failed",
        display_name="同步失败",
        icon="❌",
        color="#dc3545",
        description="站内信专用，对应 webhook/email 的 mark_failed",
        is_item_level=False,
        visible_in_ui=False,
    ),
    "summary_llm_failed": NotificationTypeMeta(
        id="summary_llm_failed",
        display_name="总结LLM失败",
        icon="🤖",
        color="#dc3545",
        description="LLM 返回空内容",
        is_item_level=False,
        visible_in_ui=False,
        payload_fields=(("job_name", "job_name"), ("model", "model")),
    ),
    "summary_job_failed": NotificationTypeMeta(
        id="summary_job_failed",
        display_name="总结任务失败",
        icon="⚠️",
        color="#dc3545",
        description="Summary 任务执行异常",
        is_item_level=False,
        visible_in_ui=False,
        payload_fields=(("job_name", "job_name"),),
    ),
}


# ── 派生查询接口 ──────────────────────────────────────────────────────────


def get_type_meta(type_id: str) -> NotificationTypeMeta | None:
    """按 id 查类型元数据

    支持 watching_summary_{name} 动态类型：返回通配元数据。
    """
    if type_id.startswith(WATCHING_SUMMARY_PREFIX):
        return _WATCHING_SUMMARY_META
    return _TYPES.get(type_id)


def all_types() -> list[NotificationTypeMeta]:
    """所有已注册类型（含 watching_summary 通配）"""
    return list(_TYPES.values()) + [_WATCHING_SUMMARY_META]


def ui_visible_types() -> list[NotificationTypeMeta]:
    """配置页可见类型（按 id 排序，用于前端复选框）"""
    return sorted(
        [t for t in _TYPES.values() if t.visible_in_ui],
        key=lambda t: t.id,
    )


def item_level_types() -> frozenset[str]:
    """所有按 item 维度冷却的类型 id"""
    return frozenset(t.id for t in _TYPES.values() if t.is_item_level)


def is_item_level_type(type_id: str) -> bool:
    """判断是否按 item 维度冷却"""
    meta = get_type_meta(type_id)
    return meta.is_item_level if meta else False


def resolve_in_app_type(type_id: str) -> str | None:
    """解析通知类型对应的站内信 type

    mark_failed → sync_failed
    anime_not_found → sync_failed
    episode_not_found → sync_failed
    其他 → None（不写站内信）
    """
    meta = get_type_meta(type_id)
    return meta.in_app_type if meta else None


def normalize_type(type_id: str) -> str:
    """归一化类型 id（watching_summary_dad → watching_summary）"""
    if type_id.startswith(WATCHING_SUMMARY_PREFIX):
        return WATCHING_SUMMARY_PREFIX
    return type_id


def type_display_name(type_id: str) -> str:
    """获取类型展示名（fallback 到原始 id）"""
    meta = get_type_meta(type_id)
    return meta.display_name if meta else type_id


def type_icon(type_id: str) -> str:
    """获取类型图标"""
    meta = get_type_meta(type_id)
    return meta.icon if meta else "📢"


def type_color(type_id: str) -> str:
    """获取类型颜色"""
    meta = get_type_meta(type_id)
    return meta.color if meta else "#6c757d"
