---
title: 📝 更新日志
order: 45
---

# Changes

本页汇总自 3.11.3 版本以来的主要变更。完整提交历史可在 [GitHub Commits](https://github.com/SanaeMio/Bangumi-syncer/commits/main) 查看。

## ✨ 新功能

- 新增番剧放送日历视图与 API 端点，仪表板展示 30 天追番日程 `2026-07-31`
- 新增 `airing_today` 今日放送提醒定时任务，每日推送当日放送汇总 `2026-07-31`
- 新增 Bangumi Archive 离线查询层，把全站数据快照下载到本地 SQLite [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 新增 Bangumi Replay 待同步队列补发，API 不可达时入队暂存、恢复后秒级补发 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 新增匹配追踪（MatchTrace）可视化，同步记录详情展示流水线各阶段 [#200](https://github.com/SanaeMio/Bangumi-syncer/pull/200) `2026-07-17`
- 通知系统重构，引入「事件 → 规则 → 渠道 → 模板」四层架构 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 新增 AI 追番总结，支持每日 / 每周 / 每月 / 每季度 / 年度任务 [#205](https://github.com/SanaeMio/Bangumi-syncer/pull/205) `2026-07-26`
- 新增三次元（日剧/电影）匹配支持，动画匹配失败时继续尝试三次元条目 [#194](https://github.com/SanaeMio/Bangumi-syncer/pull/194) `2026-07-17`
- 映射管理增强，支持季度感知映射与正则规则 [#194](https://github.com/SanaeMio/Bangumi-syncer/pull/194) `2026-07-17`
- 新增 fongmi 局域网同步，轮询 fongmi 设备 HTTP API 检测「看完」的剧集 [#185](https://github.com/SanaeMio/Bangumi-syncer/pull/185) `2026-07-14`
- 新增配置备份与恢复功能，登录后跳回原页面 [#189](https://github.com/SanaeMio/Bangumi-syncer/pull/189) `2026-07-15`
- 引入 SectionMeta 注册表作为配置元数据单一真相源 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 引入 SchedulerRegistry 单例统一管理调度器 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 配置页改为双栏布局，TOC 与操作按钮移至左侧 sticky 侧栏 [#189](https://github.com/SanaeMio/Bangumi-syncer/pull/189) `2026-07-15`

## 🐛 Bug 修复

- 修复 Replay 匹配阶段在 API 不可达时反复产出「未找到匹配的番剧」error 记录，改为直接跳过；探测成功后统一复位所有用户实例的不可达标记 `2026-08-09`
- 修复 `_bangumi_archive.html` 多余 `</div>` 导致 Replay/Airing Today 段渲染在 form 外、配置数据保存丢失 `2026-07-31`
- 修复 `airing_calendar.js` 缺少 IIFE 起始导致整个 JS 解析失败、卡片不显示 `2026-07-31`
- 修复 `list_user_collections` 把 `httpx.Response` 当 dict 处理导致在看列表获取为空 `2026-07-31`
- 修复 `notify-airing-today.enabled` 默认启用语义矛盾 `2026-07-31`
- 修复 `airing_calendar.js` catch 分支未重置 `row.style.display` 导致隐藏行后错误提示看不见 `2026-07-31`
- 修复 `BangumiApi` 实例从不 close 导致的 httpx.Client 连接池泄漏 `2026-07-31`
- 修正 `BangumiApi.get/post/put/patch` 返回类型注解（实际返回 `httpx.Response`） `2026-07-31`
- 修复 `date.today()` 取系统时区导致 Docker UTC 环境下与 `[scheduler] timezone` 不一致 `2026-07-31`
- 为 `_watching_cache` 添加锁防止并发竞态 `2026-07-31`
- `bangumi_archive.reload_config()` 异常时降级避免 API 500 与调度器报错 `2026-07-31`
- 任务超时/失败时触发 `scheduler_job_failed` 通知 `2026-07-31`
- `notify-airing-today` 段补全 `notification_types` 关联 `2026-07-31`
- 修复启动时 Archive `.tmp` 残留子目录未清理 [#220](https://github.com/SanaeMio/Bangumi-syncer/pull/220) `2026-07-31`
- 修复 Archive SSE `cached` 为 None 时 AttributeError [#220](https://github.com/SanaeMio/Bangumi-syncer/pull/220) `2026-07-31`
- 修复 Archive 导入进度直接跳到 100% 的问题 [#220](https://github.com/SanaeMio/Bangumi-syncer/pull/220) `2026-07-31`
- 修复 Archive 下载阶段前端卡死（进度推送过频+历史日志暴涨） [#220](https://github.com/SanaeMio/Bangumi-syncer/pull/220) `2026-07-31`
- 清空旧库前先 invalidate FTS5 连接，修复 WinError 32 [#220](https://github.com/SanaeMio/Bangumi-syncer/pull/220) `2026-07-31`
- 修复 `_build_fts5_index` 批量插入逻辑，攒满 `_BATCH_SIZE` 再插 [#217](https://github.com/SanaeMio/Bangumi-syncer/pull/217) `2026-07-30`
- 修复 `try_get_episodes` / `try_get_related_subjects` 在 archive 不完整时静默返回空数据 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 修复 `bangumi-archive` 段被误归入多账号导致前端读不到 enabled 状态 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 修复 `import_local` 路径穿越漏洞 (CWE-22) [#215](https://github.com/SanaeMio/Bangumi-syncer/pull/215) `2026-07-29`
- 修复 Replay API 探测始终不可达 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 修复 Replay `trigger_immediate_run` 防抖未使用 `time.monotonic()` [#214](https://github.com/SanaeMio/Bangumi-syncer/pull/214) `2026-07-29`
- 修复重试时因信息缺失导致匹配错误的问题，完善输入持久化 [#206](https://github.com/SanaeMio/Bangumi-syncer/pull/206) `2026-07-24`
- 修复多用户模式保存配置失败的问题 [#201](https://github.com/SanaeMio/Bangumi-syncer/pull/201) `2026-07-17`
- 修复升级后首次启动误删历史同步记录的问题 [#195](https://github.com/SanaeMio/Bangumi-syncer/pull/195) `2026-07-17`
- 修复 Dockerfile/release-zip/.dockerignore 引用 `config.ini` 的问题 [#194](https://github.com/SanaeMio/Bangumi-syncer/pull/194) `2026-07-17`
- 修复 `season=1` 时改选无季度后缀的第一季候选 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 修复匹配记录详情点击报错 — `getMethodBadge` 函数未定义 [#197](https://github.com/SanaeMio/Bangumi-syncer/pull/197) `2026-07-17`
- 修复 `config` 零值被当作 falsy 的问题 [#199](https://github.com/SanaeMio/Bangumi-syncer/pull/199) `2026-07-17`
- 修复多次保存导致的密钥丢失问题 [#201](https://github.com/SanaeMio/Bangumi-syncer/pull/201) `2026-07-17`
- 修复 Trakt 同步时 TMDB 映射不支持季度感知查询导致无职第三季匹配错误 [#204](https://github.com/SanaeMio/Bangumi-syncer/pull/204) `2026-07-23`
- 修复集匹配季度边界检测与续集链计数 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 修复登录测试与 `apiFetch returnResponse` 未生效导致登录页报错 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 修复 P0-P2 前端问题，`fetch → apiFetch` 全量迁移 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 修复 `getSourceColor` 与 `mappings showLoading` bug [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 测试接口可选跳过用户名校验（仅对测试来源生效） [#199](https://github.com/SanaeMio/Bangumi-syncer/pull/199) `2026-07-17`

## 🚀 优化

- `BangumiApi` 实例按用户缓存，避免重复构造 `httpx.Client` [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- Archive 标题索引后台构建 + 磁盘缓存，消除首次查询 111s 阻塞 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 优化 Archive 导入性能，临时文件统一到 `data_dir/.tmp/` 并解压后立即删 zip [#220](https://github.com/SanaeMio/Bangumi-syncer/pull/220) `2026-07-31`
- FTS5 trigram 替代内存索引 + 磁盘阈值上调至 4GB [#217](https://github.com/SanaeMio/Bangumi-syncer/pull/217) `2026-07-30`
- 移除死表并按 type 过滤导入，单库体积降至约 0.8GB [#217](https://github.com/SanaeMio/Bangumi-syncer/pull/217) `2026-07-30`
- 优化首页时间线海报异步加载 [#191](https://github.com/SanaeMio/Bangumi-syncer/pull/191) `2026-07-15`
- 列表查询去除 `match_trace` + retention 自动清理 + config 模板化 [#194](https://github.com/SanaeMio/Bangumi-syncer/pull/194) `2026-07-17`
- `e2e-tests` 提速 + 失败捕获 + 补关键用例 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`

## ♻️ 重构

- 通知系统重构 + 调度器状态卡修复 + 死代码清理 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 拆分 `config.html` 巨石模板为 12 个子模板 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 引入 `ConfigForm` 库实现前端自动序列化与配置注入 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 配置保存统一联动改为遍历 SectionMeta 驱动 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 前端通知类型动态加载，消除硬编码复选框 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 拆分 `app.js` 与 `style.css` 按功能模块/分区 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 抽取 `BangumiApi` 工厂与写映射 helper [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 提取文本处理字典常量到 `utils/text_constants.py` 统一管理 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 集成 `bangumi/common` 常量，消除关联/条目/收藏类型硬编码 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 修复 `core↔utils`、`utils↔services` 分层违规 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`

## 📚 文档

- 拆出独立的 `bangumi-replay.md`，明确 Replay 与 Archive 解耦但功能互补 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 新增常见同步失败原因文档 + 重试弹窗加宽并链接文档/issues [#192](https://github.com/SanaeMio/Bangumi-syncer/pull/192) `2026-07-15`
- 修正 `mark_api_unreachable` 过时注释 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 文档拆分 LLM 配置和 AI 追番总结 [#205](https://github.com/SanaeMio/Bangumi-syncer/pull/205) `2026-07-26`
- 提供月度/年度配置的 cron 表达式供用户参考 [#205](https://github.com/SanaeMio/Bangumi-syncer/pull/205) `2026-07-26`
- 更新 trakt 文档：通过 SSH 端口转发以应对 Trakt 严格校验回调地址 [#209](https://github.com/SanaeMio/Bangumi-syncer/pull/209) `2026-07-26`

## 🧪 测试

- 引入 Playwright E2E 测试基础设施 [#219](https://github.com/SanaeMio/Bangumi-syncer/pull/219) `2026-07-31`
- 修复 `conftest` 优先用 `config.example.ini` 而非 `config.ini` [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 修复 `bangumi-archive.enabled=True` 卡死测试 + `asyncio.run` 污染事件循环 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 显式关闭 sqlite 连接修复 Windows 临时目录清理失败 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`

## 🔧 其他

- 简化 PR 模板，移除提交前检查清单（pre-commit 钩子自动完成） [#198](https://github.com/SanaeMio/Bangumi-syncer/pull/198) `2026-07-17`
- HTTP 成功请求日志改为 DEBUG [#190](https://github.com/SanaeMio/Bangumi-syncer/pull/190) `2026-07-15`
- 后台任务统一注册与生命周期管理 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- 数据库迁移，`sync_records.db` 默认路径迁移到 `data/` [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`
- Archive 数据目录迁移到 `./data/archive` 并清理旧索引缓存 [#210](https://github.com/SanaeMio/Bangumi-syncer/pull/210) `2026-07-29`

---

::: tip 完整提交历史
本页仅汇总主要变更，完整提交记录可在 [GitHub Commits](https://github.com/SanaeMio/Bangumi-syncer/commits/main) 查看。
:::
