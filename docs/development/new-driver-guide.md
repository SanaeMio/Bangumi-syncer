---
title: 新驱动接入指南
order: 1
---

# 新驱动接入指南

本文档介绍如何为 Bangumi-syncer 接入新的媒体服务器驱动。

## 架构概览

所有驱动最终都将各自的数据源转换为统一的 `CustomItem` 模型，再委托给共享的 `SyncService.sync_custom_item()` 完成同步。

```
外部数据源
  │
  ├─ Webhook 推送型（Emby/Jellyfin/Plex/Custom）
  │    POST /{Driver} → API 层鉴权 → 驱动 sync_service → CustomItem
  │
  └─ 主动拉取型（飞牛/Fongmi/Trakt）
       定时调度器 → 驱动 sync_service → 数据源读取 → CustomItem
  │
  ▼
SyncService.sync_custom_item(item, source)
  ├─ 权限/屏蔽校验
  ├─ 查找番剧 ID（映射 → bangumi-data → API 搜索）
  ├─ 解析季度/集 ID
  ├─ 标记 Bangumi 观看状态
  └─ 通知/记录/归档
```

## 驱动类型

| 类型 | 特征 | 示例 | 需要的组件 |
|------|------|------|-----------|
| **Webhook 推送型** | 媒体服务器主动推送事件 | Emby/Jellyfin/Plex/Custom | `models.py` + `extractor.py` + `sync_service.py` |
| **主动拉取型** | 定时读取外部数据源 | 飞牛/Fongmi/Trakt | `models.py` + `reader.py` + `sync_service.py` + `scheduler.py` |

## 接入步骤

以接入一个名为 `mydriver` 的新驱动为例。

### 1. 创建子包目录

```
app/services/mydriver/
  ├── __init__.py
  ├── models.py        # 数据模型
  ├── extractor.py     # Webhook 型：数据提取；拉取型改用 reader.py
  └── sync_service.py  # 同步服务（必须）
```

拉取型还需 `scheduler.py`。

### 2. 实现数据模型（models.py）

定义该驱动接收的原始数据结构（Pydantic 模型或 dataclass）。

```python
"""MyDriver 数据模型"""
from typing import Any, Optional
from pydantic import BaseModel, Field


class MyDriverWebhookData(BaseModel):
    """MyDriver Webhook 报文模型"""
    event: str = Field(..., description="事件类型")
    title: str = Field(..., description="番剧标题")
    season: int = Field(..., description="季数")
    episode: int = Field(..., description="集数")
    user_name: str = Field(..., description="用户名")
```

### 3. 实现数据提取（extractor.py）

将驱动专有数据转换为通用的 `CustomItem`。

```python
"""MyDriver 数据提取"""
from ...models.sync import CustomItem


def extract_mydriver_data(raw: dict) -> CustomItem:
    """将 MyDriver 报文转换为 CustomItem"""
    return CustomItem(
        media_type="episode",
        title=raw["title"],
        ori_title=raw.get("ori_title"),
        season=raw["season"],
        episode=raw["episode"],
        release_date=raw.get("release_date", ""),
        user_name=raw["user_name"],
        source="mydriver",
    )
```

### 4. 实现同步服务（sync_service.py）

核心逻辑：校验数据 → 提取 CustomItem → 委托给共享 SyncService。

```python
"""MyDriver 同步服务"""
from __future__ import annotations

from ...core.logging import logger
from ...models.sync import SyncResponse
from .extractor import extract_mydriver_data

MYDRIVER_SYNC_SOURCE = "mydriver"


class MyDriverSyncService:
    """MyDriver 同步服务

    由共享 SyncService 的 sync_mydriver_item 方法委托调用。
    异步任务跟踪仍由 SyncService 负责。
    """

    def sync_item(self, raw_data: dict, sync_svc=None) -> SyncResponse:
        """处理 MyDriver 同步请求"""
        if sync_svc is None:
            from ..sync_service import sync_service as sync_svc
        try:
            # 1. 校验必要字段
            required = ["title", "season", "episode", "user_name"]
            for field in required:
                if field not in raw_data:
                    return SyncResponse(
                        status="error", message=f"缺少必要字段: {field}"
                    )

            # 2. 事件过滤（按需）
            event = raw_data.get("event", "")
            if event != "playback.completed":
                return SyncResponse(status="ignored", message=f"事件 {event} 无需同步")

            # 3. 提取并转换
            custom_item = extract_mydriver_data(raw_data)

            # 4. 委托给共享 SyncService
            return sync_svc.sync_custom_item(custom_item, source=MYDRIVER_SYNC_SOURCE)

        except Exception as e:
            logger.error(f"MyDriver 同步处理出错: {e}")
            return SyncResponse(status="error", message=f"处理失败: {str(e)}")


mydriver_sync_service = MyDriverSyncService()
```

### 5. 实现 __init__.py

```python
from .extractor import extract_mydriver_data
from .models import MyDriverWebhookData
from .sync_service import mydriver_sync_service

__all__ = ["extract_mydriver_data", "MyDriverWebhookData", "mydriver_sync_service"]
```

### 6. 注册 API 端点（Webhook 型）

在 `app/api/sync.py` 中添加路由和委托方法：

