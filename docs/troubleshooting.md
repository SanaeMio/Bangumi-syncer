---
title: 🔧 常见同步失败原因
order: 35
---

# 🔧 常见同步失败原因

同步记录里出现 `error` 状态时，可以先按本页列出的常见原因排查。若仍无法解决，欢迎到 [GitHub Issues](https://github.com/SanaeMio/Bangumi-syncer/issues) 反馈。

在「同步记录」页面点击失败记录的 **重试同步** 按钮，会弹出实时 debug 日志，可用于定位具体失败环节。

---

## 1. Bangumi 账号问题

最常见的一类失败，通常表现为 `认证失败`、`access_token` 相关错误。

- **未配置 Bangumi 账号**：在「配置管理」里填写 Bangumi 的用户名和密码（或 access_token），保存后重试。
- **access_token 失效**：长期运行后 token 可能过期，到「配置管理」里重新获取或重新登录一次。
- **账号密码变更**：如果在 Bangumi 网站改过密码，需要同步更新本程序里的配置。
- **多用户模式下用户名不匹配**：多用户同步时按「媒体服务器用户名」路由到对应 Bangumi 账号，若媒体服务器用户名与配置里的不一致，会找不到对应的 Bangumi 账号。

排查方法：在「配置管理」里检查 Bangumi 账号配置是否完整，必要时点「测试连接」验证。

---

## 2. 未设置媒体服务器用户名

多用户模式下，程序依赖媒体服务器推送过来的 **用户名** 字段来路由到对应的 Bangumi 账号。

- **用户名大小写或空格不一致**：媒体服务器里的用户名与「配置管理」里填写的 Bangumi 用户名需要严格一致（包括大小写）。
- fongmi 的用户名比较特殊，为设备名，在日志里可以看到相关信息。

排查方法：在「同步记录」详情里查看 `user_name` 字段是否为空或与配置不符；必要时在媒体服务器侧调整 webhook 配置，确保携带正确的用户名。

---

## 3. 接入媒体源失败

程序需要能访问到媒体服务器或 Bangumi API，网络不通会导致失败。

### 3.1 不在局域网内

- 程序部署在与媒体服务器不同的网络环境（如 Docker 网络隔离、跨网段、VPN 未连通）。
- 飞牛 / fongmi 等定时同步场景下，程序需要主动访问媒体服务器 API，若网络不通会探测失败。

### 3.2 网络连接失败

- 程序无法访问 `bgm.tv`：检查服务器到 Bangumi 的网络连通性，必要时在「配置管理」里配置代理。
- 程序无法访问媒体服务器：检查媒体服务器地址、端口是否正确，防火墙是否放行。
- DNS 解析失败：服务器 DNS 配置异常，导致无法解析域名。

### 3.3 账号鉴权失败

- 媒体服务器的 API Token / 密码错误或已过期。
- Plex / Emby / Jellyfin 的 webhook 密钥与本程序配置的「Webhook 认证密钥」不一致。
- 飞牛 / fongmi 的登录凭证失效。

排查方法：重试时弹窗里的 debug 日志会显示具体的连接错误信息（超时、拒绝连接、401/403 等），按提示检查对应配置。

---

## 4. 媒体名字未被 Bangumi 收录

程序会把媒体库推送的标题拿去 Bangumi 搜索匹配，匹配不到就会失败。

### 4.1 不在 ACG 范围内

Bangumi 是以 ACG（动画、漫画、游戏）为主的站点，非 ACG 内容（如欧美剧、纪录片、真人秀等）通常不会被收录。同步这类内容会因找不到条目而失败。

解决方法：在「配置管理」的 **屏蔽关键词** 里添加这类标题关键词，让程序自动跳过，避免产生失败记录。

### 4.2 集数不存在

- 番剧实际还没有这一集（如推送了第 13 集但 Bangumi 上只登记了 12 集）。
- 季数解析错误，导致去错误的季度里找集数。
- 特别篇 / OVA / 剧场版的集数编号规则与正片不同，可能对不上。

排查方法：到 Bangumi 条目页面确认对应季度的章节列表是否包含推送的集数。

### 4.3 标题匹配失败

- **译名差异**：媒体库里的标题是中文译名，Bangumi 上用的是日文原名或英文译名。
- **多季合并**：媒体库把多季合并成一个条目（如「某番剧 第二季」），与 Bangumi 上的分季条目对不上。
- **标题带额外信息**：媒体库标题里带了分辨率、字幕组、年份等后缀（如 `[1080p] 某番剧 S02`），干扰匹配。

解决方法：到「映射管理」里添加 **[自定义映射](./mapping)**，手动指定「标题 → Bangumi 条目 ID」，程序会优先使用映射规则。

---

## 5. 特殊名称与特殊格式

### 5.1 特殊名称

- 标题里包含特殊字符（如全角括号、特殊标点），导致字符串比对失败。
- 标题与 Bangumi 上的同名条目撞名（如多部同名作品），自动匹配选错了条目。

解决方法：在「映射管理」里用精确的标题字符串指定正确的 Bangumi ID。

### 5.2 特殊格式

- **电影 vs 剧集类型混淆**：媒体库把剧场版标记为剧集，或把剧集标记为电影，导致程序走了错误的解析分支。
- **自定义 Webhook 字段缺失**：使用自定义 webhook 时，推送的 JSON 里缺少 `title`、`season`、`episode` 等必需字段。

排查方法：在「调试工具」页面用「测试同步」功能模拟一条请求，或查看重试弹窗里的 debug 日志，确认程序收到的原始数据是否符合预期。

---

## 仍然无法解决？

如果以上原因都不符合，可以：

1. 在「同步记录」页面找到失败记录，点击 **重试同步**，观察弹窗里的实时 debug 日志，定位具体失败环节。
2. 到 [GitHub Issues](https://github.com/SanaeMio/Bangumi-syncer/issues) 提交一个新 Issue，附上：
   - 失败记录的截图（含标题、集数、错误消息）
   - 重试弹窗里的完整 debug 日志
   - 程序版本号（在「仪表板」页面可见）
   - 对应的媒体服务器类型（Plex / Emby / Jellyfin / 自定义等）

收到完整信息后会尽快协助排查。

---

## 6. Bangumi Archive 相关问题

启用了 [Bangumi Archive 离线查询层](/bangumi-archive) 后，同步会优先查本地 SQLite。本节列出 archive 相关的常见问题。

### 6.1 启用后一直未生效

**排查步骤**：

1. **查看状态**：通过 `GET /api/bangumi_archive/status` 检查 `enabled` / `last_error` / `import_in_progress` / `current_progress` 字段
2. **查看进度日志**：`GET /api/bangumi_archive/progress_log?task_id=xxx` 看完整阶段变化
3. **常见原因**：
   - **磁盘空间不足**：可用空间低于 `min_disk_space_mb`（默认 3000MB）会跳过导入
   - **网络代理配置错误**：`http_proxy` 留空但 `[dev] script_proxy` 也未配置
   - **GitHub 下载失败**：直连与镜像源 fallback 链均不通，建议配置代理或手动下载 zip 后通过 `/api/bangumi_archive/import_local` 上传

### 6.2 导入失败后重试

- **自动重试**：`retry_interval = 3600`（默认 1 小时）后自动重试
- **手动重试**：`POST /api/bangumi_archive/trigger?force=true` 强制重新下载导入（忽略 dump_date 未变化的跳过逻辑）

### 6.3 索引未就绪降级到 API

archive 启用但**标题索引未构建完成**时（首次启用约 3-8 分钟），`try_search` / `try_search_old` 会返回 `archive_miss` 自动降级到 API：

- **不影响同步成功率**，只是该次查询走 API 较慢
- 其他 `try_get_*` 方法（直接查 SQLite，不依赖标题索引）可正常工作
- 索引构建在子线程进行，**不阻塞主流程**，构建完成后自动开始命中

### 6.4 数据陈旧

- Bangumi 官方每周三 05:00（北京时间）发布新 dump
- 默认 `update_cron = 0 8 * * 3` 每周三 08:00 自动拉取
- 如需立即更新：`POST /api/bangumi_archive/trigger?force=true`

### 6.5 archive 命中但结果不对

archive 数据来自 Bangumi 官方 dump，可能存在数据延迟或边缘情况：

1. **临时关闭 archive** 走纯 API 路径，对比结果是否一致
2. 在「调试工具」用「测试同步」功能复现问题
3. 必要时到 [GitHub Issues](https://github.com/SanaeMio/Bangumi-syncer/issues) 反馈

### 6.6 磁盘占用过大

- 导入峰值 = 双库（a+b 各 ~0.8GB）+ 临时下载 zip ~0.4GB + WAL 余量 ≈ **2.4GB**（FTS5 contentless 表不重复存储原始内容）
- 导入成功切换 active 指针后，旧库（db / db-wal / db-shm）会自动清理，常态占用约 **1.3GB**（仅 active 库）
- 旧版本（FTS5 改造前）残留的 `bangumi_archive_*.index` 缓存文件会在清空旧库时一并清理；若从旧版升级后仍有残留，可手动删除 `data_dir` 下的 `.index` 文件
- 若磁盘紧张可关闭 archive，并删除 `data_dir` 下的 `bangumi_archive_*.db` 文件

更详细的 archive 说明请看 [🗄️ Bangumi Archive 离线查询层](/bangumi-archive)。

---

## 7. Bangumi Replay 待同步队列问题

启用了 [Bangumi Replay 待同步队列补发](/bangumi-replay) 后，API 不可达时的写操作会进入 `pending_sync_queue` 队列。本节列出 replay 相关的常见问题。

### 7.1 同步记录显示 `queued` 但一直不补发

**排查步骤**：

1. **确认开关**：`[bangumi-replay] enabled` 未被设为 `false`（默认 `true`，与 archive 互相独立）
2. **查看调度器状态**：`GET /api/bangumi_replay/status`，关注 `enabled` / `running` / `cron` / `next_run`
   - `running = false`：调度器未启动，定时补发与立即触发都不会执行。保存一次 Replay 配置触发 `apply_config_after_save`，或重启程序
3. **手动触发补发**：队列页点击「批量补发」或 `POST /api/bangumi_replay/replay`
4. **看日志关键字**：
   - `📚 待同步队列为空，本轮跳过`：队列已被其他线程补发完，正常
   - `📚 Bangumi API 仍不可达，本轮补发跳过`：API 还没恢复，等下一轮
   - `📚 API 探测失败`：探测请求本身异常，看具体堆栈

### 7.2 API 一直不可达

队列页点击「探测 API」或调度器日志一直报告不可达：

1. **检查 `[bangumi]` 段配置**：探测使用的账号来自 `[sync] mode` 对应的段
   - `mode = single`（默认）→ 读 `[bangumi]` 段的 `username` / `access_token`
   - `mode = multi` → 读第一个用户映射指向的 `[bangumi-*]` 段
   - 任一字段为空都会导致探测直接返回 `False`（日志：`📚 无可用账号配置用于探测 API`）
2. **检查 `[dev]` 段代理与 SSL**：`script_proxy` 不通或 `ssl_verify=false` 配置错误都会让探测请求失败
3. **手动验证账号可用性**：用相同 `access_token` 直接请求 `https://api.bgm.tv/v0/subjects/1`，确认 token 未过期（有效期 1 年）
4. **强制清除不可达标记**：调度器探测成功后会自动清除，但缓存的 `BangumiApi` 实例可能仍带标记；补发单条时已通过 `mark_api_reachable()` 强制清除，如仍异常可重启服务

### 7.3 队列堆积过多

- 单条任务最大重试次数由 `max_attempts` 控制（默认 50），超过后标记为 `abandoned` 不再重试
- 可在队列页手动删除不需要的任务（如已用映射解决的旧失败记录）
- 长时间堆积通常意味着 Bangumi API 长期不可达，建议先解决 API 可达性问题（代理 / token / 网络）

更详细的 replay 说明请看 [🔄 Bangumi Replay 待同步队列补发](/bangumi-replay)。
