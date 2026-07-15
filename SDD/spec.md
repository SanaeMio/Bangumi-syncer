# AI-Powered Watching Summaries via Webhook (Spec)

## Context

Bangumi-Syncer (BS) 是一个将媒体服务器（Plex/Emby/Jellyfin/Trakt/Feiniu/Fongmi）观看进度同步到 Bangumi 的工具。已有能力：

- `sync_records` SQLite 表存储所有同步记录（user_name, title, episode, season, timestamp, source, status, bgm_title）
- Notifier 模块支持多 webhook/email 配置，13 种通知类型，通过 `types` 字段订阅
- APScheduler 运行 Trakt/Feiniu/Fongmi 三个定时同步任务
- 前后端一体：Jinja2 + Bootstrap 5 + vanilla JS，Web UI 管理所有配置
- **多用户模式**：`[sync] mode = multi`，`user_name` 映射到不同 Bangumi 账号

本次引入 AI 能力，目标：

1. **构建 LLM 基础模块** (`app/services/llm/`) — 独立 service package，provider 抽象，重试/日志
2. **基于 LLM 模块构建追番总结** (`app/services/summary/`) — 多用户适配，综合 webhook/定时任务/database
3. **前后端配套** — LLM 配置和 Summary Job CRUD 全在 `config.html` 内（一站式管理），不新增页面，Dashboard 集成用量统计

## Architecture

```
app/services/llm/          LLM 基础模块（新建 package）
├── __init__.py            导出 LLMClient, Message, ChatResponse, get_llm_client
├── models.py              Message, ChatResponse（最小化，v1 只有 content: str）
├── providers/
│   ├── __init__.py
│   ├── base.py            BaseProvider ABC（一个抽象方法：chat）
│   └── openai_compat.py   OpenAI 兼容协议实现 + 重试 + usage 提取
└── client.py              LLMClient（封装 provider，成本日志，单例）

app/services/summary/      追番总结业务（消费 LLM 模块，新建 package）
├── __init__.py
├── models.py              SummaryJobConfig
├── service.py             编排：DB查询 → prompt格式化 → LLM调用 → Notifier发送
└── scheduler.py           APScheduler 封装（多 job 动态管理，不继承 BaseScheduler）

app/core/database/         数据库（现有 package，新增文件）
├── llm_usage.py           LLM 用量日志表 + log_llm_usage() + get_llm_usage_stats() + cleanup
└── sync_records.py        新增 get_records_in_date_range() 方法

app/utils/notifier/        通知（现有 package，修改 3 个文件）
├── webhook.py             新增 watching_summary payload
├── html_builders.py       新增 watching_summary email HTML
└── email_sender.py        新增 watching_summary email subject
```

**SummaryScheduler 不继承 BaseScheduler 的原因：** `BaseScheduler` 为单任务设计（一个 job_id + 一个 cron 表达式），而 SummaryScheduler 需要从多个 `[summary-N]` 配置动态管理多个 cron job。直接使用 APScheduler 的 `add_job`/`remove_job` API，参考 `BaseScheduler` 的 start/stop/cron 解析模式但不继承。

## Configuration Reference

### `[llm]` — LLM 全局配置（6 个字段）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_base` | string | `https://api.openai.com/v1` | OpenAI 兼容 API 地址 |
| `api_key` | string | (空) | API 密钥，保存后 Fernet 加密（BGS1: 前缀） |
| `model` | string | `gpt-4o-mini` | 模型名称 |
| `max_tokens` | int | `2000` | 最大输出 token 数 |
| `temperature` | float | `0.7` | 生成温度 (0.0~2.0) |
| `timeout` | int | `60` | 请求超时（秒） |

存入 `config.ini [llm]` section。`api_key` 注册为敏感字段，走现有加密通道。

### `[summary-N]` — Summary Job 配置（9 个字段）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `id` | int | 自增 | Job 唯一标识 |
| `enabled` | bool | `true` | 是否启用 |
| `name` | string | `"Summary-N"` | Job 名称，用于 webhook payload 标识 |
| `cron` | string | `0 21 * * *` | 标准 5 字段 cron 表达式（北京时间） |
| `lookback_days` | int | `1` | 回溯天数（1=每日, 7=每周） |
| `user_name` | string | (空) | 用户过滤。空=所有用户；指定则只查该 media server 用户名 |
| `system_prompt` | string | (中文默认) | LLM 系统提示词 |
| `user_prompt_template` | string | (中文默认) | 用户提示词模板，变量：`{date_from}`, `{date_to}`, `{records}`, `{record_count}`, `{lookback_days}` |
| `max_records` | int | `200` | 最大记录数，超出截断 |

