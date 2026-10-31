# Frontend Interaction Plan: AI Watching Summary

> **日期**: 2026-07-14  
> **用途**: 向 Bangumi-Syncer 维护者说明 AI 追番总结功能的前端交互设计，作为 `SDD/spec.md` 的补充。

## 技术约束

- 零新依赖：Jinja2 + Bootstrap 5 + Vanilla JS + Chart.js（均已就位）
- 零新 CSS 文件：复用 `style.css` 现有类名
- 相对路径跳转：`{{ '/path' | p }}` + `appUrl('/path')`，兼容反向代理

## 页面与改动清单

```
现有页面                         改动
────────────────────────────────────────
config.html          ← 新增 "LLM 配置" section + 交叉链接
dashboard.html       ← 新增 LLM 用量 stat cards
base.html            ← 新增 "AI 总结" 导航项
pages.py             ← 新增 GET /summary 路由
templates/summary.html  ← 新建：Summary 管理主页
static/js/summary.js    ← 新建：页面 JS 逻辑
```

---

## 1. config.html — LLM 配置区

```
┌─────────────────────────────────────────┐
│ 🤖 LLM 配置                             │
├─────────────────────────────────────────┤
│ API 地址  [https://api.openai.com/v1 ] │
│ API 密钥  [••••••••••••••••       ]    │  ← type=password，已保存值脱敏
│ 模型      [gpt-4o-mini            ]    │
│ 最大Token [2000                   ]    │
│ 温度      [0.7                    ]    │
│ 超时(秒)  [60                     ]    │
│                                         │
│ [测试连接]    ← POST /llm/test │
└─────────────────────────────────────────┘
```

**交互**：
- 页面加载 → `GET /llm` → 填充表单
- "测试连接" → 按钮 loading → toast "连接成功 ✅" 或 "连接失败 ❌"
- 保存走 config.html 统一 `section.field` 命名 (`llm.api_base`, `llm.api_key`...)

**交叉链接（通知配置区底部）**：
```
🔔 通知配置
  Webhook 管理 [卡片...] [+ 新增]
  Email 管理 [卡片...] [+ 新增]
  🤖 AI 追番总结 →     ← 新链接，href="{{ '/summary' | p }}"
```

---

## 2. dashboard.html — LLM 用量卡片

```
┌──────────┬──────────┬──────────┬──────────┐
│ 总同步   │ 今日同步  │ 成功率   │ 错误     │  ← 现有 4 张
├──────────┼──────────┼──────────┼──────────┤
│ 🤖 调用  │ 🎯 Token │ ⚡ 延迟  │ ❌ 失败  │  ← 新增 2-4 张
│ 150 次   │ 450K     │ 1.2s     │ 3 次     │
└──────────┴──────────┴──────────┴──────────┘
```

**交互**：
- `loadDashboardData()` → `GET /llm/stats?scope=aggregate`
- api_key 非空 → 展示卡片；为空 → 隐藏整行
- 请求失败 → 静默降级，不展示（不弹错误）

---

## 3. base.html — 导航

```html
<!-- 在 "Trakt 同步" 和用户信息之间插入 -->
<li class="nav-item">
    <a class="nav-link {% if request.url.path == '/summary' %}active{% endif %}"
       href="{{ '/summary' | p }}">
        <i class="bi bi-robot me-2"></i>AI 总结
    </a>
</li>
```

---

## 4. templates/summary.html — 主页面

```
┌──────────────────────────────────────────────────────────────┐
│ 📊 追番总结                                                   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│ ┌──────────────────┐ ┌──────────┬──────────┬──────────────┐ │
│ │ 🤖 LLM 状态       │ │ 本月调用  │ Token    │ 平均延迟     │ │
│ │ model: gpt-4o-mini│ │  150 次   │ 450K    │ 1.2s        │ │
│ │ 状态: ✅ 正常      │ │           │         │             │ │
│ │ [修改配置 →/config]│ │           │         │             │ │
│ └──────────────────┘ └──────────┴──────────┴──────────────┘ │
│                                                              │
│ ┌──────────────────────────────────────────────────┐         │
│ │ 📈 7天用量趋势                          [收起 ▲]  │         │
│ │  ▁▂▃▅▂▄▆  (Chart.js line chart)                  │         │
│ └──────────────────────────────────────────────────┘         │
│                                                              │
│ 📋 定时总结任务                               [+ 新建任务]    │
│                                                              │
│ ┌──────────────────────────────────────────────────────┐     │
│ │ ✅ 爸爸日报                                   [开关]  │     │
│ │ cron: 0 21 * * * (每日 21:00)                        │     │
│ │ 用户: dad | 回溯: 1天 | 记录上限: 200                  │     │
│ │ 📊 本月: 30次调用 / 90K tokens                        │     │
│ │ [测试] [立即触发] [编辑] [删除]                        │     │
│ └──────────────────────────────────────────────────────┘     │
│                                                              │
│ ┌──────────────────────────────────────────────────────┐     │
│ │ ✅ 孩子周报                                   [开关]  │     │
│ │ cron: 0 20 * * 0 (每周日 20:00)                       │     │
│ │ ...                                                  │     │
│ └──────────────────────────────────────────────────────┘     │
│                                                              │
│ ─────────────────────────────────────────────────────        │
│ 📬 [配置 Webhook 通知渠道 →] (/config 锚点)                   │
└──────────────────────────────────────────────────────────────┘
```

