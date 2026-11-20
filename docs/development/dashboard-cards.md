---
title: 🧱 仪表盘卡片接入指南
order: 12
---

# 🧱 仪表盘卡片接入指南

首页仪表板的卡片清单、默认尺寸与摆放位置集中在 `app/core/dashboard_registry.py`，
模板只做循环渲染，前端（`static/js/dashboard-cards.js`）只做拖动 / 拉伸 / 显隐。
新增一张卡片不需要改首页模板骨架、路由或前端布局代码。

## 组成

| 位置 | 职责 |
| --- | --- |
| `app/core/dashboard_registry.py` | `DashboardCard` 数据结构、内置卡片登记、默认摆放算法、插件登记入口 |
| `templates/dashboard/cards/*.html` | 卡片本体的局部模板（一个 `.card` 元素） |
| `templates/dashboard.html` | 单一 `#dashboardGrid` 容器，循环 `dashboard_layout` |
| `static/js/dashboard-cards.js` | 12 栏自由布局、拖动换位、拉伸尺寸、显隐弹窗、`localStorage` 偏好 |
| `static/css/dashboard.css` | `.dashboard-grid*` / `.dashboard-card__*` / `.dashboard-customize__*` 样式 |
| `tests/api/test_dashboard_cards.py` | 注册表、默认布局与页面渲染的契约测试 |

## 布局语义

- **横向**按 12 栏栅格（`GRID_COLUMNS`），**纵向**按像素（`HEIGHT_STEP = 8` 吸附）。
- 卡片间距固定 `GRID_GAP = 24`，由 CSS 变量 `--dg-gap` 统一给出。
- 卡片位置写成 `--dg-x` / `--dg-y` / `--dg-w` / `--dg-h` 四个自定义属性，
  服务端在 `style` 里给出默认值，**没有 JS 也能正确落位**；前端只负责改写它们。
- 默认摆放由 `dashboard_layout()` 按登记顺序做「首次适配」：先从左到右排满一行，
  再往下找第一个放得下的位置，效果与旧版两列仪表盘一致
  （最近同步占左 5 列，热力图 + 趋势叠在右 7 列）。
- 用户拖动后按「重力」向上压缩，不留悬空缝隙；偏好存 `localStorage`
  的 `app-dashboard-cards`（`{version: 2, layout, hidden}`），不落服务端。
- 宽度 `< 768px` 时降级为单列堆叠，拖动与拉伸自动禁用。

## 新增内置卡片

1. 新建局部模板 `templates/dashboard/cards/<名字>.html`，根元素写一个 `.card`
   （卡片高度由栅格决定，内容区请自行处理溢出）。
2. 在 `app/core/dashboard_registry.py` 的 `_register_builtin_cards()` 里登记：

```python
register_card(
    DashboardCard(
        id="sync-weekly-report",          # 小写 kebab-case，前端偏好以此为准，发布后不要改
        title="每周同步报表",
        description="按周汇总的同步成功与失败数量。",
        template="dashboard/cards/sync_weekly_report.html",
        width=6,                           # 默认占 6 列
        height=300,                        # 默认 300px 高
        group="activity",                  # stats / llm / activity
    )
)
```

3. 若卡片需要前端行为（例如图表在尺寸变化后重绘），监听首页事件，不要改
   `dashboard-cards.js`：

```javascript
document.addEventListener('dashboard:layout-changed', function (event) {
    // event.detail.layout: { cardId: { x, y, w, h } }
    myChart.resize()
})
```

- `dashboard:cards-ready`：布局首次就绪（含 `layout`、`reset()`、`setVisible()`、`relayout()`）。
  同一份 API 也挂在 `window.dashboardCards` 上，供先于它执行的脚本直接调用。
- `dashboard:layout-changed`：用户拖动 / 拉伸 / 显隐后触发。

默认隐藏的卡片（例如「我在追的放送」「调度器状态」）在用户打开前**不要发起网络请求**：
脚本先判断 `document.querySelector('[data-dashboard-card="<id>"]')` 是否带 `d-none`，
隐藏时只监听上面两个事件，等卡片可见后再加载。

## 插件接入自定义卡片

插件没有模板文件时，用 `render` 回调直接返回 HTML 字符串（与 `template` 二选一）：

```python
from app.core.dashboard_registry import DashboardCard, register_card


def render_plugin_card() -> str:
    return '<div class="card"><div class="card-body p-4">来自插件的卡片</div></div>'


register_card(
    DashboardCard(
        id="plugin-pluginname-status",
        title="插件运行状态",
        description="由插件注册的自定义卡片。",
        render=render_plugin_card,
        width=4,
        height=180,
    )
)
```

约定与校验（`DashboardCard.__post_init__` 会直接抛 `ValueError`，让插件加载显式失败）：

- `id`：非空小写 kebab-case，**全局唯一**（重复登记直接报错），建议加插件名前缀；
- `width`：`1..12`，且 `min_width <= width`；`height >= min_height >= 96`；
- `group`：只能是 `stats` / `llm` / `activity`；
- `template` 与 `render` 必须且只能提供一个；
- `default_visible=False` 的卡片同样会渲染进 DOM（自定义弹窗要能列出它），
  但默认隐藏、不参与默认摆放，用户打开时由前端按「首次适配」找第一个空位；
- 移除卡片用 `unregister_card(card_id)`；本地偏好里已不存在的 id 会被自动丢弃。

## 自检

改动卡片区后至少跑：

```bash
uv run pytest tests/api/test_dashboard_cards.py tests/api/test_pages.py -q
uv run ruff check . && uv run ruff format --check .
uv run djlint templates/ --check
```
