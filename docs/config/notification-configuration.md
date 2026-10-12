---
title: 🔔 通知系统配置
order: 25
---

# 🔔 通知系统配置

Bangumi-syncer 可以在关键时刻主动给你发消息——同步成功、同步失败、收到播放请求、追番总结生成完成等。本页介绍通知系统的使用场景、4 类渠道，以及邮件自定义模板的写法。

配置项全部在 Web 的「配置管理 → 通知配置」中可视化完成，**不需要手写 INI**。

---

## 使用场景

通知系统的常见用法：

| 场景                                      | 订阅事件             | 推荐渠道               |
| ----------------------------------------- | -------------------- | ---------------------- |
| **同步失败立即告警**                      | 同步失败             | 钉钉 / 企业微信 / Bark |
| **追番总结每天发到邮箱**                  | `watching_summary_*` | 邮件                   |
| **今日放送每日早报**                      | 今日放送提醒         | 邮件 / 企业微信        |
| **所有事件多通道冗余**                    | 全部事件             | Webhook + 邮件         |
| **接入第三方自动化（如 Home Assistant）** | 同步成功 / 失败      | 通用 Webhook           |
| **Trakt / 飞牛定时任务执行情况**          | 调度任务类事件       | 钉钉 / 企业微信        |

::: tip 排查问题必备
强烈建议至少订阅「同步失败」事件到一个即时渠道（钉钉 / 企业微信 / Bark），出问题时第一时间知道，不用反复刷 Web 界面。
:::

---

## 整体结构

通知系统由四层组成，按数据流向串联：

```
触发事件 → 匹配通知规则 → 渲染模板 → 发送到渠道
```

- **触发事件**：由系统内部产生，如「同步成功」「同步失败」「收到请求」「匹配到番剧」「追番总结完成」。
- **通知规则**：决定哪些事件发给哪些渠道，是配置的核心。一个规则 = 「勾选若干事件 + 选择若干渠道 + 可选自定义模板」。
- **渠道**：实际发送方，如 Webhook、邮箱、企业微信群机器人、钉钉群机器人。同一类渠道支持添加**多个实例**。
- **模板**：决定消息长什么样，支持「默认」「自定义模板文件」「内联 JSON」三种来源。

::: tip 规则是发布闸门
旧版本中每条渠道各自带一个 `types`（订阅哪些事件）字段。新格式把所有订阅关系**集中到「通知规则」**里，渠道只负责**连接配置**——「换事件订阅」和「换渠道」互不干扰。

**未创建任何规则时，外部渠道一律停发**（渠道 `types` 字段不再生效，事件仅写入站内信）。删除全部规则即可一键静默，无需逐个删除渠道。
:::

---

## 4 类渠道介绍

在「通知配置」卡片右上角点击 **「渠道配置」** 打开模态框。模态框内有 4 个 Tab：**Webhook / 邮件 / 企业微信 / 钉钉**，每个 Tab 独立管理一类渠道的多个实例。

> 渠道配置在模态框内**直接内联编辑**：「添加配置」展开空白表单，「编辑」加载已有数据，「保存」后立即生效，「删除」通过浏览器原生 `confirm()` 二次确认。

### Webhook（最通用）

**适合谁**：钉钉机器人、Telegram Bot、飞书机器人、Bark、Discord、Slack、自建网关——所有「接收一个 HTTP 请求」的服务都可以接。

**特点**：灵活、跨平台，所有事件共用一个 URL 即可。模板支持自定义 JSON。

| 字段         | 必填 | 说明                                                                  |
| ------------ | ---- | --------------------------------------------------------------------- |
| 启用         | 是   | 关闭后该渠道不参与任何事件分发                                        |
| Webhook URL  | 是   | 完整 HTTP(S) 地址                                                     |
| 请求方法     | 否   | `POST`（默认，发 JSON body）或 `GET`（payload 作为 query string）     |
| 自定义请求头 | 否   | JSON 格式，如 `{"Authorization":"Bearer xxx"}`；也支持 `K:V,K:V` 简写 |
| 消息模板     | 否   | 留空使用默认 JSON；详见下方「模板系统」                               |