### 交互流程

```
页面加载
├─ loadLLMStatus()     → GET /llm        → 渲染状态卡
├─ loadLLMStats()      → GET /llm/stats   → 渲染用量卡片 + Chart.js
└─ loadSummaryJobs()   → GET /api/summary/jobs        → 渲染 job 卡片列表

[+ 新建任务] → showJobModal(null) → 填写表单 → [保存]
  → POST /api/summary/jobs → toast → reload jobs

[测试] → testJob(id) → loading → 展示结果 modal
[立即触发] → triggerJob(id) → toast "任务已触发，summary 将发送到订阅的 webhook"
[编辑] → showJobModal(jobData) → 修改 → [保存] → PUT → reload
[删除] → showDeleteModal(id, name) → 确认 → DELETE → reload

[开关 toggle] → PUT /api/summary/jobs/{id} {enabled: !} → reload
```

### Add/Edit Modal

```
┌──────────────────────────────────────────┐
│ 新建 Summary Job                         │
├──────────────────────────────────────────┤
│ 名称       [爸爸日报__________________] │
│ Cron       [0 21 * * *______________] │
│ 回溯天数   [1________________________] │
│ 用户名     [dad______________________] │  ← 空=所有用户
│ 最大记录数 [200______________________] │
│                                          │
│ 系统提示词                                │
│ ┌──────────────────────────────────────┐ │
│ │ 你是一个友好的追番助手。请根据提供...  │ │  ← textarea rows=4
│ └──────────────────────────────────────┘ │
│                                          │
│ 用户提示词模板                            │
│ ┌──────────────────────────────────────┐ │
│ │ 以下是 {date_from} 到 {date_to} 的...│ │  ← textarea rows=6
│ │                                      │ │
│ │ {records}                            │ │
│ │                                      │ │
│ │ 请根据以上记录生成追番总结。           │ │
│ └──────────────────────────────────────┘ │
│ 可用变量：{date_from} {date_to}          │
│ {records} {record_count} {lookback_days} │
│                                          │
│ [取消]                         [保存]    │
└──────────────────────────────────────────┘
```

### Test Result Modal

```
┌──────────────────────────────────────────┐
│ 测试结果: 爸爸日报                        │
├──────────────────────────────────────────┤
│ 模型: gpt-4o-mini                        │
│ Token: 1270 (提示:850 + 生成:420)         │
│ 延迟: 1.2s                               │
│ ──────────────────────────────────────── │
│                                          │
│ 今日爸爸共观看了 3 部番剧，播了 5 集。    │
│                                          │
│ 📺 葬送的芙莉莲 S1E10                     │
│    > 勇者一行人继续他们的旅程...          │
│                                          │
│ 📺 迷宫饭 S1E3                            │
│    > 在地下城中探索各种美食...            │
│                                          │
│ 📺 我推的孩子 S2E5                        │
│    > 新一集揭开了更多娱乐圈内幕...        │
│                                          │
│ [关闭]                                   │
└──────────────────────────────────────────┘
```

---

## 5. static/js/summary.js — JS 模块

```javascript
// ===== 初始化 =====
document.addEventListener('DOMContentLoaded', () => {
    loadLLMStatus();
    loadLLMStats();
    loadSummaryJobs();
});

// ===== API 调用（均使用 fetch + appUrl） =====
async function loadLLMStatus()          // GET  /llm
async function loadLLMStats()           // GET  /llm/stats
async function loadSummaryJobs()        // GET  /api/summary/jobs
async function saveJob(data, id)        // POST /api/summary/jobs[/{id}]
async function deleteJob(id)            // DELETE /api/summary/jobs/{id}
async function testJob(id)              // POST /api/summary/jobs/{id}/test
async function triggerJob(id)           // POST /api/summary/jobs/{id}/trigger

// ===== 渲染函数 =====
function renderLLMStatusCard(config)     // 模型名 + 状态指示
function renderLLMStatsCards(stats)      // 4 张 stat cards
function renderUsageChart(daily)         // Chart.js line chart（7天）
function renderJobCards(jobs)           // 卡片网格
function renderSingleJobCard(job)       // 单张卡片 HTML
function renderEmptyState()             // 空状态："还没有总结任务"

// ===== Modal =====
function showJobModal(job = null)       // null=新建, object=编辑
function showDeleteModal(id, name)
function showTestResultModal(data)      // summary_text + token 信息

// ===== 辅助 =====
function parseJobForm()                 // 从 #jobForm 提取数据
function formatNumber(n)               // 1500 → "1.5K"
```

## 关键设计点

1. **LLM 用量卡片仅在有配置时展示**：Dashboard 和 /summary 都检查 `api_key` 是否非空
2. **空状态友好**：无 job 时展示引导文案 + CTA 按钮，而非空白页面
3. **操作反馈**：所有异步操作用 `showAlert()` toast，（测试结果用 modal）
4. **错误降级**：API 调用失败 → toast 错误信息，不破坏页面状态
5. **变量提示**：prompt 模板 textarea 下方标注可用变量名
