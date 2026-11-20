"""首页仪表盘卡片注册表（DashboardCardRegistry）

集中登记仪表盘卡片的元信息，作为卡片清单、局部模板、栅格宽度、
默认可见性与分组归属的单一真相源。卡片区由 ``templates/dashboard.html``
的单一行网格循环渲染；显示/排序偏好只存在浏览器 localStorage
（见 ``static/js/dashboard-cards.js``），不涉及服务端配置。

新增一张卡片只有两步：

1. 在 ``templates/dashboard/cards/`` 下新建局部模板，内容只含卡片本体
   （``<div class="card ...">…</div>``），不带外层 col；
2. 在下面的 ``CARDS`` 里追加一条 :class:`DashboardCard`。

分组 ``llm`` 的卡片仅在 LLM 已接入时由 :func:`dashboard_cards` 提供，
默认不可见（``default_visible=False``），用户可在首页「自定义」里打开。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DashboardCard:
    """单个仪表盘卡片的注册元数据。"""

    id: str  # kebab-case 唯一标识，前端偏好按它记录顺序与隐藏态
    title: str  # 中文显示名（自定义弹窗列表 + data-card-title）
    description: str  # 一句话说明（自定义弹窗里的副标题）
    template: str  # 局部模板路径，如 "dashboard/cards/stats_totals.html"
    span: str  # Bootstrap col class，如 "col-xl-3 col-md-6"
    default_visible: bool = True  # 默认是否显示（False 时首屏带 d-none）
    group: str = "stats"  # 分组：stats / llm / activity


# 统计卡片：默认显示。
_STATS_CARDS: tuple[DashboardCard, ...] = (
    DashboardCard(
        id="stats-totals",
        title="总同步次数",
        description="累计执行过的同步总次数",
        template="dashboard/cards/stats_totals.html",
        span="col-xl-3 col-md-6",
        group="stats",
    ),
    DashboardCard(
        id="stats-today",
        title="今日同步",
        description="今天已经执行的同步次数",
        template="dashboard/cards/stats_today.html",
        span="col-xl-3 col-md-6",
        group="stats",
    ),
    DashboardCard(
        id="stats-success",
        title="成功率",
        description="同步成功占总次数的比例",
        template="dashboard/cards/stats_success.html",
        span="col-xl-3 col-md-6",
        group="stats",
    ),
    DashboardCard(
        id="stats-errors",
        title="失败次数",
        description="同步失败的累计次数",
        template="dashboard/cards/stats_errors.html",
        span="col-xl-3 col-md-6",
        group="stats",
    ),
)

# LLM 用量卡片：仅在 LLM 已接入时出现，且默认隐藏（见 default_visible）。
_LLM_CARDS: tuple[DashboardCard, ...] = (
    DashboardCard(
        id="llm-calls",
        title="LLM 调用",
        description="近 30 天的 LLM 调用次数",
        template="dashboard/cards/llm_calls.html",
        span="col-xl-3 col-md-6",
        default_visible=False,
        group="llm",
    ),
    DashboardCard(
        id="llm-tokens",
        title="LLM Token",
        description="近 30 天消耗的 Token 总量",
        template="dashboard/cards/llm_tokens.html",
        span="col-xl-3 col-md-6",
        default_visible=False,
        group="llm",
    ),
    DashboardCard(
        id="llm-latency",
        title="LLM 平均延迟",
        description="LLM 调用的平均响应耗时",
        template="dashboard/cards/llm_latency.html",
        span="col-xl-3 col-md-6",
        default_visible=False,
        group="llm",
    ),
    DashboardCard(
        id="llm-errors",
        title="LLM 调用失败",
        description="近 30 天 LLM 调用失败次数",
        template="dashboard/cards/llm_errors.html",
        span="col-xl-3 col-md-6",
        default_visible=False,
        group="llm",
    ),
    DashboardCard(
        id="llm-trend",
        title="LLM 用量趋势",
        description="近 7 天 Token 用量与错误率/延迟走势",
        template="dashboard/cards/llm_trend.html",
        span="col-12",
        default_visible=False,
        group="llm",
    ),
)

# 活动卡片：同步记录时间线 / 热力图 / 每日趋势，默认显示。
_ACTIVITY_CARDS: tuple[DashboardCard, ...] = (
    DashboardCard(
        id="sync-timeline",
        title="最近同步",
        description="最近 6 条同步记录的时间线",
        template="dashboard/cards/sync_timeline.html",
        span="col-xl-5 col-lg-6 d-flex",
        group="activity",
    ),
    DashboardCard(
        id="sync-heatmap",
        title="同步热力图",
        description="按日期统计的同步次数分布",
        template="dashboard/cards/sync_heatmap.html",
        span="col-xl-7 col-lg-6 d-flex",
        group="activity",
    ),
    DashboardCard(
        id="sync-daily-trend",
        title="最近7天趋势",
        description="最近 7 天每日同步次数折线",
        template="dashboard/cards/sync_daily_trend.html",
        span="col-12",
        group="activity",
    ),
)

# 全部卡片（注册顺序 = 首屏 DOM 顺序 = 「恢复默认」顺序）
CARDS: tuple[DashboardCard, ...] = (
    *_STATS_CARDS,
    *_LLM_CARDS,
    *_ACTIVITY_CARDS,
)


def dashboard_cards(llm_enabled: bool = False) -> list[DashboardCard]:
    """返回首页可用的卡片列表。

    ``llm_enabled=False``（默认）时不提供 ``llm`` 组：LLM 未接入时这些卡片
    既无数据也无入口，不占用栅格。统计卡片的 ``default_visible`` 不受影响。
    """
    if llm_enabled:
        return list(CARDS)
    return [card for card in CARDS if card.group != "llm"]
