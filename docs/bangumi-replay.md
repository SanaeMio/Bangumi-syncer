---
title: 🔄 Bangumi Replay 待同步队列补发
order: 27
---

# 🔄 Bangumi Replay 待同步队列补发

Bangumi Replay 是一项**写降级与自动补发**功能：当 Bangumi API 不可达时，已匹配到 `subject_id` 的写操作（点在看 / 标章节）不会直接失败，而是进入本地 `pending_sync_queue` 队列暂存，待 API 恢复后由调度器自动补发。

默认**开启**（`[bangumi-replay] enabled = true`），无需额外操作即可在 API 短暂抖动时保护写操作。

## 与 Archive 的关系

::: warning 完全实现「无网缓存请求」需要 Archive 配合
Replay 与 Archive 是两个**互相独立**的功能，配置段分别为 `[bangumi-replay]` 与 `[bangumi-archive]`，可各自启停。

但**完全实现「无网缓存请求 + 自动补发」需要 Archive 配合**：

| 能力 | 仅 Replay | Replay + Archive |
| --- | --- | --- |
| API 不可达时，已匹配的写操作入队补发 | ✅ | ✅ |
| API 不可达时，匹配新条目（读操作） | ❌ 走 API 仍失败 | ✅ 命中本地数据集 |
| API 不可达时，端到端「无网匹配 + 入队 + 补发」 | ❌ 无法匹配 | ✅ 完整闭环 |

**不开 Archive 时**，Replay 仍可独立工作，但仅能在「API 已匹配到 `subject_id` 后写失败」场景下补发；无法在 API 完全不可达时匹配新条目并入队。
:::

简言之：

- **Archive 提供读降级**：API 不可达时，读操作（搜索 / 查条目）命中本地数据集，仍能匹配新条目
- **Replay 提供写降级**：API 不可达时，写操作（点在看 / 标章节）入队待补发

两者搭配才能覆盖「API 完全不可达」的端到端场景。详见 [🗄️ Bangumi Archive 离线查询层](/bangumi-archive)。

## 配置项

在 Web 的「配置管理」→「Bangumi Replay 待同步队列补发」卡片里填写，对应 `config.ini` 的 `[bangumi-replay]` 段。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关。`false` 时关闭写降级入队与定时补发；与 `[bangumi-archive] enabled` 互相独立 |
| `api_probe_interval` | `300` | API 不可达 TTL（秒）。标记后此期间内所有写操作直接入队，到期后下一次请求恢复探测 |
| `replay_cron` | `*/10 * * * *` | 待同步队列补发调度 cron（五段式），默认每 10 分钟扫描一次 |
| `replay_batch_size` | `20` | 每轮补发批量大小 |
| `max_attempts` | `50` | 单条任务最大重试次数，超过则标记为 `abandoned` |

### 推荐配置示例

```ini
[bangumi-replay]
enabled = true
api_probe_interval = 300          ; 不可达标记 TTL（秒）
replay_cron = */10 * * * *        ; 每 10 分钟补发一次
replay_batch_size = 20
max_attempts = 50
```

::: tip 配置立即生效
保存配置后会自动调用 `bangumi_replay_scheduler.apply_config_after_save()`，**无需重启程序**。`enabled` 或 `replay_cron` 变化时调度器会自动重建定时任务。
:::

## 触发条件与降级机制

写操作进入待同步队列需同时满足：

1. `[bangumi-replay] enabled` 未显式设为 `false`（默认 `true`，与 archive 解耦）
2. `BangumiApi` 实例 `_api_unreachable = true`（HTTP 层重试耗尽或收到 5xx/429 后自动置位，TTL 内不再发请求）

::: tip 写降级 vs 读降级
- **读操作**（搜索 / 查条目）→ archive 启用时优先走本地数据集；archive 未启用时仍走 API（可能失败）
- **写操作**（点在看 / 标章节）→ 进入 `pending_sync_queue` 队列，待 API 恢复后补发
:::

## TTL 与探测

- 标记不可达后，按 `api_probe_interval`（默认 300 秒）推迟下一次探测
- TTL 到期后下一次请求恢复实际探测；成功后自动清除不可达标记
- 补发调度器（`BangumiReplayScheduler`）按 `replay_cron` 定时执行：
  1. **队列空短路**：先调 `count_pending_sync()` 统计待补发条数，为 0 直接跳过本轮，避免无意义的 API 探测请求
  2. 创建临时 `BangumiApi` 实例探测 `GET /v0/subjects/1`
  3. 探测成功 → 调用 `sync_service.replay_pending_batch` 批量补发
  4. 仍不可达 → 跳过本轮，等待下一次调度

