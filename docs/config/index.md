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
| [🛠️ 自建 ECH DoH（进阶）](./self-hosted-doh) | 需要一定门槛：自行部署 DoH 服务（Total-ECH），自定义 ECH 配置来源与优选 IP |

## 配置段速查

`config.ini` 按功能分段。**普通用户不需要手改**，下表用于对照排查：

| 段名 | 用途 |
| --- | --- |
| `[sync]` | 同步行为、屏蔽关键词、模糊匹配置信度 |
| `[auth]` | 管理页登录、Webhook 认证 |
| `[web]` | 子路径反向代理（`base_path`） |
| `[dev]` | 网络代理、ECH、日志、同步记录保留 |
| `[bangumi-data]` | 公共番剧资料库（用于片名匹配） |
| `[matching]` | 匹配裁决层（双阈值门控，默认关闭） |
| `[bangumi-archive]` | 本地归档（离线查询层） |
| `[bangumi-replay]` | 写降级与自动补发 |
| `[notify-airing-today]` | 今日放送提醒 |
| `[notify-webhook-{n}]` / `[notify-email-{n}]` 等 | 通知渠道实例（由配置页弹窗管理） |
| `[feiniu]` / `[fongmi]` / `[trakt]` | 拉取型数据源 |
| `[llm]` | AI 追番总结所用的模型参数 |
| `[summary-{name}]` | AI 追番总结任务（由配置页管理） |
| `[scheduler]` | 调度器并发、超时、重试（一般无需修改） |
| `[bangumi]` / `[bangumi-oauth]` | 旧版账号字段与 OAuth 应用凭证（一般无需修改） |

::: tip 每一项的含义
各配置项的用途、默认值与填写建议，见 [⚙️ 配置说明](./configuration)。该页覆盖了 `config.example.ini` 中的**全部配置项**。
:::

::: tip 账号存储位置
Bangumi 账号（用户名、访问令牌、OAuth 令牌等）存储在 SQLite 数据库 `data/sync_records.db` 的 `bangumi_accounts` 表中，不再写入 `config.ini`。令牌加密存储，数据库文件泄露也不会暴露明文 token。
:::

具体字段含义请查看对应文档。