### 默认 Prompt 设计

**设计原则：**
1. 角色明确、口吻自然——避免机械的"以下是您的数据"风格
2. 结构清晰——骨架固定，LLM 负责填充内容
3. Token 经济——prompt 本身尽量简洁，把 token 预算留给 records
4. 多用户感知——records 中自带 `user_name`，LLM 应自然地按用户分组描述
5. 空记录兜底——如果 `{record_count}` 为 0，LLM 应友好地告知 "今天还没有追番记录"

**默认 system_prompt：**

```
你是一个轻松有趣的追番助手。用户会给你一段观影记录，请你用亲切自然的中文生成追番总结。

规则：
1. 如果记录为 0 条，友好地告知用户"今天还没有追番记录哦~"
2. 按番剧分组，简要描述观看进度（如"《芙莉莲》追到 S1E10"）
3. 如果涉及多用户（记录中 user_name 不同），按用户分开描述
4. 加一两句轻松评论，语气像朋友聊天，不要太正式
5. 限制在 300 字以内
```

**默认 user_prompt_template：**

```
{date_from} 至 {date_to} 观影记录（共 {record_count} 条）：

{records}
```

**为什么 user_prompt_template 这么简单？** 结构化的指令全部放在 system_prompt 中（role + rules），user_prompt_template 只负责"塞数据"——{records} 是格式化的纯文本表格。这个分工让用户自定义时也更清晰：改 system_prompt 调整风格/规则，改 user_prompt_template 调整数据呈现方式。

## Key Design Decisions

### 1. LLM 模块：只保留 Provider 抽象，不预声明未实现的方法

| 决策 | 理由 |
|------|------|
| **保留** Provider 抽象 | 后续换 provider（如 Anthropic native）时提取接口+拆分代码需 2-3 小时，现在 40 行 ABC 解决 |
| **保留** `ChatResponse` 类型 | 比裸 `str` 多一行代码；未来加字段不破坏 caller |
| **不预声明** `chat_stream()` | 改签名的常规重构，30 分钟 |
| **不预声明** `embedding()` | 纯增量（加方法），零现有代码改动 |

v1 LLM 模块核心就是一个抽象方法 `chat()` + OpenAI 实现 + 重试日志。

```python
# base.py
class BaseProvider(ABC):
    @abstractmethod
    async def chat(self, messages: list[Message], **kwargs) -> ChatResponse: ...

# models.py
class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

class ChatResponse(BaseModel):
    content: str
    model: str = ""
    usage: Usage | None = None  # prompt_tokens, completion_tokens, total_tokens
```

### 2. webhook 路由：watching_summary 是一种普通通知类型

`watching_summary` 与现有的 `mark_success`、`mark_failed`、`request_received` 等完全平级——用户通过在 webhook/email 配置的 `types` 字段中勾选来订阅。

```ini
# 现有 webhook，新增 watching_summary 类型即可
[webhook-1]
types = mark_failed,watching_summary    # 收失败通知 + AI 追番总结

[webhook-2]
types = watching_summary_dad             # 只收爸爸的总结
```

**零新增配置机制。** 不需要 `webhook_ids` 过滤、不需要新的 UI 控件——完全复用现有 webhook/email CRUD 的 `types` 多选框（在 webhook/email Modal 中加一个 checkbox）。用户名级别的路由通过动态 notification type（`watching_summary_{user_name}`）自然实现。

### 3. UI 布局：全在 config.html，不新增页面

```
config.html section 导航 (TOC 锚点):
├── 同步配置
├── Bangumi 账号
├── 多用户 (mode=multi 时)
├── 高级配置
├── Web 认证
├── Bangumi-data
├── 🤖 LLM 配置 [新增 - 顶层 section，与同步/Bangumi 同级]
│   ├── api_base, api_key (密码框), model, max_tokens, temperature, timeout
│   └── [测试连接] 按钮 → POST /api/summary/llm/test → toast 结果
├── 📊 AI 追番总结 [新增 - 卡片式 CRUD，仿 webhook 管理]
│   ├── [+ 新建 Job] 按钮
│   ├── Job 卡片列表 (row g-3 > col-md-6):
│   │   ┌──────────────────────────────────┐
│   │   │ ✅ 爸爸日报               [开关] │
│   │   │ cron: 0 21 * * * (每日 21:00)   │
│   │   │ 用户: dad | 回溯: 1天 | 200条   │
│   │   │ 📊 本月: 30次 / 90K tokens      │
│   │   │ [测试] [触发] [编辑] [删除]      │
│   │   └──────────────────────────────────┘
│   ├── 新建/编辑 Modal (#summaryJobModal): 11 字段 + 系统/用户提示词 textarea
│   ├── 删除确认 Modal (#deleteSummaryModal)
│   └── 测试结果 Modal: summary 文本 + token 用量
├── 通知配置 (Webhook + Email 卡片式 CRUD)
├── 飞牛配置
└── Fongmi 配置
```