## 立即触发补发

定时调度之外，**入队成功后会自动触发一次立即补发**（`trigger_immediate_run`）：

- 入队方在写入 `pending_sync_queue` 后调用 `bangumi_replay_scheduler.trigger_immediate_run()`
- 调度器用 `add_job(trigger="date")` 立即执行一次 `_run_sync_job`，走完整的「队列计数 → 探测 → 补发」流程
- **500ms 防抖**：短时间内多次入队（如 Trakt 全量同步、批量补番）只触发一次立即执行，避免堆积
- **并发安全**：APScheduler 的 `max_instances=1, coalesce=True` 保证同一时刻只有一个补发任务在跑，触发只负责"提前唤醒"
- **失败兜底**：调度器未启动 / 未启用 / 触发异常都不影响入队本身，下一轮 cron 仍会正常补发

::: tip 实时性
立即触发让"API 抖动恢复"场景下的补发延迟从「下一个 cron 周期」降到「秒级」，用户体验接近直写成功。
:::

## WebUI 队列页

`[bangumi-replay] enabled = true` 时，导航栏与侧边栏会出现「待同步队列」入口（路径 `/bangumi-replay`），提供：

- **调度器状态栏**：展示 enabled / cron / running / 队列统计
- **过滤与列表**：按状态（pending / synced / abandoned）筛选，分页展示
- **详情弹窗**：查看单条任务的完整 payload 与匹配追踪
- **批量 / 单条补发**：手动触发补发（API 已恢复但调度器未到下一轮时有用）
- **API 探测按钮**：手动探测当前 API 可达性
- **删除**：清理不需要的任务

## 任务状态流转

```
pending  ──补发成功──→  synced
   │
   ├──补发失败（重试次数 < max_attempts）──→  仍为 pending（等待下一轮）
   │
   └──重试次数 ≥ max_attempts ──→  abandoned
```

## 单条任务去重

`pending_sync_queue` 通过 `(user_name, source, subject_id, episode_id)` 唯一索引去重。重复入队时更新 `payload_json` 与 `updated_at`，不会新增记录。

## 故障排查

### 待同步队列一直显示「API 不可达」

队列页点击「探测 API」或调度器日志一直报告不可达时，按以下顺序排查：

1. **检查 `[bangumi]` 段配置**：探测使用的账号来自 `[sync] mode` 对应的段
   - `mode = single`（默认）→ 读 `[bangumi]` 段的 `username` / `access_token`
   - `mode = multi` → 读第一个用户映射指向的 `[bangumi-*]` 段
   - 任一字段为空都会导致探测直接返回 `False`（日志：`📚 无可用账号配置用于探测 API`）
2. **检查 `[dev]` 段代理与 SSL**：`script_proxy` 不通或 `ssl_verify=false` 配置错误都会让探测请求失败
3. **手动验证账号可用性**：用相同 `access_token` 直接请求 `https://api.bgm.tv/v0/subjects/1`，确认 token 未过期（有效期 1 年）
4. **查看应用日志**：`log.txt` 中搜索 `📚 API 探测失败` 查看具体异常
5. **强制清除不可达标记**：调度器探测成功后会自动清除，但缓存的 `BangumiApi` 实例可能仍带标记，补发单条时已通过 `mark_api_reachable()` 强制清除；如仍异常可重启服务

### 入队后一直不补发

1. 确认 `[bangumi-replay] enabled` 未被设为 `false`
2. 查看调度器状态：`GET /api/bangumi_replay/status`，关注 `enabled` / `cron` / `next_run`
3. 手动触发补发：队列页点击「批量补发」或 `POST /api/bangumi_replay/replay`
4. 检查调度器是否已启动：`status.running = false` 时立即触发与定时补发都不会执行，需保存一次 Replay 配置触发 `apply_config_after_save` 或重启程序
5. 看日志是否有 `📚 待同步队列为空，本轮跳过`：说明队列被其他进程/线程先行补发完，属正常行为

## 接下来

- 想了解读降级（API 不可达时仍能匹配新条目）？看 [🗄️ Bangumi Archive 离线查询层](/bangumi-archive)。
- 想配置参数？看 [⚙️ 配置说明](/configuration) 的「Bangumi Replay 待同步队列补发」段。
- 同步失败排查？看 [🔧 常见同步失败原因](/troubleshooting)。
