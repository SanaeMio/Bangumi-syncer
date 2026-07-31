---
title: 🔄 Bangumi Replay 待同步队列补发
order: 34
---

# 🔄 Bangumi Replay 待同步队列补发

Bangumi Replay 是一项**写降级与自动补发**功能：当 Bangumi API 不可达时，已匹配到番剧的写操作（点在看 / 标章节）不会直接失败，而是进入本地待同步队列暂存，待 API 恢复后由调度器自动补发。

默认**开启**，无需额外操作即可在 API 短暂抖动时保护写操作。

## 与 Archive 的关系

::: warning 完全实现「无网缓存请求」需要 Archive 配合
Replay 与 [Archive](/config/bangumi-archive) 是两个**互相独立**的功能，可各自启停。

但**完全实现「无网缓存请求 + 自动补发」需要 Archive 配合**：

| 能力 | 仅 Replay | Replay + Archive |
| --- | --- | --- |
| API 不可达时，已匹配的写操作入队补发 | ✅ | ✅ |
| API 不可达时，匹配新条目（读操作） | ❌ 走 API 仍失败 | ✅ 命中本地数据集 |
| API 不可达时，端到端「无网匹配 + 入队 + 补发」 | ❌ 无法匹配 | ✅ 完整闭环 |

**不开 Archive 时**，Replay 仍可独立工作，但仅能在「API 已匹配到番剧后写失败」场景下补发；无法在 API 完全不可达时匹配新条目并入队。
:::

简言之：

- **Archive 提供读降级**：API 不可达时，读操作（搜索 / 查条目）命中本地数据集，仍能匹配新条目
- **Replay 提供写降级**：API 不可达时，写操作（点在看 / 标章节）入队待补发

## 配置项

在 Web 的「配置管理」→「Bangumi Replay 待同步队列补发」卡片里填写，对应 `config.ini` 的 `[bangumi-replay]` 段。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| **启用 Replay** | 开启 | 总开关。关闭后不再入队与定时补发；与 Archive 互相独立 |
| **API 不可达 TTL (秒)** | `300` | 标记 API 不可达后的冷却时间，期间所有写操作直接入队不发请求，默认 5 分钟 |
| **补发调度 Cron** | `*/10 * * * *` | 五段式 cron，默认每 10 分钟扫描一次队列进行补发。队列为空时自动跳过本轮 |
| **批量大小** | `20` | 每轮补发最多处理的任务数量 |
| **最大重试次数** | `50` | 单条任务重试次数上限，超过后标记为 `abandoned` 不再重试 |

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
保存配置后无需重启程序。`enabled` 或 `replay_cron` 变化时调度器会自动重建定时任务。
:::

## 触发条件

写操作进入待同步队列需同时满足：

1. `[bangumi-replay] enabled` 未设为 `false`（默认开启）
2. 检测到 Bangumi API 不可达（请求重试耗尽或收到 5xx/429 后自动标记，TTL 内不再发请求）

::: tip 写降级 vs 读降级
- **读操作**（搜索 / 查条目）→ archive 启用时优先走本地数据集；archive 未启用时仍走 API（可能失败）
- **写操作**（点在看 / 标章节）→ 进入待同步队列，待 API 恢复后补发
:::

## 立即触发补发

除 cron 定时调度外，**入队成功后会自动触发一次立即补发**：

- 入队方写入队列后自动通知调度器立即执行一次补发
- **500ms 防抖**：短时间内多次入队（如 Trakt 全量同步、批量补番）只触发一次立即执行，避免堆积
- **失败兜底**：调度器未启动 / 未启用 / 触发异常都不影响入队本身，下一轮 cron 仍会正常补发

::: tip 实时性
立即触发让"API 抖动恢复"场景下的补发延迟从「下一个 cron 周期」降到「秒级」，用户体验接近直写成功。
:::

## WebUI 队列页

`[bangumi-replay] enabled = true` 时，导航栏与侧边栏会出现「待同步队列」入口（路径 `/bangumi-replay`），提供：

- **调度器状态栏**：展示开关 / Cron / 运行状态 / 队列统计
- **过滤与列表**：按状态（pending / synced / abandoned）筛选，分页展示
- **详情弹窗**：查看单条任务的完整数据与匹配追踪
- **批量 / 单条补发**：手动触发补发（API 已恢复但调度器未到下一轮时有用）
- **API 探测按钮**：手动探测当前 API 可达性
- **删除**：清理不需要的任务

## 任务状态

```
pending  ──补发成功──→  synced
   │
   ├──补发失败（重试次数 < max_attempts）──→  仍为 pending（等待下一轮）
   │
   └──重试次数 ≥ max_attempts ──→  abandoned（不再重试）
```

## 常见问题

### 待同步队列一直显示「API 不可达」

队列页点击「探测 API」或调度器日志一直报告不可达时，按以下顺序排查：

1. **检查 Bangumi 账号配置**：探测使用的账号来自 `[sync] mode` 对应的段
   - `mode = single`（默认）→ 读 `[bangumi]` 段的 `username` / `access_token`
   - `mode = multi` → 读第一个用户映射指向的 `[bangumi-*]` 段
   - 任一字段为空都会导致探测直接返回失败（日志：`📚 无可用账号配置用于探测 API`）
2. **检查代理与 SSL**：`[dev] script_proxy` 不通或 `ssl_verify=false` 配置错误都会让探测请求失败
3. **手动验证账号可用性**：用相同 `access_token` 直接请求 `https://api.bgm.tv/v0/subjects/1`，确认 token 未过期（有效期 1 年）
4. **查看应用日志**：`log.txt` 中搜索 `📚 API 探测失败` 查看具体异常
5. **强制清除不可达标记**：调度器探测成功后会自动清除；如仍异常可重启服务

### 入队后一直不补发

1. 确认 `[bangumi-replay] enabled` 未被设为 `false`
2. 查看调度器状态：`GET /api/bangumi_replay/status`，关注 `enabled` / `cron` / `next_run`
3. 手动触发补发：队列页点击「批量补发」或 `POST /api/bangumi_replay/replay`
4. 检查调度器是否已启动：`status.running = false` 时立即触发与定时补发都不会执行，需保存一次 Replay 配置触发重启或重启程序
5. 看日志是否有 `📚 待同步队列为空，本轮跳过`：说明队列被其他进程/线程先行补发完，属正常行为

### 队列堆积过多

- 单条任务最大重试次数由 `max_attempts` 控制（默认 50），超过后标记为 `abandoned` 不再重试
- 可在队列页手动删除不需要的任务（如已用映射解决的旧失败记录）
- 长时间堆积通常意味着 Bangumi API 长期不可达，建议先解决 API 可达性问题（代理 / token / 网络）

## 接下来

- 想了解读降级（API 不可达时仍能匹配新条目）？看 [🗄️ Bangumi Archive 离线查询层](/config/bangumi-archive)。
- 想配置参数？看 [⚙️ 配置说明](/config/configuration) 的「Bangumi Replay 待同步队列补发」段。
- 同步失败排查？看 [🔧 常见同步失败原因](/troubleshooting)。