### 邮件（最适合长文 / 总结）

**适合谁**：追番总结、每日放送早报、定期回顾——内容较多、希望归档留存的场景。

**特点**：支持 HTML 富文本模板，可完全自定义样式；QQ 邮箱、Gmail、Outlook、自建 SMTP、企业邮箱均可。

| 字段               | 必填       | 说明                                                                                   |
| ------------------ | ---------- | -------------------------------------------------------------------------------------- |
| 启用               | 是         | 关闭后该渠道不参与任何事件分发                                                         |
| SMTP 服务器        | 是         | 例如 `smtp.qq.com`、`smtp.gmail.com`                                                   |
| 端口               | 是         | SSL 通常 `465`；STARTTLS 通常 `587`；不加密通常 `25`                                   |
| SMTP 用户名        | 是         | 完整邮箱地址                                                                           |
| SMTP 密码/授权码   | 是（新建） | 编辑时**留空表示不修改**；QQ 邮箱等需要「授权码」而不是登录密码                        |
| 发件人地址         | 否         | 留空使用 SMTP 用户名                                                                   |
| 收件人地址         | 是         | 支持多个收件人，逗号或分号分隔                                                         |
| 使用 TLS 加密      | 是         | 端口 465 时务必开启（SSL on connect）                                                  |
| 邮件标题模板       | 否         | 留空使用默认模板                                                                       |
| 自定义模板文件路径 | 否         | 高级选项，指向本地 `email/<name>.{subject.txt,body.txt,html}` 三件套；留空使用默认模板 |