**关键决策：**
- 不新增页面（无 `/summary`），不新增 JS 文件（无 `summary.js`）
- 不修改 `base.html`（无需新增导航项——"配置"导航已存在）
- 不修改 `pages.py`（无需新增路由）
- 所有 Summary Job 前端逻辑内联到 `config.html` 的 `<script>` 中
- Dashboard LLM 用量 stat cards 保留

### 4. 多用户适配

- Summary job 的 `user_name` 字段传透到 `DatabaseManager.get_records_in_date_range(user_name=...)`——已有索引 `idx_sync_records_user_name`
- `user_name` 为空时汇总所有用户，notification type 为 `watching_summary`
- 指定用户时 notification type 为 `watching_summary_{user_name}`（如 `watching_summary_dad`）
- 用户通过 webhook 的 `types` 字段订阅感兴趣的用户总结，无需新增 ID 过滤功能

### 5. 成本监控 & 重试：基于现有基础设施

**两层日志，互补作用：**

| 层面 | 存储 | 用途 | 消费者 |
|------|------|------|--------|
| 结构化用量日志 | SQLite `llm_usage_logs` 表 | 聚合统计、趋势图表 | Dashboard 用量卡片 + config.html AI 追番总结区 |
| 运维日志 | `log.txt`（现有 Logger） | 错误排查、调试 | `/logs` 页面 |

**结构化表设计（仿照 `sync_records` 表模式）：**

```sql
CREATE TABLE llm_usage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    job_id INTEGER,                  -- 关联 summary job，NULL = 非 summary 调用
    job_name TEXT,                    -- summary job 名称（冗余，方便查询）
    model TEXT NOT NULL,             -- 模型名称
    provider TEXT NOT NULL DEFAULT 'openai_compat',
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,    -- 响应延迟
    status TEXT NOT NULL DEFAULT 'success',  -- success / error
    error_message TEXT               -- 失败时的错误信息
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_logs_timestamp ON llm_usage_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_llm_usage_logs_job_id ON llm_usage_logs(job_id);
```

**API 端点（聚合指标）：**

在 `/api/summary` 下新增：
- `GET /api/summary/llm/stats` — LLM 用量统计

返回格式（参考 `GET /api/stats` 模式）：
```json
{
  "status": "success",
  "data": {
    "total_calls": 150,
    "total_tokens": 450000,
    "total_prompt_tokens": 300000,
    "total_completion_tokens": 150000,
    "error_count": 3,
    "avg_latency_ms": 1200,
    "by_model": [{"model": "gpt-4o-mini", "calls": 100, "tokens": 300000}, ...],
    "by_job": [{"job_id": 1, "job_name": "每日总结", "calls": 30, "tokens": 90000}, ...],
    "daily": [{"date": "2026-07-14", "calls": 5, "tokens": 15000}, ...]
  }
}
```

**UI 展示（两层）：**

| 位置 | 展示内容 | 数据来源 |
|------|---------|---------|
| Dashboard | 新增 LLM 用量卡片组（本月调用次数/Token/平均延迟，仿现有 stat cards 模式），放在现有 4 张卡片旁边 | `GET /api/summary/llm/stats?scope=aggregate` |
| config.html | AI 追番总结区的 Job 卡片含该 job 用量小计 | `GET /api/summary/llm/stats` (含 by_job 分组) |

Dashboard 集成方式参考现有模式：
- 卡片布局：现有 4 张 `col-md-3` stat cards（total-syncs, today-syncs, success-rate, error-syncs），新增 2 张或更多 LLM stat cards 排在同一行
- 数据获取：`loadDashboardData()` 中新增 `GET /api/summary/llm/stats?scope=aggregate` 调用
- 只在 LLM 已配置时展示（api_key 非空），避免空白卡片

**重试策略：**