```python
from ..services.mydriver import mydriver_sync_service


@root_router.post("/MyDriver/{webhook_key}", status_code=202)
async def mydriver_sync(request: Request, webhook_key: str):
    """MyDriver Webhook 接口"""
    if not await _verify_webhook_auth(webhook_key):
        return Response(
            content='{"status": "error", "message": "认证失败"}',
            status_code=401,
            media_type="application/json",
        )
    body = await request.body()
    raw_data = json.loads(body)
    task_id = await sync_service.sync_mydriver_item_async(raw_data)
    return {"status": "accepted", "message": "MyDriver 同步请求已接收", "task_id": task_id}
```

在 `app/services/sync_service.py` 中添加委托方法：

```python
async def sync_mydriver_item_async(self, raw_data: dict) -> str:
    """异步处理 MyDriver 同步"""
    return await self._submit_async(
        "mydriver", mydriver_sync_service.sync_item, raw_data
    )

def sync_mydriver_item(self, raw_data: dict) -> SyncResponse:
    """同步处理 MyDriver 同步"""
    return mydriver_sync_service.sync_item(raw_data, self)
```

### 7. 实现调度器（仅拉取型）

拉取型驱动需继承 `BaseScheduler`：

```python
"""MyDriver 定时同步"""
from __future__ import annotations

import asyncio

from ...core.config import config_manager
from ...core.logging import logger
from ..base.scheduler import BaseScheduler
from .sync_service import mydriver_sync_service


class MyDriverScheduler(BaseScheduler):
    JOB_ID = "mydriver_sync"
    DEFAULT_CRON = "*/10 * * * *"
    DRIVER_NAME = "MyDriver"

    def _is_enabled(self) -> bool:
        cfg = config_manager.get_mydriver_config()
        return cfg.get("enabled", False)

    def _get_driver_config(self) -> dict:
        return config_manager.get_mydriver_config()

    async def _run_sync_job(self) -> None:
        if not self._is_enabled():
            return
        timeout = self._scheduler_config.get("job_timeout", 300)
        try:
            await asyncio.wait_for(
                mydriver_sync_service.run_sync(), timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.error(f"MyDriver 定时同步超时 ({timeout} 秒)")


mydriver_scheduler = MyDriverScheduler()
```

### 8. 添加配置项

在 `app/core/config.py` 中添加配置读取方法：

```python
def get_mydriver_config(self) -> dict:
    """获取 MyDriver 配置"""
    return {
        "enabled": self.get("mydriver", "enabled", fallback=False),
        "sync_interval": self.get("mydriver", "sync_interval", fallback="*/10 * * * *"),
        # ... 其他配置项
    }
```

在 `config.ini` 中添加默认配置节：

```ini
[mydriver]
enabled = False
sync_interval = */10 * * * *
```

### 9. 编写测试

在 `tests/services/mydriver/` 下创建测试：

```python
"""MyDriver 同步服务测试"""
from unittest.mock import MagicMock, patch

from app.services.mydriver.sync_service import MyDriverSyncService


def test_sync_item_success():
    """测试同步成功"""
    with patch("app.services.mydriver.sync_service.sync_service") as mock_svc:
        mock_result = MagicMock()
        mock_result.status = "success"
        mock_svc.sync_custom_item.return_value = mock_result

        svc = MyDriverSyncService()
        result = svc.sync_item({
            "title": "测试番剧",
            "season": 1,
            "episode": 1,
            "user_name": "user",
            "event": "playback.completed",
        })

        assert result.status == "success"
        mock_svc.sync_custom_item.assert_called_once()
```

## 参考实现

| 驱动 | 类型 | 路径 | 特点 |
|------|------|------|------|
| **Custom** | Webhook（最简） | `app/services/custom/` | 无 extractor/scheduler，直接接收 CustomItem |
| **Emby** | Webhook | `app/services/emby/` | 标准 webhook 型，含 extractor |
| **Jellyfin** | Webhook | `app/services/jellyfin/` | 与 Emby 类似 |
| **Plex** | Webhook | `app/services/plex/` | 报文为 XML/表单，需特殊解析 |
| **飞牛** | 拉取 | `app/services/feiniu/` | 读取 SQLite，含 reader.py + scheduler.py |
| **Fongmi** | 拉取 | `app/services/fongmi/` | HTTP API 轮询，含 client.py + scheduler.py |
| **Trakt** | 拉取 | `app/services/trakt/` | OAuth 认证，含 auth.py + client.py |

## 关键约定

1. **来源标识**：每个驱动定义 `XXX_SYNC_SOURCE` 常量（如 `"emby"`、`"feiniu"`），用于日志和数据库记录
2. **委托模式**：驱动 sync_service 不直接调用 Bangumi API，统一委托给 `sync_service.sync_custom_item()`
3. **异常处理**：驱动 sync_service 捕获异常后返回 `SyncResponse(status="error")`，不向上抛出
4. **调度器继承**：拉取型驱动继承 `BaseScheduler`，只需实现 4 个抽象方法
5. **鉴权统一**：Webhook 型驱动使用全局 `webhook_key` 鉴权，通过 `_verify_webhook_auth()` 验证
