"""首页仪表盘卡片注册表（纯机制 + 内置卡片登记）。

首页的卡片清单、局部模板、默认尺寸与摆放都由这里集中登记：模板只负责循环渲染，
前端 (:mod:`static.js.dashboard-cards`) 只负责拖动 / 拉伸 / 显隐的交互与本地偏好，
新增卡片不必改路由、模板骨架或前端逻辑。

扩展方式（插件系统接入新卡片时照此登记）：

1. 在任意模板可查找的位置提供卡片本体的局部模板（``dashboard/cards/xxx.html``），
   或在 ``DashboardCard.render`` 里给出一个返回 HTML 字符串的函数；
2. 调用 :func:`register_card` 登记一张 :class:`DashboardCard`，字段含义见其文档；
3. 需要额外前端行为时监听 ``document`` 上的 ``dashboard:cards-ready`` 事件
   （detail 为当前布局），不要直接改 ``dashboard-cards.js``。

布局单位：横向以 12 栏栅格计（``GRID_COLUMNS``），纵向以像素计（卡片高度即像素，
前端拖动时按 ``HEIGHT_STEP`` 吸附）。默认布局由 :func:`dashboard_layout` 按登记顺序
做「首次适配」摆放，效果与旧版两列仪表盘一致。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

#: 栅格列数，与 ``static/css/dashboard.css`` 的 ``--dg-cols``、
#: ``static/js/dashboard-cards.js`` 的 ``COLUMNS`` 保持一致。
GRID_COLUMNS = 12
#: 卡片之间的间距（px），与 CSS 变量 ``--dg-gap`` 保持一致。
GRID_GAP = 24
#: 卡片最小高度（px）。
MIN_CARD_HEIGHT = 96
#: 卡片默认高度（px）：沿用旧版统计卡片的视觉高度。
DEFAULT_CARD_HEIGHT = 106
#: 前端拖动拉伸时的高度吸附步长（px）。
HEIGHT_STEP = 8

#: 卡片分组：统计 / LLM 用量 / 活动可视化。仅用于默认可见性与样式归类。
GROUPS = ("stats", "llm", "activity")


@dataclass(frozen=True)
class DashboardCard:
    """一张首页卡片的登记信息（不可变，注册后按 id 索引）。

    :param id: 唯一标识（小写 kebab-case），前端本地偏好以此为准，发布后不要改名；
    :param title: 自定义弹窗里显示的标题；
    :param description: 自定义弹窗里的一行说明，说清这张卡展示什么；
    :param template: 卡片本体的局部模板路径；与 ``render`` 二选一；
    :param render: 返回 HTML 字符串的函数（插件没有模板文件时的入口）；
    :param width: 默认占用的栅格列数（1..12）；
    :param height: 默认高度（px，>= ``min_height``）；
    :param min_width: 允许拖到的最小列数；
    :param min_height: 允许拖到的最小高度（px）；
    :param fit_height: 是否按内容自动长高（只长不缩），用于列表 / 热力图这类
        内容高度随数据变化的卡片，避免默认高度把内容截断；
    :param group: 分组，见 :data:`GROUPS`；
    :param default_visible: 首次访问时是否显示（``False`` 的卡不进默认布局）。
    """

    id: str
    title: str
    description: str
    template: str | None = None
    render: Callable[[], str] | None = None
    width: int = 3
    height: int = DEFAULT_CARD_HEIGHT
    min_width: int = 2
    min_height: int = MIN_CARD_HEIGHT
    fit_height: bool = False
    group: str = "stats"
    default_visible: bool = True

    def __post_init__(self) -> None:
        if not self.id or self.id != self.id.lower():
            raise ValueError("卡片 id 必须非空且为小写")
        if not self.id.replace("-", "").isalnum() or " " in self.id or "_" in self.id:
            raise ValueError(f"卡片 id 只能是 kebab-case：{self.id!r}")
        if self.group not in GROUPS:
            raise ValueError(f"未知的卡片分组：{self.group!r}")
        if not 1 <= self.width <= GRID_COLUMNS:
            raise ValueError(f"{self.id}: width 必须在 1..{GRID_COLUMNS}")
        if not 1 <= self.min_width <= self.width:
            raise ValueError(f"{self.id}: min_width 必须在 1..width")
        if self.height < self.min_height or self.min_height < MIN_CARD_HEIGHT:
            raise ValueError(
                f"{self.id}: height 不能小于 min_height 与 {MIN_CARD_HEIGHT}"
            )
        if (self.template is None) == (self.render is None):
            raise ValueError(f"{self.id}: template 与 render 必须二选一")

    def render_html(self) -> str | None:
        """返回卡片本体的 HTML（模板型卡片返回 ``None``，由模板 include 渲染）。"""
        if self.render is None:
            return None
        return self.render()


@dataclass(frozen=True)
class DashboardCardLayout:
    """卡片 + 默认摆放位置（``x``/``w`` 以栅格列计，``y``/``h`` 以像素计）。"""

    card: DashboardCard
    x: int
    y: int
    w: int
    h: int
    visible: bool = True

    @property
    def grid_style(self) -> str:
        """写成 CSS 自定义属性，供 ``.dashboard-grid__item`` 定位。"""
        return f"--dg-x:{self.x}; --dg-y:{self.y}px; --dg-w:{self.w}; --dg-h:{self.h}px"


_CARDS: dict[str, DashboardCard] = {}


def register_card(card: DashboardCard) -> DashboardCard:
    """登记一张卡片；id 重复或模板缺失时抛 ``ValueError``（插件加载应显式失败）。"""
    if card.id in _CARDS:
        raise ValueError(f"仪表盘卡片 id 重复：{card.id!r}")
    _CARDS[card.id] = card
    return card


def unregister_card(card_id: str) -> None:
    """移除登记（主要供插件热卸载与测试使用）。"""
    _CARDS.pop(card_id, None)


def get_card(card_id: str) -> DashboardCard | None:
    """按 id 取卡片，未登记返回 ``None``。"""
    return _CARDS.get(card_id)


def registered_cards() -> tuple[DashboardCard, ...]:
    """按登记顺序返回全部卡片（含默认隐藏的卡）。"""
    return tuple(_CARDS.values())


def _conflicts(x: int, y: int, w: int, h: int, other: DashboardCardLayout) -> bool:
    """判断矩形是否与已摆放的卡片冲突（横向按列重叠，纵向留出 ``GRID_GAP``）。"""
    overlap_x = x < other.x + other.w and other.x < x + w
    overlap_y = y < other.y + other.h + GRID_GAP and other.y < y + h + GRID_GAP
    return overlap_x and overlap_y


def _first_fit(placed: list[DashboardCardLayout], w: int, h: int) -> tuple[int, int]:
    """自上而下、自左而右找第一个放得下的位置（保持旧版两列的观感）。"""
    candidates = sorted({0} | {item.y + item.h + GRID_GAP for item in placed})
    for y in candidates:
        for x in range(GRID_COLUMNS - w + 1):
            if not any(_conflicts(x, y, w, h, item) for item in placed):
                return x, y
    bottom = max((item.y + item.h + GRID_GAP for item in placed), default=0)
    return 0, bottom


def dashboard_layout() -> list[DashboardCardLayout]:
    """返回每张卡片的默认摆放位置（含默认隐藏的卡，隐藏卡不参与排版）。

    隐藏卡也一并返回，首页据此渲染出全部卡片节点，自定义弹窗才能列出它们；
    它们的 ``x``/``y`` 为 0，实际位置在用户打开时由前端按当前布局追加。
    """
    placed: list[DashboardCardLayout] = []
    for card in _CARDS.values():
        if not card.default_visible:
            placed.append(
                DashboardCardLayout(
                    card=card, x=0, y=0, w=card.width, h=card.height, visible=False
                )
            )
            continue
        x, y = _first_fit(
            [item for item in placed if item.visible], card.width, card.height
        )
        placed.append(
            DashboardCardLayout(card=card, x=x, y=y, w=card.width, h=card.height)
        )
    return placed


def layout_height(layout: list[DashboardCardLayout]) -> int:
    """默认布局的总高度（px），用于给容器一个占位高度，避免加载时页面跳动。"""
    return max((item.y + item.h for item in layout if item.visible), default=0)


def _register_builtin_cards() -> None:
    """登记内置卡片（顺序即默认摆放顺序）。"""
    stats = (
        ("stats-totals", "总同步次数", "累计同步成功的记录总数。", "stats_totals.html"),
        ("stats-today", "今日同步", "今天完成的同步记录数量。", "stats_today.html"),
        ("stats-success", "成功率", "最近同步的成功率占比。", "stats_success.html"),
        ("stats-errors", "失败次数", "最近同步中失败的记录数量。", "stats_errors.html"),
    )
    for card_id, title, description, template in stats:
        register_card(
            DashboardCard(
                id=card_id,
                title=title,
                description=description,
                template=f"dashboard/cards/{template}",
                width=3,
                height=DEFAULT_CARD_HEIGHT,
            )
        )

    for suffix, title, description, template in (
        (
            "calls",
            "LLM 调用次数",
            "最近一段时间内 LLM 匹配的调用总量。",
            "llm_calls.html",
        ),
        (
            "tokens",
            "LLM Token",
            "LLM 匹配消耗的输入输出 Token 总量。",
            "llm_tokens.html",
        ),
        ("latency", "LLM 延迟", "LLM 匹配的平均响应耗时。", "llm_latency.html"),
        ("errors", "LLM 失败", "LLM 匹配调用失败的数量。", "llm_errors.html"),
    ):
        register_card(
            DashboardCard(
                id=f"llm-{suffix}",
                title=title,
                description=description,
                template=f"dashboard/cards/{template}",
                group="llm",
                default_visible=False,
            )
        )

    register_card(
        DashboardCard(
            id="llm-trend",
            title="LLM 用量趋势",
            description="Token 用量与匹配质量随时间的变化。",
            template="dashboard/cards/llm_trend.html",
            width=12,
            height=300,
            group="llm",
            default_visible=False,
        )
    )

    register_card(
        DashboardCard(
            id="sync-timeline",
            title="最近同步",
            description="最近的同步记录，新的从顶部出现。",
            template="dashboard/cards/sync_timeline.html",
            group="activity",
            width=5,
            height=780,
            min_width=3,
            min_height=280,
            fit_height=True,
        )
    )
    register_card(
        DashboardCard(
            id="sync-heatmap",
            title="同步热力图",
            description="按天展示最近一年的同步活跃度。",
            template="dashboard/cards/sync_heatmap.html",
            group="activity",
            width=7,
            height=200,
            min_width=4,
            min_height=140,
            fit_height=True,
        )
    )
    register_card(
        DashboardCard(
            id="sync-daily-trend",
            title="最近7天趋势",
            description="最近 7 天的同步数量走势。",
            template="dashboard/cards/sync_daily_trend.html",
            group="activity",
            width=7,
            height=556,
            min_width=4,
            min_height=300,
        )
    )
    register_card(
        DashboardCard(
            id="airing-calendar",
            title="我在追的放送",
            description="未来 30 天在追番剧的放送日程（需启用 Archive 离线库）。",
            template="dashboard/cards/airing_calendar.html",
            group="activity",
            width=7,
            height=360,
            min_width=4,
            default_visible=False,
        )
    )
    register_card(
        DashboardCard(
            id="scheduler-status",
            title="调度器状态",
            description="各调度器任务的下次运行时间。",
            template="dashboard/cards/scheduler_status.html",
            group="activity",
            width=5,
            height=360,
            min_width=3,
            default_visible=False,
        )
    )


_register_builtin_cards()


__all__ = [
    "DEFAULT_CARD_HEIGHT",
    "GRID_COLUMNS",
    "GRID_GAP",
    "GROUPS",
    "HEIGHT_STEP",
    "MIN_CARD_HEIGHT",
    "DashboardCard",
    "DashboardCardLayout",
    "dashboard_layout",
    "get_card",
    "layout_height",
    "register_card",
    "registered_cards",
    "unregister_card",
]
