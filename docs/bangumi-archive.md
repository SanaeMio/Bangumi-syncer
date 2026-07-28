---
title: 🗄️ Bangumi Archive 离线查询层
order: 26
---

# 🗄️ Bangumi Archive 离线查询层

Bangumi Archive 是一项**本地归档**功能：把 [Bangumi 官方 Archive 项目](https://github.com/bangumi/Archive) 发布的全站数据快照（约 65 万个条目、84 万条标题）下载并导入到本地 SQLite，启用后同步流程会**优先查本地**，命中即返回、未命中再回退到 Bangumi 官方 API。

启用后能带来两个直接收益：

- **更快**：本地精确查询 `<1ms`，原本要走 API 的查询延迟从秒级降到毫秒级。
- **更稳**：命中本地后**零 API 调用**，能显著降低对 Bangumi 官方 API 的依赖，避免触发频率限制、网络抖动导致的同步失败。

默认**关闭**，需要手动在「配置管理」里开启。

## 适用场景

| 场景 | 是否建议启用 | 说明 |
| --- | --- | --- |
| 大批量同步历史观看记录 | ✅ 强烈建议 | 数千条记录首次同步时，archive 可把绝大多数查询转为本地，避免 API 风控 |
| 网络环境差或不稳定 | ✅ 建议 | 命中 archive 后无需等待 API，规避 10s × 3 重试的长尾延迟 |
| 跨季续集链查找 | ✅ 建议 | `try_find_sequel_chain` 一次拿完整续集链，避免逐跳 API 调用（最多 30 跳） |
| 磁盘空间紧张（可用 <2GB） | ⚠️ 不建议 | 双库 + 索引缓存 + 临时下载文件合计约 **4.6GB**，详见下文「磁盘占用」 |
| 排查匹配问题 | ⚠️ 可临时关闭 | 关闭后走纯 API 路径，便于对比是 archive 数据问题还是 API 数据问题 |
| 调试环境 | ❌ 建议关闭 | 测试框架默认强制禁用，避免后台索引构建阻塞测试 |

## 配置项

在 Web 的「配置管理」→「Bangumi Archive 离线查询层」卡片里填写，对应 `config.ini` 的 `[bangumi-archive]` 段。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 总开关。关闭后所有 `try_*` 短路立即返回 `archive_disabled`，行为与未接入 archive 完全一致 |
| `data_dir` | `./data/archive` | 数据目录，存放双库 `bangumi_archive_a.db` / `bangumi_archive_b.db`、`bangumi_archive.active` 指针、`bangumi_archive.meta` 元数据与 `.index` 索引缓存 |
| `http_proxy` | （空） | 下载 dump zip 时使用的 HTTP 代理。**留空则自动继承 `[dev] script_proxy`**，建议国内用户配置 |
| `ssl_verify` | `true` | 下载时是否校验 SSL 证书。自签名环境可关闭 |
| `update_cron` | `0 8 * * 3` | 定时更新 cron（五段式），默认每周三 08:00（北京时间），晚于官方 05:00 发布 |
| `min_disk_space_mb` | `2000` | 磁盘可用空间阈值（MB），低于此值跳过导入，避免磁盘写满 |
| `retry_interval` | `3600` | 导入失败后的重试间隔（秒） |

::: tip 配置立即生效
保存配置后会自动调用 `bangumi_archive.reload_config()` 与 `bangumi_archive_scheduler.apply_config_after_save()`，**无需重启程序**。从「关闭」切到「开启」时会自动触发首次导入与索引构建。
:::

### 推荐配置示例

```ini
[bangumi-archive]
enabled = true
data_dir = ./data/archive
http_proxy = http://127.0.0.1:7890       ; 国内建议配置代理
ssl_verify = true
update_cron = 0 8 * * 3                  ; 每周三 08:00 自动更新
min_disk_space_mb = 2000
retry_interval = 3600
```

## 待同步队列（Replay）

::: tip 独立文档
Replay 已从 Archive 拆分为独立的 `[bangumi-replay]` 配置段，可独立启停。完整的配置项、触发条件、TTL 探测、WebUI 队列页、状态流转与故障排查已迁移到 → [🔄 Bangumi Replay 待同步队列补发](/bangumi-replay)。
:::

简言之：

- **Archive 提供读降级**：API 不可达时，读操作（搜索 / 查条目）命中本地数据集，仍能匹配新条目
- **Replay 提供写降级**：API 不可达时，写操作（点在看 / 标章节）入队待补发

两者互相独立但互补：**不开 Archive 时**，Replay 仍可独立工作，但仅能在「API 已匹配到 `subject_id` 后写失败」场景下补发；无法在 API 完全不可达时匹配新条目并入队。详见 Replay 文档的「与 Archive 的关系」章节。

## 启用后的行为

### 首次启用

1. **保存配置**触发 `reload_config()`，立即生效
2. **后台导入**：从 GitHub 下载 dump zip（约 400MB，含 SHA256 校验），导入到 SQLite 双库中的 inactive 库，导入成功后切换 active 指针并清空旧库
3. **后台索引构建**：在子线程构建标题内存索引（首次约 **3-8 分钟**，磁盘缓存加载 5-10 秒），**不阻塞主流程**
4. 构建完成前 archive 查询会返回 `archive_miss` 自动降级到 API，调用方无感知

### 查询优先级

```
ArchiveShortcut.try_*  →  ArchiveStore（SQLite）  →  BangumiApi（HTTP）
        ↑                        ↑                        ↑
   命中即返回              命中即返回              兜底回退
```

`ArchiveShortcut` 提供 8 个短路方法，覆盖 `BangumiApi` 大部分读操作：

| 方法 | 用途 |
| --- | --- |
| `try_search` | 短路 v0 搜索（带 air_date 区间过滤） |
| `try_search_old` | 短路旧版搜索（仅 type 过滤） |
| `try_get_subject` | 短路条目详情查询 |
| `try_get_episodes` | 短路章节列表查询 |
| `try_get_related_subjects` | 短路关联条目查询 |
| `try_find_next_sequel_id` | 短路单跳续集查找 |
| `try_find_related_id_by_relation` | 短路按中文关联名查找 |
| `try_find_sequel_chain` | 短路续集链批量查找（避免逐跳 API） |

### 标题匹配 6 步策略

`try_search` 内部按以下顺序尝试，前一步命中合格结果即停止：

1. **媒体前缀变体优先**（仅当原始标题不以「劇場版 / 剧场版 / OVA / OAD」开头时触发）
2. **精确匹配**（原始标题 + 剥离季后缀后的标题，如「完美世界 S06E279」→「完美世界」）
3. **标题分割主段精确匹配**（按 `:` / `-` / `～` / `〜` 拆分，取主段）
4. **媒体前缀变体**（用剥离后标题拼接劇場版/OVA/OAD）
5. **剥离书名号/方括号包裹后再精确匹配**
6. **模糊兜底**（bigram 倒排索引预筛 + rapidfuzz 多 scorer 融合）

## 全量测试报告

**总览**：5570 用例，耗时 13.8s
- subject：命中 100.0%，正确 99.96%（5568/5570）
- episode：命中 100.0%，正确 100.0%（5570/5570）
- **联合正确率：99.96%（5568/5570）**

| 场景 | 用例 | subj正确 | ep正确 | 联合率 | P50ms |
|------|------|----------|--------|--------|-------|
| P1 单季动画 | 1984 | 1983 | 1984 | 99.9% | 1.43ms |
| P2 季数后缀剥离 | 592 | 592 | 592 | 100.0% | 1.94ms |
| P3 跨季续集链 | 774 | 774 | 774 | 100.0% | 1.85ms |
| P4 长篇动画 | 232 | 232 | 232 | 100.0% | 2.69ms |
| P5 电影本篇 | 1988 | 1987 | 1988 | 99.9% | 0.77ms |

## 磁盘占用

| 项目 | 大小 |
| --- | --- |
| `bangumi_archive_a.db` + `bangumi_archive_b.db`（双库互备） | 约 2.8GB（各 ~1.4GB） |
| `bangumi_archive_a.index` + `bangumi_archive_b.index`（索引缓存） | 约 1GB（各 ~0.5GB） |
| 临时下载的 dump zip | 约 0.4GB |
| **合计峰值占用** | **约 4.6GB** |

导入成功切换 active 指针后，旧库（db / db-wal / db-shm）与对应的 `.index` 缓存会自动清理，常态占用约 **3.3GB**。

`min_disk_space_mb = 2000` 是**导入前**的硬性阈值，低于此值会跳过导入并记录错误日志。

## 数据存储结构

`data_dir`（默认 `./data/archive`）目录下包含：

```
data/archive/
├── bangumi_archive_a.db          # 双库 a
├── bangumi_archive_b.db          # 双库 b
├── bangumi_archive_a.index       # 标题索引缓存 a（format_version=3）
├── bangumi_archive_b.index       # 标题索引缓存 b
├── bangumi_archive.active        # 当前 active 指针（仅含 "a" 或 "b"）
└── bangumi_archive.meta          # JSON 元数据（dump 信息、行数、错误等）
```

### 双库设计

- 双库 `a` / `b` 互为备份，循环写入
- 导入时写入 **inactive** 库，导入成功后切换 active 指针，再清空旧库
- 切换通过原子写入 `bangumi_archive.active` 文件完成
- **导入期间零停服**：旧库持续提供查询，新库导入完成后无缝切换

## 性能基线

| 操作 | 耗时 |
| --- | --- |
| 精确标题查询 | `<1ms`（O(1) 哈希查找） |
| 模糊标题查询（bigram 预筛 + rapidfuzz） | `5-15ms`（原 50-300ms） |
| 首次从 DB 构建标题索引 | 约 `111s`（主要耗时在 `parse_infobox`） |
| 从磁盘缓存加载索引 | `5-10s` |

### 关键限制

- `ArchiveShortcut._MAX_IDS_TO_FETCH = 200`：精确命中大量同名 subject 时限制遍历数，避免极端场景拖慢查询
- `BangumiApi` HTTP 超时 `10s × max_retries=3`：archive 未命中降级到 API 时的延迟上限
- `find_episode_across_seasons` 整体 deadline `60s`
- 续集链遍历 `_SEQUEL_CHAIN_MAX_HOPS = 30` + visited set 防环

## 故障排查

### 启用后一直未生效

1. **查状态**：`GET /api/bangumi_archive/status`，关注 `enabled` / `last_error` / `import_in_progress` / `current_progress`
2. **看进度日志**：`GET /api/bangumi_archive/progress_log?task_id=xxx` 查看完整阶段变化
3. **常见原因**：
   - 磁盘空间不足（`min_disk_space_mb` 阈值未达到）
   - 网络代理配置错误（`http_proxy` 留空但 `[dev] script_proxy` 也未配置）
   - GitHub 下载失败（镜像源 fallback 链均不通）
4. **手动处理**：网络受限时下载 zip 后通过 `/api/bangumi_archive/import_local` 上传

### 导入失败后重试

- `retry_interval = 3600`：失败后 1 小时自动重试
- 也可直接 `POST /api/bangumi_archive/trigger?force=true` 立即重试

### 索引未就绪降级到 API

archive 启用但标题索引未构建完成时，`try_search` / `try_search_old` 会返回 `archive_miss` 自动降级到 API，**不影响同步成功率**，只是查询走 API 较慢。其他 `try_get_*` 方法（直接查 SQLite 不依赖标题索引）可正常工作。

### 数据陈旧

- Bangumi 官方每周三 05:00（北京时间）发布新 dump
- 默认 `update_cron = 0 8 * * 3` 每周三 08:00 自动拉取
- 如需立即更新：`POST /api/bangumi_archive/trigger?force=true`

### 同步记录显示 archive 命中但结果不对

archive 数据来自 Bangumi 官方 dump，可能存在数据延迟或边缘情况。可以：

1. 临时关闭 archive 走纯 API 路径，对比结果
2. 在「调试工具」用「测试同步」功能复现问题
3. 必要时到 [GitHub Issues](https://github.com/SanaeMio/Bangumi-syncer/issues) 反馈

### 待同步队列一直显示「API 不可达」

Replay 待同步队列的故障排查（API 探测、账号配置、代理与 SSL、强制清除不可达标记等）已迁移到 → [🔄 Bangumi Replay 待同步队列补发 · 故障排查](/bangumi-replay#故障排查)。

## Docker 部署提示

Docker 部署时建议把 `data_dir` 挂载到宿主机，避免容器重建时数据丢失：

```yaml
volumes:
  - ./data:/app/data
```

默认 `data_dir = ./data/archive`，对应容器内 `/app/data/archive`。

## 接下来

- 想了解写降级与待同步队列补发？看 [🔄 Bangumi Replay 待同步队列补发](/bangumi-replay)。
- 想了解整体匹配流程？看 [🔀 自定义映射](/mapping) 与 [🔧 常见同步失败原因](/troubleshooting)。
- 想配置参数？看 [⚙️ 配置说明](/configuration) 的「Bangumi Archive 离线查询层」段。
