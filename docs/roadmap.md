---
title: 🗺️ 计划与路线图
order: 40
---

# 🗺️ 计划与路线图

✅ 支持自定义 Webhook 同步标记

✅ 支持 Plex（Tautulli）同步标记

✅ 支持指定单用户同步

✅ 适配 Plex 原生 Webhook（需要 Plex Pass）

✅ 适配 Emby 通知

✅ 适配 Jellyfin（需要 jellyfin-plugin-webhook 插件）

✅ 支持通过 bangumi-data 匹配番剧 ID，减少 API 请求

✅ 支持 Docker 部署

✅ 支持多账号同步

✅ Web 端管理界面

✅ 同步记录查看和统计

✅ 配置文件在线编辑

✅ 自定义映射管理

✅ 配置备份和恢复

✅ 同步触发通知（Webhook/邮件）

✅ 支持 Trakt.tv 定时同步

✅ 支持飞牛影视定时同步

✅ 支持 fongmi 定时同步

✅ Bangumi Archive 离线查询层（本地全站数据快照，命中即返回、未命中回退 API）

✅ Bangumi Replay 待同步队列补发（API 不可达时写操作入队，恢复后自动补发；入队后立即触发补发，与 cron 双保险）

✅ 匹配追踪（MatchTrace）可视化：流水线摘要、各阶段步骤卡、搜索参数、API 返回摘要、错误堆栈、步骤耗时表

✅ 候选确认与手动指定 Bangumi ID（确认即补发，无需手动重试）

✅ 番剧放送日历与今日提醒（复用 Archive `episode.airdate` 数据，仪表板按 7/14/30 天展示放送日程，支持「仅我在追」筛选；可启用「今日放送提醒」定时任务，每日推送当日放送汇总到 Webhook / 邮件 / 企业微信 / 钉钉）

以上能力已陆续实现，如果您有好的功能建议，欢迎提一个 [Issues](https://github.com/SanaeMio/Bangumi-syncer/issues) 进行交流。

## 接下来

- 想参与贡献？看 [贡献、鸣谢与许可](/community) 与 [🛠️ 开发文档](/development/)。
- 想接入新驱动？看 [新驱动接入指南](/development/new-driver-guide)。
