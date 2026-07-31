---
title: ⏰ 调度器框架
order: 3
---

# ⏰ 调度器框架

拉取型驱动（飞牛 / Fongmi / Trakt）通过继承 `BaseScheduler` 接入定时调度。

## 技术栈

- **调度引擎**：[APScheduler](https://apscheduler.readthedocs.io/)（`AsyncIOScheduler`）
- **触发器**：`CronTrigger`（标准 5 字段 cron 表达式）
- **时区**：默认 `Asia/Shanghai`，由 `[scheduler] timezone` 或 `TZ` 环境变量覆盖

---

## BaseScheduler：如何实现子类

`app/services/base/scheduler.py` 封装了 APScheduler 的所有公共逻辑。子类只需定义 3 个类属性 + 实现 3 个方法：

```python
from ..base.scheduler import BaseScheduler

class MyDriverScheduler(BaseScheduler):
    JOB_ID = "mydriver_sync"            # 任务唯一标识
    DEFAULT_CRON = "*/10 * * * *"       # 默认 cron
    DRIVER_NAME = "MyDriver"            # 日志名

    def _is_enabled(self) -> bool:
        """是否启用（不能只看 enabled，还要校验外部依赖）"""
        cfg = config_manager.get_mydriver_config()
        if not cfg.get("enabled"):
            return False
        return bool(cfg.get("api_url"))  # 必须填了 API 地址

    def _get_driver_config(self) -> dict:
        """获取驱动配置"""
        return config_manager.get_mydriver_config()

    async def _run_sync_job(self) -> None:
        """执行同步（基类已包超时控制，子类只管业务）"""
        result = await mydriver_sync_service.run_sync()
        notify_batch_sync_summary("mydriver", ...)
```

### 常见参数（类属性）

| 参数 | 说明 | 示例 |
| --- | --- | --- |
| `JOB_ID` | APScheduler 任务唯一标识，用于启停与配置联动 | `"feiniu_trimmedia_sync"` |
| `DEFAULT_CRON` | 默认 cron 表达式（用户未配置时回退） | `"*/15 * * * *"` |
| `DRIVER_NAME` | 日志与通知中展示的名称 | `"飞牛"` |

### 基类自动处理

子类**无需**重复实现：

- scheduler 创建 / start / stop
- cron 解析与刷新（`apply_config_after_save`）
- 配置保存后联动重建任务
- 超时控制（从 `_scheduler_config.job_timeout` 读取，默认 300 秒）
- 失败告警（配合 `notify_scheduler_failure`）

### `_run_sync_job` 推荐模式

```python
async def _run_sync_job(self) -> None:
    if not self._is_enabled():
        logger.debug("MyDriver 未启用，跳过定时同步")
        return
    timeout = self._scheduler_config.get("job_timeout", 300)
    try:
        result = await asyncio.wait_for(
            mydriver_sync_service.run_sync(), timeout=timeout
        )
        notify_batch_sync_summary(
            "mydriver",
            total=result.synced_count + result.skipped_count + result.error_count,
            succeeded=result.synced_count,
            failed=result.error_count,
            skipped=result.skipped_count,
        )
    except asyncio.TimeoutError:
        logger.error(f"MyDriver 定时同步超时 ({timeout} 秒)")
        notify_scheduler_failure("mydriver", f"定时同步超时 ({timeout} 秒)", timeout=True)
    except Exception as e:
        logger.error(f"MyDriver 定时同步失败: {e}")
        notify_scheduler_failure("mydriver", str(e))
```

::: tip 异常不要向上抛
`_run_sync_job` 内部要 try/except 包裹，超时和异常都发 `notify_scheduler_failure` 告警。向上抛出会让调度器卡死。
:::

---

## SchedulerRegistry：注册表

`app/core/scheduler_registry.py` 是所有调度器的**单一入口**。两种注册模式：

| 方法 | 适用场景 | 示例 |
| --- | --- | --- |
| `register_spec(JobSpec)` | INI 驱动的单 job 调度器 | feiniu / fongmi / archive / replay |
| `register_instance(scheduler_id, scheduler)` | 命令式调度器 | trakt / summary |

### 注册位置

在 `app/services/scheduler_bootstrap.py` 的 `register_all()` 中添加：

```python
from .mydriver.scheduler import mydriver_scheduler

scheduler_registry.register_spec(
    JobSpec(scheduler_id="mydriver", runner=mydriver_scheduler)
)
```

注册后，`main.py` 只需：

```python
await scheduler_registry.start_all()      # 启动
await scheduler_registry.stop_all()       # 停止
await scheduler_registry.apply_config_by_section("feiniu")  # 配置保存后联动
```

### 配置保存联动

`SectionMeta.scheduler_id` 字段让用户改 cron 后自动触发对应调度器的 `apply_config_after_save()`，按新 cron 重建任务，**无需重启程序**。

---

## 时区

`BaseScheduler` 默认用 `[scheduler]` 段的 `timezone`（默认 `Asia/Shanghai`）。优先级：

1. `config.ini [scheduler] timezone`
2. `TZ` 环境变量
3. `Asia/Shanghai`

cron 表达式按该时区解释，不要在驱动里单独处理时区。