- 网络/API 错误：LLMClient 统一重试 2 次，指数退避 (1s, 3s)
- 每次失败记录到 `llm_usage_logs`（status=error, error_message=异常信息）
- 连续失败：静默跳过本次 job，不级联影响其他模块
- 运维日志通过现有 Logger 输出，自动出现在 `/logs` 页面

**数据清理：**

- `llm_usage_logs` 按 `timestamp` 保留 30 天自动清理（在 `DatabaseManager` 中加 cleanup 方法，scheduler 或 summary scheduler 启动时执行一次）

### 6. 数据库索引

`sync_records.timestamp` 用于范围查询。在执行 `get_records_in_date_range()` 前确保索引存在：

```sql
CREATE INDEX IF NOT EXISTS idx_sync_records_timestamp ON sync_records(timestamp);
```

在 `DatabaseManager._init_database()` 中执行，沿用现有 `_ensure_*` 迁移模式。

## Development Methodology: TDD

所有后端代码采用 TDD（测试驱动开发）模式，每个 phase 按以下流程推进：

1. **写测试** — 先写失败的测试，定义预期的接口行为
2. **跑测试确认失败** — `pytest tests/path/to/test_xxx.py -v`
3. **实现代码** — 最小实现让测试通过
4. **跑测试确认通过** — 全部绿灯
5. **重构** — 消除重复、改善可读性（保持测试通过）

测试文件命名和位置参考现有模式（`tests/utils/test_notifier.py`、`tests/services/test_feiniu_scheduler.py`）。

### 测试文件清单

| Phase | 测试文件 |
|-------|---------|
| Phase 1 | `tests/services/llm/test_models.py`, `tests/services/llm/test_provider_openai_compat.py`, `tests/services/llm/test_client.py` |
| Phase 2 | `tests/core/test_config_llm.py`, `tests/core/test_database_records_range.py` |
| Phase 3 | `tests/services/summary/test_service.py`, `tests/services/summary/test_scheduler.py` |
| Phase 4 | `tests/utils/test_notifier.py`（扩展已有测试） |
| Phase 5 | `tests/api/test_summary.py` |

## Implementation Phases

### Phase 1: LLM 基础模块（6 个新文件）

| 文件 | 内容 |
|------|------|
| `app/services/llm/__init__.py` | 导出 LLMClient, Message, ChatResponse, get_llm_client |
| `app/services/llm/models.py` | Message, Usage, ChatResponse（最小化，v1 只有 content + model + usage） |
| `app/services/llm/providers/__init__.py` | 空 |
| `app/services/llm/providers/base.py` | BaseProvider ABC — 只定义 `async def chat()` |
| `app/services/llm/providers/openai_compat.py` | OpenAICompatProvider — httpx POST，重试，usage 提取，错误处理 |
| `app/services/llm/client.py` | LLMClient 单例 — 封装 provider + 成本日志 |

### Phase 2: Config + Database（3 个修改文件）

| 文件 | 改动 |
|------|------|
| `app/core/database/sync_records.py` | +`get_records_in_date_range()` + timestamp 索引确保 |
| `app/core/database/llm_usage.py` | 新文件：`llm_usage_logs` 表创建 + `log_llm_usage()` + `get_llm_usage_stats()` + cleanup |
| `app/core/config.py` | `get_llm_config()`, `get_summary_configs()`, save/delete summary section 方法 |
| `app/core/config_secret_crypto.py` | `("llm", "api_key")` 加入敏感字段列表 |

### Phase 3: Summary 业务模块（4 个新文件）

| 文件 | 内容 |
|------|------|
| `app/services/summary/__init__.py` | 导出 SummaryService, summary_scheduler |
| `app/services/summary/models.py` | SummaryJobConfig dataclass |
| `app/services/summary/service.py` | SummaryService.generate_summary() + execute_job() |
| `app/services/summary/scheduler.py` | SummaryScheduler — 多 job 动态管理，参考 FeiniuScheduler |

### Phase 4: Notifier 扩展（3 个修改文件）

`app/utils/notifier/`:
- `webhook.py` — `_build_payload_by_type`：新增 `"watching_summary" in notification_type` 分支，返回 summary 专用 JSON payload
- `html_builders.py` — `_build_email_dynamic_content`：新增 watching_summary 邮件 HTML 内容块
- `email_sender.py` — `_build_email_subject_by_type`：新增 watching_summary 邮件标题

### Phase 5: API 层（2 个新文件）

| 文件 | 内容 |
|------|------|
| `app/models/summary.py` | Pydantic: LLMConfigResponse, LLMConfigUpdate, LLMTestResponse, SummaryJobCreate, SummaryJobUpdate, SummaryJobResponse, SummaryJobTestResponse |
| `app/api/summary.py` | FastAPI router：LLM config CRUD + test + stats，Summary job CRUD + test + trigger |