::: tip 邮件是唯一支持 HTML 富文本的渠道
追番总结这种长文 + 排版 + 表格 + emoji 的内容，强烈建议走邮件渠道。邮件默认模板会直接把总结正文渲染进正文区，无需额外配置；要换样式见下方 [自定义模板（高阶）](#自定义模板-高阶)。
:::

### 企业微信

**适合谁**：公司用企业微信、或自建群聊用群机器人推送的场景。

**特点**：底层是 Webhook，但格式定制化。模板复用 Webhook 默认模板，自定义格式需用内联 JSON。

| 字段        | 必填 | 说明                                                        |
| ----------- | ---- | ----------------------------------------------------------- |
| 启用        | 是   | 关闭后该渠道不参与任何事件分发                              |
| Webhook Key | 是   | 群机器人 Webhook URL 中的 `key=` 参数值；也可直接粘完整 URL |
| 消息类型    | 否   | `text`（默认）或 `markdown`                                 |
| 消息模板    | 否   | 留空走代码内置构造；自定义格式填内联 JSON                   |

### 钉钉

**适合谁**：钉钉群机器人（自定义机器人）。

**特点**：支持「加签」安全模式。模板复用 Webhook 默认模板，自定义格式需用内联 JSON。

| 字段         | 必填 | 说明                                                               |
| ------------ | ---- | ------------------------------------------------------------------ |
| 启用         | 是   | 关闭后该渠道不参与任何事件分发                                     |
| Access Token | 是   | 机器人 Webhook URL 中的 `access_token=` 参数值；也可直接粘完整 URL |
| Secret       | 否   | 「加签」模式密钥；填了之后请求会带 HMAC-SHA256 签名                |
| 消息类型     | 否   | `text`（默认）或 `markdown`                                        |
| 消息模板     | 否   | 留空走代码内置构造；自定义格式填内联 JSON                          |

---

## 通知规则

在「通知配置」卡片右上角点击 **「新建配置」** 打开规则编辑器。这是「决定哪些事件发给哪些渠道」的核心配置。

| 字段       | 必填 | 说明                                            |
| ---------- | ---- | ----------------------------------------------- |
| 启用此规则 | 是   | 关闭后该规则不参与任何事件分发                  |
| 规则名称   | 是   | 自己看的名称，例如「同步失败钉钉告警」          |
| 触发事件   | 否   | 勾选要订阅的事件；**全部不勾选 = 订阅全部事件** |
| 通知渠道   | 是   | 至少选择 1 个渠道实例（多选）                   |
| 自定义模板 | 否   | 留空则使用渠道自身配置的模板                    |

事件按 6 大类分组展示，方便勾选：

- **同步流程**：同步成功、同步失败等
- **匹配质量**：匹配到番剧、候选确认等
- **数据源**：收到请求等
- **调度任务**：飞牛 / fongmi / Trakt / 今日放送 / 追番总结等定时任务执行情况
- **Bangumi API**：API 不可达、恢复等
- **系统运维**：调度器任务失败等

**举例**：

- 「同步失败钉钉告警」：事件勾选 `mark_failed`，渠道勾选 `notify-dingtalk-1`
- 「追番总结每天邮件」：事件勾选 `watching_summary` 前缀的所有事件，渠道勾选 `notify-email-1`
- 「所有事件 → Webhook + 邮件双通道」：事件不勾选，渠道勾选所有

::: tip 同类通知防刷屏
同类通知在短时间内会限制连续发送次数（默认 60 秒冷却），避免 Trakt 全量同步等场景下刷屏。
:::

---

## 模板系统

模板决定「消息长什么样」。

::: tip 强烈建议：先用默认模板，别急着自定义
**默认模板已经能正确发送所有事件，包括追番总结的正文和标题里的任务名，并会随程序升级自动获得改进。** 你不需要为了「让消息好看一点」去手写模板。

只有在下面这种情况下才需要自定义：你明确想要**和默认不一样的排版**（比如企业微信里换一种 Markdown 结构、邮件里加公司 Logo 和页脚）。

自定义属于**高阶操作**，代价是：默认模板将来新增的字段不会自动出现在你的模板里，需要你自己跟。详见 [自定义模板（高阶）](#自定义模板-高阶)。
:::

### 默认模板（推荐，零配置）

渠道配置中模板字段**留空**即可，系统按渠道使用内置模板：

- **Webhook**：使用 `templates/notifications/webhook/default.json`，结构为：

```json
{
  "title": "{payload_title}",
  "type": "{notification_type}",
  "timestamp": "{timestamp}",
  "user": "{user_name}",
  "anime": "{title}",
  "episode": "{ep_label}",
  "source": "{source}",
  "error": "{error_message}",
  "extra": "__type_fields__"
}
```

其中 `"extra": "__type_fields__"` 是一个**标记**（哨兵值），不是最终发出的键。渲染时这个键会被**整体摘掉**，同时把**该事件类型特有的字段平铺到 JSON 的顶层**（见下方「事件专属字段」）。

以追番总结为例，**实际发出去的内容**长这样（注意：**没有 `extra` 键**，`summary` 等直接在顶层；追番总结没有集数信息，`episode` 为空串）：

```json
{
  "title": "📊 追番总结 - 每日总结",
  "type": "watching_summary_daily",
  "timestamp": "2026-08-15 09:00:00",
  "user": "alice",
  "anime": "",
  "episode": "",
  "source": "summary",
  "error": "",
  "job_name": "每日总结",
  "summary": "本周共观看 3 部番剧，合计 12 集……",
  "date_range": "2026-08-09 ~ 2026-08-15",
  "record_count": 12
}
```

所以接 Webhook 时请**直接取顶层字段**（如 `summary`、`job_name`），**不要**去取 `extra.summary` —— 那个路径永远是空的。

- **邮件**：所有事件共用 `templates/notifications/email/default.html` 单文件。邮件主题从 HTML 的 `<title>` 标签提取（与 Webhook 的 `title` 同源，因此追番总结会显示为「📊 追番总结 - 任务名」），纯文本 body 由 HTML 去标签生成作为 fallback。追番总结的正文会渲染在邮件正文区。
- **企业微信 / 钉钉**：渠道配置的「消息模板」字段留空时，由代码内置构造消息体（`text` 或 `markdown` 两种格式）。
- **站内信**：标题使用注册表中类型的 `in_app_title_template`，正文使用 `error_message` 或 `message` 字段。

---

## 可用占位符（变量）

模板中使用 `{变量名}` 引用数据。所有渠道、所有事件都共享以下变量：

### 基础信息

| 占位符                | 说明                                     | 示例                  |
| --------------------- | ---------------------------------------- | --------------------- |
| `{timestamp}`         | 事件触发时间，格式 `YYYY-MM-DD HH:MM:SS` | `2026-07-30 14:23:11` |
| `{user_name}`         | 媒体服务器上的用户名                     | `alice`               |
| `{bgm_username}`      | 执行标记的 Bangumi 账号用户名            | `alice_bgm`           |
| `{source}`            | 触发来源（媒体服务器或事件源）           | `plex`、`emby`        |
| `{notification_type}` | 事件类型标识                             | `mark_failed`         |
| `{type_display_name}` | 事件类型中文展示名                       | `同步失败`            |
| `{type_icon}`         | 事件类型图标（emoji）                    | `❌`                  |
| `{payload_title}`     | 事件标题（含图标，追番总结会带任务名）   | `📊 追番总结 - 每日总结` |

### 番剧与集数

| 占位符         | 说明                                           | 示例                |
| -------------- | ---------------------------------------------- | ------------------- |
| `{title}`      | 番剧主标题（Bangumi 主标题，匹配前为原始标题） | `葬送的芙莉莲`      |
| `{ori_title}`  | 媒体服务器传来的原始标题                       | `Frieren S01E12`    |
| `{bgm_title}`  | 匹配到 Bangumi 后的中文标题                    | `葬送的芙莉莲`      |
| `{season}`     | 季号                                           | `1`                 |
| `{episode}`    | 集号（纯数字，未补零）                         | `12`                |
| `{ep_label}`   | 集数标签（补零；剧场版为「剧场版」；**无集数信息时为空串**） | `S01E12` / `剧场版` / 空 |
| `{media_type}` | 媒体类型                                       | `episode` / `movie` |
| `{subject_id}` | Bangumi 番剧 ID                                | `425602`            |
| `{episode_id}` | Bangumi 单集 ID                                | `1234567`           |

### 错误信息

| 占位符            | 说明                         | 示例                     |
| ----------------- | ---------------------------- | ------------------------ |
| `{error_message}` | 错误信息（仅失败类事件有值） | `Bangumi API timeout`    |
| `{error_type}`    | 错误类型分类                 | `network`、`auth`、`api` |

### AI 追番总结

以下占位符仅在 AI 追番总结事件（`watching_summary_{name}`）中才有值：

| 占位符            | 说明               | 示例                        |
| ----------------- | ------------------ | --------------------------- |
| `{job_name}`      | 总结任务名         | `每日总结`                  |
| `{summary_text}`  | 总结正文（AI 生成） | `本周共观看 3 部番剧…`      |
| `{date_range}`    | 统计的日期范围     | `2026-08-09 ~ 2026-08-15`   |
| `{record_count}`  | 覆盖的观看记录数   | `42`                        |
| `{lookback_days}` | 回溯天数           | `7`                         |
| `{model}`         | 生成用的模型名     | `deepseek-v3`               |
| `{tokens_used}`   | 消耗 token 数      | `12345`                     |

::: tip 未提供的变量怎么办？
模板中未提供的占位符在渲染时会被替换为**空字符串**（不会保留 `{xxx}` 字面量）。所以你可以放心地把所有变量都写进模板，没值时自动留空。
:::

### 事件专属字段

除了上面这些通用占位符，**每类事件还有自己的专属字段**，会由系统自动**平铺到 Webhook JSON 的顶层**（不是塞进 `extra`，`extra` 只是模板里的标记键，详见下节），并渲染进邮件正文。你不必手动声明：

| 事件                       | 专属字段                                                                 |
| -------------------------- | ------------------------------------------------------------------------ |
| 追番总结 `watching_summary` | `job_name`、`summary`、`date_range`、`record_count`、`model`、`tokens_used` |
| 候选待确认 `pending_candidate` | `candidates_count`、`top_candidate_id`、`top_candidate_name`            |
| 匹配歧义 `match_ambiguous` | `final_subject_id`、`top1_name`、`top1_subject_id`、`top1_score`、`top2_name`、`top2_subject_id`、`top2_score`、`score_diff` |
| 标记成功 / 跳过 / 排队     | `subject_id`、`episode_id`、`bgm_title`、`bgm_username`                   |
| 找到番剧 `bangumi_id_found` | `subject_id`、`bgm_title`                                                |
| 同步失败 `mark_failed`     | `error_type`、`additional_info`                                          |
| 今日放送 `airing_today`    | `airdate`、`total`                                                       |
| 批量同步汇总               | `total`、`succeeded`、`failed`、`skipped`                                 |
| 队列积压告警               | `pending_count`、`threshold`                                             |
| IP 锁定 `ip_locked`        | `ip`、`locked_until`、`attempt_count`、`max_attempts`                     |
| 归档磁盘告警               | `available_mb`、`required_mb`、`warning_threshold_mb`                     |
| 版本升级可用               | `current_version`、`latest_version`                                       |
| 调度任务失败               | `driver`、`is_timeout`                                                    |
| 总结任务 / LLM 失败        | `job_name`（LLM 失败另有 `model`）                                        |
| API 类错误                 | `status_code`（重试失败另有 `url`、`method`、`retry_count`）              |

未在上表列出的类型（如 `anime_not_found`、`config_error`、`request_received`）没有专属字段，用通用占位符即可。

这些字段的取值规则见下方[脚注](#事件专属字段的取值规则)。

#### 事件专属字段的取值规则

**只有 `None` 和空串 `""` 会被省略**——键根本不存在，而不是输出 `null` 或空串。

**`0` 和 `False` 是有效值，会被保留。** 例如 `record_count: 0`（本期没有任何观看记录）、`is_timeout: false`（任务失败但不是超时）都会正常发出。消费方应把「键不存在」理解为「这项信息没有」，而不是「值为 0」——两者含义不同。

---

## 自定义模板（高阶）

::: warning 这是高阶操作，先确认你确实需要
动手前请想清楚：**默认模板已经能正确发送所有事件**。自定义模板意味着你要自己维护一份格式，并对将来新增的字段负责。

如果你的目的是「让 Webhook 的 JSON 结构符合我的接收端要求」，或「让企业微信消息用另一种排版」，那自定义是合适的。如果只是觉得默认样式不够好看，改邮件模板即可，**不必动 Webhook**。
:::

### 先读：三种自定义方式

| 方式 | 配置方式 | 适用渠道 | 风险 |
| --- | --- | --- | --- |
| **自定义模板（文件）** | 在 `templates/<channel>/` 目录放文件 | **仅 Webhook 与邮件** | 中：文件覆盖默认模板，升级后不会自动获得新字段 |
| **内联 JSON** | 渠道配置中直接粘 JSON 字符串 | **Webhook / 企业微信 / 钉钉** | 高：写死在配置里，最容易漏掉事件专属字段 |
| 默认模板（推荐） | 模板字段留空 | 全部 | 无 |

### ⚠️ 自定义前必读：不要丢掉 `extra`

::: danger 这一步做错会导致内容静默丢失
Webhook 的默认模板用一个特殊值 `"extra": "__type_fields__"` 来开启**事件专属字段**（如追番总结的 `summary`）。你自己写模板时：

- **要保留专属字段** → 原样写下 `"extra": "__type_fields__"`。渲染时这个键会被摘掉，字段被**平铺到 JSON 顶层**。
- **不要**写成 `"extra": {}` 或省略 —— 那样追番总结就只有标题、没有正文。
- **不要**把专属字段手抄成固定的空值（如 `"summary": ""`），那会把真实内容覆盖成空。

**你写的模板就是最终发出的内容**：只有你显式写下 `"extra": "__type_fields__"`，系统才会把事件专属字段附加上去；没写就一个都不会加。这样严格的接收端（会拒绝未知字段的）不会被塞入你没声明过的键。你显式写的键始终优先于自动附加的字段。正因为默认模板已经写好了这一行，我们才建议把「消息模板」留空用默认模板。

再强调一次：`extra` 本身**不会**出现在发出去的 JSON 里——它只是「在这里展开专属字段」的占位标记，发出的字段在**顶层**。
:::

**正确的自定义 Webhook 模板示例**（在默认结构上增加自己的字段）：

```json
{
  "title": "{payload_title}",
  "type": "{notification_type}",
  "timestamp": "{timestamp}",
  "user": "{user_name}",
  "anime": "{title}",
  "episode": "{ep_label}",
  "error": "{error_message}",
  "extra": "__type_fields__",
  "my_custom_field": "固定值",
  "server": "{source}"
}
```

### 自定义模板文件（仅 Webhook / 邮件）

1. 在项目根目录查找 `templates/<channel>/` 目录，其中 `<channel>` 取值为 `webhook` 或 `email`。
2. 在该目录下放置与默认模板**同名**的文件即可覆盖（Webhook 是 `.json`，邮件是 `.html`）。
3. 渠道配置中模板字段**填入文件名**（不含扩展名）。例如 `default` 表示使用 `templates/webhook/default.json`（或邮件的 `templates/email/default.html`）。

**目录结构示例**：

```
templates/
├── webhook/
│   └── default.json         # Webhook 默认模板
└── email/
    └── default.html         # 邮件默认 HTML 模板
```

::: tip Docker 部署
Docker 部署时，需要把 `templates` 目录挂载进容器。在 `docker-compose.yml` 的 `volumes` 加一行：

```yaml
- ./templates:/app/templates
```

:::

### 写一个自定义邮件模板

邮件是**唯一支持 HTML 富文本**的渠道，特别适合追番总结、每日放送早报等需要排版的内容。一个最小的可用模板：

```html
<!DOCTYPE html>
<html>
  <head>
    <title>{payload_title}</title>
  </head>
  <body style="font-family: sans-serif; padding: 20px;">
    <h2 style="color: #dc3545;">{type_icon} {type_display_name}</h2>
    <p><strong>番剧：</strong>{title} {ep_label}</p>
    <p><strong>用户：</strong>{user_name}</p>
    <p><strong>时间：</strong>{timestamp}</p>
    <p><strong>错误：</strong>{error_message}</p>
    <div style="white-space: pre-wrap;">{summary_text}</div>
  </body>
</html>
```

**关键点**：

- 邮件**主题**从 HTML 的 `<title>` 标签提取。写 `{payload_title}` 可得到与默认模板一致的标题（含任务名）；写死文字则所有邮件都是那个标题。
- 邮件**正文**是 `<body>` 内的内容。
- 所有 [可用占位符](#可用占位符-变量) 都可以用 `{变量名}` 写在 HTML 任意位置。
- 内联 CSS 样式（`style="..."`）兼容性最好，避免用 `<style>` 标签或外部 CSS。
- 想保留追番总结正文，务必写上 `{summary_text}`（默认模板里已包含）。

**多套模板按需切换**：文件名自由命名（如 `default.html`、`fancy.html`），不带扩展名的部分就是「模板名」。在渠道配置的「自定义模板文件路径」填该名字即可：

- 留空 → 用默认模板 `templates/notifications/email/default.html`
- 填 `fancy` → 用 `templates/email/fancy.html`

### 邮件模板查找优先级

完整查找顺序（排在前面的优先）：

1. 渠道配置的 `email_subject` 字段（仅覆盖主题，不覆盖正文）
2. 自定义目录 `templates/email/<name>.html`（`<name>` 来自渠道配置的「自定义模板文件路径」）
3. 仓库默认目录 `templates/notifications/email/<name>.html`
4. 代码 fallback（返回 `[Bangumi-Syncer] {payload_title}` 主题 + 空 body）

::: tip 纯文本正文来自同名的 .txt 模板
邮件是 `multipart/alternative`（HTML + 纯文本双部件）。纯文本部件优先取同名的 `templates/notifications/email/<name>.txt`，这样纯文本阅读时不会有 HTML 模板的缩进空白。你只提供 `.html` 时也能工作——系统会退化为「去标签 + 压缩空白」。
:::

### 关于变量转义

插入到 HTML 的变量会被**自动转义**（`<` → `&lt;`），因此媒体库文件名、用户名、AI 总结正文里即便含有 `<script>` 或 `<a href=...>` 之类的标记，也只会作为**普通文字**显示，不会被当成 HTML 执行。

这带来两个你需要注意的点：

- 你**不能**靠变量注入 HTML。想在邮件里放自定义标签，请直接写进模板本身（模板结构不转义，只有变量转义）。
- 邮件**主题**不是 HTML，所以主题里的 `&`、`<` 等字符会正常显示，不会变成 `&amp;`。

纯文本部件（`text/plain`）同样不做转义，保持原文。

::: tip 永远不会报错
查找顺序「自定义目录 → 默认目录 → 代码 fallback」保证**永远不会因为模板问题导致通知发不出去**。最多是「自定义模板没生效」——先确认文件名拼写一致，再确认扩展名正确（Webhook 是 `.json`，邮件是 `.html`）。
:::

### 测试与调试

1. 在「渠道配置」对应 Tab 点击「测试」按钮，会向该渠道发一条固定测试事件，走的是**和线上完全相同**的渲染路径。
2. 改模板后**不需要重启程序**，通知系统每次发送都会重新读模板。
3. 用浏览器开发者工具（F12）预览 HTML，比每次发邮件都快。
4. 部分邮件客户端（如 Outlook）对 CSS 兼容性较差，复杂样式建议用表格布局 + 内联样式。

### 完整示例：追番总结邮件模板

```html
<!DOCTYPE html>
<html>
  <head>
    <title>{payload_title}</title>
  </head>
  <body
    style="font-family: -apple-system, 'PingFang SC', sans-serif; padding: 20px; background-color: #f5f5f5;"
  >
    <div
      style="max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px;"
    >
      <h2
        style="color: #ff6b6b; border-bottom: 2px solid #ff6b6b; padding-bottom: 10px;"
      >
        {type_icon} {type_display_name}
      </h2>
      <p style="color: #666; font-size: 14px;">
        {date_range} · 共 {record_count} 条记录
      </p>
      <div
        style="margin-top: 20px; padding: 15px; background: #f8f9fa; border-left: 4px solid #ff6b6b; white-space: pre-wrap; line-height: 1.7;"
      >
        {summary_text}
      </div>
      <p style="margin-top: 20px; color: #999; font-size: 12px;">
        本邮件由 Bangumi-syncer 自动发送 · {timestamp}
      </p>
    </div>
  </body>
</html>
```

---

## 内联 JSON 示例（企业微信 / 钉钉）

企业微信 / 钉钉不支持模板文件，只能用在渠道配置的「消息模板」字段填内联 JSON。

**企业微信用 Markdown 显示**：

```json
{
  "msgtype": "markdown",
  "markdown": {
    "content": "## {type_icon} {type_display_name}\n\n> **番剧**: {title} {ep_label}\n> **用户**: {user_name}\n> **时间**: {timestamp}\n\n{error_message}"
  }
}
```

同时在企业微信渠道的「消息类型」选 `markdown`。注意这类完全自定义的消息体**不会带事件专属字段**，如需追番总结正文，请把 `{summary_text}` 显式写进 content。

**钉钉加签**：钉钉的「Secret」字段填加签密钥（机器人安全设置选「加签」时给的那串字符），URL 会自动附加 `timestamp` 和 `sign` 参数。

---
## 常见问题

### Q：企业微信 / 钉钉能否用模板文件自定义格式？

不能。当前只有 **Webhook** 和 **邮件** 渠道会读取 `templates/` 目录下的模板文件。企业微信和钉钉的消息体由代码内置构造，自定义格式请在渠道配置的「消息模板」字段填**内联 JSON**。

### Q：怎么知道当前是「默认」还是「自定义」模板？

在渠道配置或通知规则中，模板字段：

- **留空** = 默认（Webhook 走 `webhook/default.json`，邮件走 `email/default.html`，企业微信/钉钉走代码内置构造，站内信走注册表 `in_app_title_template`）
- **Webhook / 邮件**：填了纯字母（如 `default`）= 引用 `templates/<channel>/default.*` 文件
- **Webhook / 企业微信 / 钉钉**：填了 `{` 开头 = 内联 JSON

### Q：自定义模板放错了位置会怎样？

查找顺序「自定义目录 → 默认目录 → 代码 fallback」保证**永远不会报错**。最多是「自定义模板没生效」——先确认文件名拼写一致，再确认扩展名正确（Webhook 是 `.json`，邮件是 `.html`）。

### Q：怎么给「追番总结」单独配模板？

**通常不需要。** 追番总结（`watching_summary_{name}`）用默认模板就能正确发送：Webhook 的 JSON **顶层**会带总结正文（`summary`、`job_name` 等），邮件的正文区也会渲染它，标题里还带任务名。

只有在你想换排版时才需要自定义：

- **Webhook**：在 `templates/webhook/default.json` 放模板（Webhook 所有事件共用 `default.json`）。**务必保留 `"extra": "__type_fields__"`**，否则会丢掉总结正文。
- **邮件**：在 `templates/email/default.html` 放模板（邮件所有事件共用 `default.html`）。**务必保留 `{summary_text}`**。
- **企业微信 / 钉钉**：在渠道配置的「消息模板」字段填内联 JSON，把 `{summary_text}` 写进 content。

::: tip Webhook 与邮件当前共用单文件
当前实现中 Webhook 和邮件各自只有一份 `default.*` 模板，所有事件类型共用。如需按事件类型区分格式，请使用内联 JSON（Webhook / 企业微信 / 钉钉）或修改默认模板文件。
:::

### Q：模板调试有什么技巧？

1. Web 界面的「配置管理 → 通知配置 → 测试」按钮：向指定渠道发一条固定测试事件，走的是**和线上完全相同**的渲染路径（包括事件专属字段）。
2. 临时改模板后无需重启程序：通知系统**每次事件都重新读模板**。
3. 调试自定义模板时，先用 Webhook 收一份默认模板的实际输出作为对照，再在此基础上改——这样不容易漏掉 `"extra": "__type_fields__"` 这一行。

### Q：升级后我的通知内容和以前不一样了？

近期版本对通知模板做了两处修正，都属于「恢复应有内容」：

1. **追番总结的正文以前可能丢失**。原因是配置页曾在你「添加 Webhook」时自动把内置模板填进「消息模板」输入框，保存后这份静态模板会一直覆盖掉事件专属字段。现在配置页不再自动填充；标题也恢复了「📊 追番总结 - 任务名」的形式（此前只有「📊 追番总结」，多个总结任务无法区分）。
2. **邮件正文现在会带上追番总结全文**。此前邮件只显示时间/番剧/用户/来源四行，总结正文没有出现在邮件里。

如果你的「消息模板」字段里存着自己早前粘贴的 JSON，且希望获得上述内容与标题，把它**清空**即可（留空使用内置模板）。留着自定义模板也能工作，但事件专属字段的附加就由你模板里的 `"extra": "__type_fields__"` 决定。

---

## 相关链接

- [⚙️ 配置说明](/config/configuration) — 通知配置之外的其他全局配置
- [🗄️ Bangumi Archive](/config/bangumi-archive) — 与通知系统的冷热路径解耦
- [🔄 Bangumi Replay](/config/bangumi-replay) — 失败补发与通知的关系
