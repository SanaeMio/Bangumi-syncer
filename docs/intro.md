---
title: 📖 简介
order: 1
---

![Banner](/images/branding/banner.png)

Bangumi-syncer 是一款把常见媒体库与 [Bangumi（番组计划）](https://bgm.tv/)连接在一起的轻量级小软件。

你可以在 Plex、Emby、Jellyfin、Trakt、飞牛、fongmi等任意媒体库客户端里照常看番，看完一集后会自动调用 [Bangumi API](https://bangumi.github.io/api/) 打格子，免去频繁打开网站的烦恼，省时省力。

![同步流程图](/images/overview/sync-demo.svg)

![Web 管理界面 - 仪表板](/images/overview/screenshot.png)

## 核心能力

### ✅ 看完即同步

在 Plex / Emby / Jellyfin 等里标记看完后，由程序调用 **Bangumi 官方 API** 自动更新观看进度，无需反复打开 Bangumi 网站手点。

### 🔌 常见媒体栈都能接

已内置适配：

- **Plex**：[Tautulli](/usage/tautulli)、[官方 Webhooks](/usage/plex-webhooks)
- [Emby](/usage/emby) / [Jellyfin](/usage/jellyfin) / [自定义 Webhook](/usage/custom-webhook)
- [Trakt 定时同步](/usage/trakt) / [飞牛定时同步](/usage/feiniu) / [fongmi 局域网同步](/usage/fongmi)

覆盖了绝大多数家庭媒体场景。

### 🧠 智能匹配

自动把媒体库标题对齐到 Bangumi 条目，减轻多季、译名不一致带来的困扰。仍对不上时也可用 [自定义映射](/mapping) 手工指定「标题 → 条目 ID」来兜底。

- 保留完整的匹配过程，可直观地了解匹配过程以便排查问题。
- 「同步记录」详情页提供流水线摘要、各阶段步骤卡、步骤耗时表。
- 候选确认与手动指定 Bangumi ID：自动匹配失败但存在相近结果时，候选会沉淀到「候选确认」页，确认即自动补发同步。

### 🌐 现代化 Web 管理界面

- 仪表板汇总总同步次数、今日同步、成功率与失败次数，支持最近 7 天趋势与用户分布等可视化。
- Bangumi 账号、同步模式、代理、认证、通知、飞牛等均可通过 Web 配置管理保存生效。
- 支持配置备份与恢复，大改前留底、换机迁移更安心。

### 👥 多用户同步

按 **媒体服务器用户名** 路由到不同 Bangumi 账号，数据互不混杂；仪表板可按用户维度查看同步分布。

### 🛡️ 安全与告警

- 可选 Web 登录、会话超时、HTTPS Cookie、登录失败锁定等。
- 同步过程支持 **Webhook / 邮件 / 企业微信 / 钉钉** 四类通知渠道。
- 通知规则按事件分类订阅，模板可高度自定义。详见 [🔔 通知系统配置](/config/notification-configuration)。

### 🔄 离线容错与自动补发（Replay）

开启 [Bangumi Replay](/config/bangumi-replay) 后：

- 当 Bangumi API 短暂不可达时，已匹配的写操作会进入本地队列暂存。
- API 恢复后自动补发；**入队后立即触发补发**，秒级恢复体验。
- 与 cron 定时调度形成双保险。

### 🗄️ 可选本地归档（Archive）

开启 [Bangumi Archive](/config/bangumi-archive) 后：

- 把 Bangumi 全站数据快照下载到本地 SQLite。
- 同步优先查本地、未命中再回退 API，显著降低延迟与对官方 API 的依赖。
- 与 Replay 搭配可覆盖「API 完全不可达」的端到端无网场景。

### 📺 番剧放送日历与今日提醒

开启 Archive 后，仪表板自动显示「番剧放送日历」卡片：

- 可查看未来 7/14/30 天的放送日程（含「仅我在追」筛选）。
- 可启用「今日放送提醒」定时任务，每日通过通知渠道推送当日放送汇总。

### 🤖 AI 追番总结

接入 LLM（OpenAI 兼容接口）后，可创建多个总结任务（每日 / 每周 / 每月 / 每季度 / 年度），各自独立运行，总结结果通过通知系统发送。

## 接下来

- 想立刻跑起来？看 [⚡ 快速上手](/getting-started)。
- 想选部署方式？看 [🚀 安装指南](/quick-start/)。
- 已部署好，想配置参数？看 [⚙️ 配置说明](/config/)。
- 想接入具体的媒体服务器？看 [🔌 接入使用](/usage/)。
- 标题对不上？看 [🔀 自定义映射](/mapping)。
- 遇到问题？看 [🔧 常见同步失败原因](/troubleshooting)。
- 想看版本变更？看 [📝 更新日志](/changelog)。
- 想参与开发或接入新驱动？看 [🛠️ 开发文档](/development/)。
