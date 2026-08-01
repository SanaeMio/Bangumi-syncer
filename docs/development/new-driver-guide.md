---
title: 🧩 新驱动接入指南
order: 9
---

# 🧩 新驱动接入指南

如何为 Bangumi-syncer 接入新的媒体服务器驱动。

## 技术栈

- **数据模型**：[Pydantic](https://docs.pydantic.dev/) v2（`CustomItem` / `SyncResponse`）
- **Web 框架**：[FastAPI](https://fastapi.tiangolo.com/)（Webhook 型注册路由）
- **调度器**：[APScheduler](https://apscheduler.readthedocs.io/)（拉取型继承 `BaseScheduler`）
- **架构模式**：驱动委托模式（所有驱动最终委托 `SyncService.sync_custom_item()`）

---

## 驱动类型

| 类型 | 特征 | 示例 | 需要的组件 |
| --- | --- | --- | --- |
| **Webhook 推送型** | 媒体服务器主动推送事件 | Emby / Jellyfin / Plex / Custom | `models.py` + `extractor.py` + `sync_service.py` |
| **主动拉取型** | 定时读取外部数据源 | 飞牛 / Fongmi / Trakt | `models.py` + `reader.py` + `sync_service.py` + `scheduler.py` |

数据流：

```
Webhook 型:  媒体服务器 ─POST→ API 鉴权 → extractor → CustomItem → SyncService
拉取型:      Scheduler ─cron→ reader/client → CustomItem → SyncService
```

---

## 接入步骤

以接入 `mydriver` 为例。

### 1. 创建子包目录

```
app/services/mydriver/
├── __init__.py
├── models.py          # 数据模型
├── extractor.py       # Webhook 型：数据提取；拉取型改用 reader.py
├── sync_service.py    # 同步服务（必须）
└── scheduler.py       # 仅拉取型
```

### 2. 实现数据模型（models.py）

定义该驱动接收的原始数据结构（Pydantic 或 dataclass）：

```python
from pydantic import BaseModel, Field

class MyDriverWebhookData(BaseModel):
    event: str = Field(..., description="事件类型")
    title: str = Field(..., description="番剧标题")
    season: int = Field(..., description="季数")
    episode: int = Field(..., description="集数")
    user_name: str = Field(..., description="用户名")
```

### 3. 实现数据提取（extractor.py / reader.py）

将驱动专有数据转换为统一的 `CustomItem`：

```python
from ...models.sync import CustomItem

def extract_mydriver_data(raw: dict) -> CustomItem:
    return CustomItem(
        media_type="episode",
        title=raw["title"],
        season=raw["season"],
        episode=raw["episode"],
        user_name=raw["user_name"],
        source="mydriver",          # 必须全局唯一
    )
```

### 4. 实现同步服务（sync_service.py）

核心逻辑：校验 → 提取 CustomItem → 委托共享 SyncService。

```python
from ...core.logging import logger
from ...models.sync import SyncResponse
from .extractor import extract_mydriver_data

MYDRIVER_SYNC_SOURCE = "mydriver"

class MyDriverSyncService:
    def sync_item(self, raw_data: dict, sync_svc=None) -> SyncResponse:
        if sync_svc is None:
            from ..sync_service import sync_service as sync_svc
        try:
            # 校验必要字段
            for field in ("title", "season", "episode", "user_name"):
                if field not in raw_data:
                    return SyncResponse(status="error", message=f"缺少必要字段: {field}")

            # 事件过滤（按需）
            if raw_data.get("event") != "playback.completed":
                return SyncResponse(status="ignored", message="事件无需同步")

            # 委托共享 SyncService
            item = extract_mydriver_data(raw_data)
            return sync_svc.sync_custom_item(item, source=MYDRIVER_SYNC_SOURCE)
        except Exception as e:
            logger.error(f"MyDriver 同步出错: {e}")
            return SyncResponse(status="error", message=str(e))

mydriver_sync_service = MyDriverSyncService()
```

::: tip 拉取型 `run_sync()` 模式
拉取型驱动的 `sync_service` 提供 `async run_sync() -> BatchSyncResult`：读取数据源 → 转换为 `CustomItem` 列表 → 逐个委托 `sync_custom_item()` → 统计 synced/skipped/error + 进程内去重。参考 [feiniu/sync_service.py](https://github.com/SanaeMio/Bangumi-syncer/blob/main/app/services/feiniu/sync_service.py)。
:::

### 5. 注册 API 端点（仅 Webhook 型）

在 `app/api/sync.py` 加路由，鉴权用全局 `webhook_key`：

```python
@root_router.post("/MyDriver/{webhook_key}", status_code=202)
async def mydriver_sync(request: Request, webhook_key: str):
    if not await _verify_webhook_auth(webhook_key):
        return Response(content='{"status":"error","message":"认证失败"}',
                        status_code=401, media_type="application/json")
    raw_data = json.loads(await request.body())
    task_id = await sync_service.sync_mydriver_item_async(raw_data)
    return {"status": "accepted", "task_id": task_id}
```

在 `app/services/sync_service.py` 加委托方法：

```python
async def sync_mydriver_item_async(self, raw_data: dict) -> str:
    return await self._submit_async("mydriver", mydriver_sync_service.sync_item, raw_data)
```

### 6. 实现调度器（仅拉取型）

继承 `BaseScheduler`，实现 3 个类属性 + 3 个方法（详见 [⏰ 调度器框架](./scheduler)）：

```python
from ..base.scheduler import BaseScheduler
from ..base.notifier_helpers import notify_batch_sync_summary, notify_scheduler_failure

class MyDriverScheduler(BaseScheduler):
    JOB_ID = "mydriver_sync"
    DEFAULT_CRON = "*/10 * * * *"
    DRIVER_NAME = "MyDriver"

    def _is_enabled(self) -> bool:
        cfg = config_manager.get_mydriver_config()
        if not cfg.get("enabled"):
            return False
        return bool(cfg.get("api_url"))   # 校验外部依赖

    def _get_driver_config(self) -> dict:
        return config_manager.get_mydriver_config()

    async def _run_sync_job(self) -> None:
        # 基类已处理超时；子类只管业务，异常不要向上抛
        ...

mydriver_scheduler = MyDriverScheduler()
```

### 7. 注册配置与调度器

**ConfigManager 读取方法**（`app/core/config.py`）：

```python
def get_mydriver_config(self) -> dict:
    return {
        "enabled": self.get("mydriver", "enabled", fallback=False),
        "sync_interval": self.get("mydriver", "sync_interval", fallback="*/10 * * * *"),
        "api_url": self.get("mydriver", "api_url", fallback=""),
    }
```

**SectionMeta 注册**（`app/core/config_schema.py`，**关键**）—— 一行覆盖前端 TOC / 敏感字段加密 / 调度器联动 / env 覆盖：

```python
"mydriver": SectionMeta(
    name="mydriver",
    display_name="MyDriver 同步",
    order=130,
    scheduler_id="mydriver",            # 配置保存后联动调度器
    env_overrides={"enabled": "MYDRIVER_ENABLED", "api_url": "MYDRIVER_API_URL"},
    fields=(
        FieldMeta(name="enabled", loose_true=True),
        FieldMeta(name="sync_interval", default="*/10 * * * *"),
    ),
),
```

**默认配置节**（`config.example.ini`）：

```ini
[mydriver]
enabled = False
sync_interval = */10 * * * *
api_url =
```

**调度器注册**（`app/services/scheduler_bootstrap.py`，仅拉取型）：

```python
from .mydriver.scheduler import mydriver_scheduler

scheduler_registry.register_spec(
    JobSpec(scheduler_id="mydriver", runner=mydriver_scheduler)
)
```

注册后，用户在 Web 改 cron 保存即自动重建任务，**无需重启**。

### 8. 编写测试

详见 [🧪 测试与 CI](./testing)。至少覆盖：模型解析、extractor/reader、sync_service 委托、scheduler 启停。

---

## 常见参数

### SectionMeta 关键字段

| 字段 | 含义 |
| --- | --- |
| `order` | 前端 TOC 排序权重，越小越靠前 |
| `scheduler_id` | 关联调度器，配置保存后自动联动 |
| `is_multi_instance` | 多实例段（如 `notify-webhook-1`） |
| `sensitive_fields` | 敏感字段集合，写入时自动加密 |
| `env_overrides` | Docker 环境变量覆盖映射 |
| `fields` | 字段元数据列表（`FieldMeta`） |

### FieldMeta 布尔语义

| 参数 | 含义 |
| --- | --- |
| `default=...` | 字段缺失/空字符串时回填的默认值 |
| `default_true=True` | 默认 true：仅显式 `false` 时取消勾选 |
| `loose_true=True` | 字符串 `'true'` 宽松匹配，undefined 视为 false |

### BaseScheduler 类属性

| 参数 | 说明 | 示例 |
| --- | --- | --- |
| `JOB_ID` | 任务唯一标识 | `"feiniu_trimmedia_sync"` |
| `DEFAULT_CRON` | 默认 cron 表达式 | `"*/15 * * * *"` |
| `DRIVER_NAME` | 日志与通知中展示的名称 | `"飞牛"` |

---

## 常见坑

### 1. 不要直接调用 Bangumi API

所有 Bangumi 调用必须通过 `sync_service.sync_custom_item()` 委托。直接调用会跳过权限校验、屏蔽关键词、用户路由，不写 `sync_records` 表，不发通知、不触发 Replay 入队，`MatchTrace` 缺失。

### 2. `source` 必须全局唯一

`CustomItem.source` 写入 `sync_records.source`，用于 dashboard 统计、记录筛选、通知模板 `{source}` 占位符。每个驱动的 `XXX_SYNC_SOURCE` 常量不要复用。

### 3. `_is_enabled()` 要校验外部依赖

不能只看 `enabled=True`，还要校验 db_path 存在 / API 地址非空 等，否则配置不完整时调度器空跑刷屏。

### 4. 异常不要向上抛

`sync_item()` 内部 try/except，异常时返回 `SyncResponse(status="error")`。向上抛会让 API 层返回 500，且不写 `sync_records`，用户看不到失败原因。

### 5. Webhook 鉴权用全局 `webhook_key`

不要在驱动配置里另起 key 字段，会造成配置分裂。统一用 `[auth] webhook_key` + `_verify_webhook_auth()`。

### 6. 进程内去重（拉取型）

同一资源在多轮调度中被重复读取时，需在 `sync_service` 维护进程内 set 去重。参考 `feiniu_sync_service` 的水位机制和 `fongmi_sync_service` 的 `_synced_keys`。

### 7. 不要自己实现配置热更新

`SectionMeta.scheduler_id` 会自动触发 `apply_config_after_save()` 重建任务。不要在驱动里监听配置变更。

---

## 参考实现

| 驱动 | 类型 | 特点 |
| --- | --- | --- |
| [Custom](https://github.com/SanaeMio/Bangumi-syncer/blob/main/app/services/custom/) | Webhook（最简） | 直接接收 CustomItem，无 extractor/scheduler |
| [Emby](https://github.com/SanaeMio/Bangumi-syncer/blob/main/app/services/emby/) | Webhook | 标准 webhook 型，含 extractor |
| [飞牛](https://github.com/SanaeMio/Bangumi-syncer/blob/main/app/services/feiniu/) | 拉取 | 读 SQLite，含 reader + scheduler |
| [Fongmi](https://github.com/SanaeMio/Bangumi-syncer/blob/main/app/services/fongmi/) | 拉取 | HTTP 轮询 + 设备发现 |
| [Trakt](https://github.com/SanaeMio/Bangumi-syncer/blob/main/app/services/trakt/) | 拉取 | OAuth 认证 |

**推荐阅读顺序**：先看 `custom/`（最简，<100 行）→ 再看 `emby/`（标准 webhook）→ 最后看 `feiniu/`（标准拉取型）。