API 端点：

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/summary/llm` | 获取 LLM 配置（api_key 脱敏） |
| PUT | `/api/summary/llm` | 更新 LLM 配置 |
| POST | `/api/summary/llm/test` | 测试 LLM 连接 |
| GET | `/api/summary/llm/stats` | LLM 用量统计（聚合数据 + 按 model/job 分组 + 每日趋势） |
| GET | `/api/summary/jobs` | 列表所有 summary jobs |
| POST | `/api/summary/jobs` | 创建 job |
| PUT | `/api/summary/jobs/{id}` | 更新 job |
| DELETE | `/api/summary/jobs/{id}` | 删除 job（含 ID 重排） |
| POST | `/api/summary/jobs/{id}/test` | 测试运行（生成 + 发送） |
| POST | `/api/summary/jobs/{id}/trigger` | 手动触发 |

### Phase 6: 前端（0 个新文件，2 个修改文件）

**不新增页面，不新增 JS 文件。** 所有逻辑内联到 `config.html`（仿 webhook/email CRUD 模式）。

| 文件 | 改动 |
|------|------|
| `templates/config.html` | 新增 "LLM 配置" section（顶层，6 字段 + 测试连接）+ "AI 追番总结" 卡片式 CRUD section（Job 列表 + 新建/编辑/删除 Modal + Test Result Modal）+ 所有内联 JS（~500 lines） |
| `templates/dashboard.html` | 新增 LLM 用量 stat cards（仅在 LLM 已配置时展示） |

### Phase 7: 生命周期集成（1 个修改文件）

`app/main.py`：
- 注册 `summary_router`
- `delayed_scheduler_start()` 中加入 `summary_scheduler.start()`
- shutdown 中加入 `summary_scheduler.stop()`

## Files Summary

### New files (15)

| 文件 | 估算行数 |
|------|---------|
| `app/services/llm/__init__.py` | ~10 |
| `app/services/llm/models.py` | ~30 |
| `app/services/llm/client.py` | ~80 |
| `app/services/llm/providers/__init__.py` | ~3 |
| `app/services/llm/providers/base.py` | ~20 |
| `app/services/llm/providers/openai_compat.py` | ~100 |
| `app/services/summary/__init__.py` | ~5 |
| `app/services/summary/models.py` | ~30 |
| `app/services/summary/service.py` | ~130 |
| `app/services/summary/scheduler.py` | ~160 |
| `app/core/database/llm_usage.py` | ~80 |
| `app/models/summary.py` | ~80 |
| `app/api/summary.py` | ~250 |

### Modified files (7)

| 文件 | 改动 |
|------|------|
| `app/core/config.py` | +3 methods (~50 lines) |
| `app/core/config_secret_crypto.py` | +1 条件 (~3 lines) |
| `app/core/database/sync_records.py` | +`get_records_in_date_range()` + timestamp 索引 (~35 lines) |
| `app/utils/notifier/webhook.py` | +watching_summary payload (~20 lines) |
| `app/utils/notifier/html_builders.py` | +watching_summary email HTML (~20 lines) |
| `app/utils/notifier/email_sender.py` | +watching_summary email subject (~10 lines) |
| `app/main.py` | router + lifecycle (~30 lines) |
| `templates/config.html` | LLM 配置 section + AI 追番总结卡片式 CRUD + 所有内联 JS (~500 lines) |
| `templates/dashboard.html` | 新增 LLM 用量 stat cards (~40 lines) |

总计：~985 新行 + ~708 修改行。零新依赖（httpx 已有）。

## Verification

1. **LLM 模块单元测试**：mock httpx → 验证 provider 接口、重试、usage 提取、错误降级
2. **Summary service 单元测试**：mock LLMClient + DB → 记录格式化、prompt 组装、多用户过滤、notifier 调用
3. **API 测试**：CRUD 端点、LLM test 端点、job test/trigger 端点、llm stats 端点
4. **E2E**：
   - 配置 LLM API（Ollama 本地或 DeepSeek）
   - 创建两个 summary job：不同 user_name，配置 webhook 分别订阅 `watching_summary` 类型
   - 验证 cron 定时触发
   - 验证 config.html LLM 配置区正常工作
   - 验证 Dashboard LLM 用量卡片
   - 验证 config.html 内所有 section（LLM 配置 + AI 追番总结 CRUD）正常工作
