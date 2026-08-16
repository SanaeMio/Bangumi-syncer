---
title: 🔄 同步服务
order: 2
---

# 🔄 同步服务

同步服务是项目的核心：所有驱动最终都将各自的数据源转换为统一的 `CustomItem`，再委托给 `SyncService.sync_custom_item()` 完成同步。

## 技术栈

- **数据模型**：[Pydantic](https://docs.pydantic.dev/) v2（`CustomItem` / `SyncResponse`）
- **匹配引擎**：[rapidfuzz](https://github.com/maxbachmann/RapidFuzz) 模糊匹配 + [regex](https://github.com/mrabarnes/regex) 超时防御
- **HTTP 客户端**：[httpx](https://www.python-httpx.org/) 调用 Bangumi API
- **架构模式**：Mixin 拆分职责，驱动委托模式

---

## CustomItem：统一数据模型

`app/models/sync.py` 中的 `CustomItem` 是**所有驱动最终输出的统一模型**。无论来自 Emby Webhook、飞牛 SQLite 还是 Trakt OAuth，最终都转换为 `CustomItem` 喂给 `SyncService`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `media_type` | `str` | `episode` / `movie` / `ova` / `oad` / `real_action` |
| `title` | `str` | 番剧标题（必填） |
| `ori_title` | `Optional[str]` | 原始标题（辅助匹配） |
| `season` | `int` | 季号（必填，剧集不允许 0） |
| `episode` | `int` | 集号（必填，不允许 0） |
| `release_date` | `str` | 发行日期（用于日期匹配，`YYYY-MM-DD`） |
| `user_name` | `str` | 媒体服务器用户名（路由到 Bangumi 账号） |
| `source` | `Optional[str]` | 来源标识（如 `"emby"`、`"feiniu"`，必须全局唯一） |
| `sync_action` | `Optional[str]` | `mark_watching` 时仅标记条目在看（剧场版场景） |
| `raw_payload` | `Optional[dict]` | 原始报文（用于「接收请求」步骤展示） |

---

## SyncService：同步主流程

`app/services/sync_service/__init__.py` 中的 `SyncService.sync_custom_item()` 是同步的**唯一入口**。

### 流水线

同步流程由**两段管线**编排（`app/services/sync_service/orchestrator.py`）：

```
sync_custom_item(item, source)
  │ 匹配阶段：MatchPipeline（app/services/matching/pipeline.py）
  ├─ 1. receive           接收请求，记录原始字段
  ├─ 2. normalize         标题归一化（去发布组/分辨率等噪声）
  ├─ 3. custom_mapping    查自定义映射（精确 + 季度感知 + 正则规则）
  ├─ 4. bangumi_data      查 bangumi-data 离线数据集
  ├─ 5. api_search        Bangumi API 搜索（含 archive 短路）
  ├─ 6. post_search       搜索后处理（媒体类型改选 / 关联条目改选）
  │ 执行阶段：SyncPipeline（app/services/sync_service/pipeline.py）
  ├─ 7. episode_resolve   季度集数解析（电影走短路径）
  ├─ 8. cross_season      跨季链查找（episode=102 等连续编号，解析未命中才执行）
  ├─ 9. sync_action       标记 Bangumi 看过（含 Replay 入队）
  └─ 10. result           结果结算（final_* 统一写入）+ 写库 + 发通知
```

每一步都通过 `MatchTrace` 记录详细过程（进入参数、输出、耗时 `elapsed_ms`），最终写入 `sync_records` 表的 `match_trace` 字段，供「匹配记录」页面展示。执行阶段每步由 `SyncPipeline._record_trace` 统一记录，`total_elapsed_ms` 覆盖包含 receive/result 的全流程。

### Mixin 拆分

`SyncService` 通过多个 Mixin 拆分职责：

| Mixin | 职责 |
| --- | --- |
| `TaskManagerMixin` | 异步任务跟踪 |
| `RetryMixin` | 标记重试 + Replay 入队 |
| `SeasonInfoMixin` | 季度信息解析 |
| `TitleNormalizeMixin` | 标题归一化 |

::: tip 不需要继承 SyncService
`SyncService` 是单例（`sync_service`），驱动**委托**它而非继承它。驱动只需调用 `sync_service.sync_custom_item(item, source)`。
:::

---

## 数据流

### Webhook 推送型（Emby / Jellyfin / Plex / Custom）

```
媒体服务器 ──POST /{Driver}/{webhook_key}──> API 层
  ├─ _verify_webhook_auth(webhook_key)  鉴权
  ├─ extractor.extract_xxx_data(raw) → CustomItem
  └─ sync_service.sync_xxx_item_async(raw_data)
       └─ 线程池执行 sync_item → 委托 sync_custom_item()
```

### 主动拉取型（飞牛 / Fongmi / Trakt）

```
APScheduler ──cron──> XxxScheduler._run_sync_job()
  ├─ reader.read_xxx() / client.fetch_xxx()  读取外部数据源
  ├─ 转换为 CustomItem 列表
  └─ 对每个 item 调用 sync_service.sync_custom_item(item, source="xxx")
```

### 标记 Bangumi 看过

```
sync_custom_item(item)
  ├─ MatchPipeline（匹配阶段，app/services/matching/）
  │   ├─ mapping_service.find_mapping()   1. 自定义映射
  │   ├─ bangumi_data.find_bangumi_id()   2. bangumi-data 离线
  │   └─ BangumiApi.bgm_search()          3. Bangumi API 搜索
  ├─ SyncPipeline（执行阶段，app/services/sync_service/steps/）
  │   ├─ _resolve_season_episode()           解析季度与集 ID
  │   ├─ find_episode_across_seasons()       跨季链回退（未命中才执行）
  │   └─ _retry_mark_episode()               标记看过（含 Replay 入队）
  ├─ _mark_subject_completed_if_needed() 全集看完时归档
  ├─ database_manager.log_sync_record()  写库
  └─ notification_service.notify()       发通知
```

---

## 调试入口

- **`test_match(item)`**：只跑匹配流水线、不写库、不发通知，返回完整 `MatchTrace`。适合在调试端点调用。
- **「同步记录」页面**：点击失败记录的「重试同步」会弹出实时 debug 日志，展示每一步。
- **日志关键字**：`同步开始: {title} S{season:02d}E{episode:02d}` / `bgm: 通过 xxx 匹配到番剧 ID` / `已标记为看过`。
