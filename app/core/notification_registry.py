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

    # 站内信映射：该类型触发站内信时使用的 type 和标题模板
    # None 表示不触发站内信；如 "sync_failed" 表示写站内信时 type=sync_failed
    in_app_type: str | None = None
    in_app_title_template: str | None = None  # 如 "同步失败：{title} {ep_label}"

    # 是否在配置页类型选择列表中展示（某些内部类型如 sync_queued 也可展示）
    visible_in_ui: bool = True


# ── 动态类型前缀 ──────────────────────────────────────────────────────────

WATCHING_SUMMARY_PREFIX = "watching_summary"

# watching_summary_{name} 的通配元数据
_WATCHING_SUMMARY_META = NotificationTypeMeta(
    id=WATCHING_SUMMARY_PREFIX,
    display_name="追番总结",
    icon="📊",
    color="#6c8ebf",
    description="AI 追番总结任务完成/失败",
    is_item_level=False,
    visible_in_ui=False,  # 动态类型不在静态列表展示
)


# ── 类型元数据注册表 ──────────────────────────────────────────────────────

_TYPES: dict[str, NotificationTypeMeta] = {
    # ── 同步流程核心 ──
    "request_received": NotificationTypeMeta(
        id="request_received",
        display_name="收到同步请求",
        icon="📥",
        color="#0d6efd",
        description="收到来自媒体服务器的同步请求",
        is_item_level=True,
    ),
    "bangumi_id_found": NotificationTypeMeta(
        id="bangumi_id_found",
        display_name="匹配到番剧",
        icon="🔍",
        color="#198754",
        description="成功匹配到 Bangumi 番剧信息",
        is_item_level=True,
    ),
    "mark_success": NotificationTypeMeta(
        id="mark_success",
        display_name="同步成功",
        icon="✅",
        color="#198754",
        description="番剧已成功标记为已看",
        is_item_level=True,
    ),
    "mark_failed": NotificationTypeMeta(
        id="mark_failed",
        display_name="同步失败",
        icon="❌",
        color="#dc3545",
        description="同步标记失败或处理异常",
        is_item_level=True,
        in_app_type="sync_failed",
        in_app_title_template="同步失败：{title} {ep_label}",
    ),
    "mark_skipped": NotificationTypeMeta(
        id="mark_skipped",
        display_name="已看过跳过",
        icon="⏭️",
        color="#6c757d",
        description="已看过不再重复标记",
        is_item_level=True,
    ),
    "sync_queued": NotificationTypeMeta(
        id="sync_queued",
        display_name="API不可达入队",
        icon="📋",
        color="#6c8ebf",
        description="Bangumi API 不可达，请求进入待同步队列",
        is_item_level=True,
    ),
    "sync_replayed": NotificationTypeMeta(
        id="sync_replayed",
        display_name="队列补发成功",
        icon="📤",
        color="#198754",
        description="待同步队列补发成功",
        is_item_level=True,
    ),
    # ── 匹配失败类 ──
    "anime_not_found": NotificationTypeMeta(
        id="anime_not_found",
        display_name="未找到番剧",
        icon="🔍",
        color="#fd7e14",
        description="未找到匹配的番剧",
        is_item_level=False,
        in_app_type="sync_failed",
        in_app_title_template="同步失败：{title} {ep_label}",
    ),
    "episode_not_found": NotificationTypeMeta(
        id="episode_not_found",
        display_name="未找到剧集",
        icon="📺",
        color="#fd7e14",
        description="未找到对应的剧集信息",
        is_item_level=False,
        in_app_type="sync_failed",
        in_app_title_template="同步失败：{title} {ep_label}",
    ),
    "pending_candidate": NotificationTypeMeta(
        id="pending_candidate",
        display_name="候选待确认",
        icon="📝",
        color="#fd7e14",
        description="匹配失败但有候选，等待用户确认",
        is_item_level=True,
    ),
    # ── 系统/配置类 ──
    "config_error": NotificationTypeMeta(
        id="config_error",
        display_name="配置错误",
        icon="⚙️",
        color="#ffc107",
        description="配置错误导致同步无法继续",
        is_item_level=False,
    ),
    "ip_locked": NotificationTypeMeta(
        id="ip_locked",
        display_name="IP被锁定",
        icon="🔒",
        color="#dc3545",
        description="登录失败次数过多，IP 被临时锁定",
        is_item_level=False,
    ),
    # ── Bangumi API 类 ──
    "api_error": NotificationTypeMeta(
        id="api_error",
        display_name="API错误",
        icon="🌐",
        color="#dc3545",
        description="Bangumi API 返回错误（5xx/429）",
        is_item_level=False,
    ),
    "api_auth_error": NotificationTypeMeta(
        id="api_auth_error",
        display_name="API认证失败",
        icon="🔑",
        color="#dc3545",
        description="Bangumi API 认证失败（401）",
        is_item_level=False,
    ),
    "api_retry_failed": NotificationTypeMeta(
        id="api_retry_failed",
        display_name="API重试失败",
        icon="🔄",
        color="#dc3545",
        description="Bangumi API 重试耗尽",
        is_item_level=False,
    ),
    # ── 站内信专用类型（不在前端选择列表展示）──
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
    ),
    "summary_job_failed": NotificationTypeMeta(
        id="summary_job_failed",
        display_name="总结任务失败",
        icon="⚠️",
        color="#dc3545",
        description="Summary 任务执行异常",
        is_item_level=False,
        visible_in_ui=False,
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
