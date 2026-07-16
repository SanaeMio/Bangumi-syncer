---
title: ✨ 特性
order: 2
---

![Web 管理界面 - 仪表板](/images/overview/screenshot.png)

- 🌐 **现代化 Web 管理界面**：仪表板汇总总同步次数、今日同步、成功率与失败次数，支持最近 7 天趋势与用户分布等可视化，表格展示最近同步记录。
- ⚙️ **全流程在线配置**：Bangumi 账号、同步模式、代理、认证、通知、飞牛等均可通过 Web 配置管理保存生效；支持配置备份与恢复，大改前留底、换机迁移更安心。
- ✅ **看完即同步**：在 Plex / Emby / Jellyfin 等里标记看完后，由程序调用 **Bangumi 官方 API** 自动更新观看进度，无需反复打开 Bangumi 网站手点。
- 🧠 **智能推理条目**：自动把媒体库标题对齐到 Bangumi 条目，减轻多季、译名不一致带来的困扰，仍对不上时也可用 **[自定义映射](/mapping)** 手工指定「标题 → 条目 ID」来兜底。
- 🔌 **常见媒体栈都能接**：已内置适配 **Plex**（[Tautulli](/usage/tautulli)、[官方 Webhooks](/usage/plex-webhooks)）、**[Emby](/usage/emby)**、**[Jellyfin](/usage/jellyfin)** 与 **[自定义 Webhook](/usage/custom-webhook)**。另可按需启用 **[Trakt定时同步](/usage/trakt)**、**[飞牛定时同步](/usage/feiniu)** 、**[fongmi定时同步](/usage/fongmi)** 等，覆盖了绝大多数场景。
- 👥 **多用户同步**：多用户模式下按 **媒体服务器用户名** 路由到不同 Bangumi 账号，数据互不混杂；仪表板可按用户维度查看同步分布。
- 🛡️ **安全与告警**：可选 Web 登录、会话超时、HTTPS Cookie、登录失败锁定等，同步过程支持 **Webhook / 邮件** 通知，模板与类型可高度自定义，便于接入Telegram、钉钉、企业微信、邮箱等外部系统。
- 🧩 **匹配记录可追溯**：保留完整的匹配过程，可直观地了解匹配过程以便排查问题。

## 接下来

- 准备部署？看 [⚡ 快速上手](/getting-started) 与 [🚀 快速开始](/quick-start/)。
- 已部署好，想配置参数？看 [⚙️ 配置说明](/configuration)。
- 想接入具体的媒体服务器？看 [🔌 接入使用](/usage/)。
- 标题对不上 Bangumi 条目？看 [🔀 自定义映射](/mapping)。