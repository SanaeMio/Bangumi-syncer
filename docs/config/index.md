---
title: ⚙️ 配置说明
order: 20
---

# ⚙️ 配置说明

Bangumi-syncer 的全部配置都集中在 Web 界面的 **「配置管理」** 页，按功能分卡片展示，**普通用户无需手写 INI**。本章节按功能模块拆分为多篇文档，分别介绍各配置项的含义与用法。

::: tip 配置入口
启动后在浏览器打开 `http://localhost:8000`，左侧菜单点击 **「配置管理」**，即可可视化完成全部设置。修改后点击页面下方的 **「保存配置」** 生效，部分项保存后需**重启程序**（页面上会标注）。
:::

## 配置文件

Web 界面背后对应一个 `config.ini` 文件（INI 格式），首次运行从 `config.example.ini` 自动复制。查找顺序：

1. `CONFIG_FILE` 环境变量
2. `/app/config/config.ini`（Docker 挂载）
3. `config.dev.ini`（开发）
4. `config.ini`（默认）

::: warning 不要混用
推荐通过 Web 界面修改。若手动编辑 `config.ini`，需重启程序生效，且编辑期间不要在 Web 界面保存（会覆盖手改内容）。
:::

## 本章节内容

| 文档 | 说明 |
| --- | --- |
| [⚙️ 配置说明](./configuration) | 同步、Bangumi 账号、代理、安全、调度器等核心配置 |
| [🔔 通知系统配置](./notification-configuration) | 通知场景、4 类渠道（Webhook / 邮件 / 企业微信 / 钉钉）、邮件自定义模板 |
| [🔀 自定义映射](/mapping) | 标题对不上时手工指定「标题 → 条目 ID」的兜底机制 |
| [🗄️ Bangumi Archive](./bangumi-archive) | 本地归档，把全站数据快照下载到本地，优先查本地降低 API 依赖 |
| [🔄 Bangumi Replay](./bangumi-replay) | 写降级与自动补发，API 不可达时入队暂存、恢复后自动补发 |

## 配置段速查

`config.ini` 按功能分若干段，常见段落：

| 段名 | 用途 |
| --- | --- |
| `[bangumi]` / `[bangumi-{user}]` | Bangumi 账号、Access Token（多账号用多段） |
| `[sync]` | 同步模式、屏蔽关键词、评分下限 |
| `[emby]` / `[jellyfin]` / `[plex]` | 各媒体服务器驱动配置 |
| `[feiniu]` / `[fongmi]` / `[trakt]` | 拉取型驱动配置 |
| `[notify-webhook-{n}]` / `[notify-email-{n}]` 等 | 通知渠道实例（可多实例） |
| `[archive]` / `[replay]` | Archive 与 Replay 配置 |
| `[scheduler]` | 调度器时区、任务超时 |
| `[summary-{name}]` | AI 追番总结任务 |

具体字段含义请查看对应文档。
