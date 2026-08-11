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
追番总结这种长文 + 排版 + 表格 + emoji 的内容，强烈建议走邮件渠道。详见下方 [邮件自定义模板教程](#邮件自定义模板教程)。
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

模板决定「消息长什么样」。本系统支持**三种模板来源**：

| 模式                   | 配置方式                             | 适用场景                                                      |
| ---------------------- | ------------------------------------ | ------------------------------------------------------------- |
| **默认模板**           | 渠道配置中模板字段**留空**           | 绝大多数用户；随程序升级自动优化                              |
| **自定义模板（文件）** | 在 `templates/<channel>/` 目录放文件 | 多人/多设备共享同一份自定义格式；**仅 Webhook 与邮件支持**    |
| **内联 JSON**          | 渠道配置中直接粘 JSON 字符串         | 临时调试、单条渠道单独定制；**Webhook / 企业微信 / 钉钉支持** |

### 1. 默认模板

不需要任何配置。系统按渠道使用内置模板：

- **Webhook**：使用 `templates/notifications/webhook/default.json`，结构为：

```json
{
  "title": "{type_icon} {type_display_name}",
  "type": "{notification_type}",
  "timestamp": "{timestamp}",
  "user": "{user_name}",
  "anime": "{title}",
  "episode": "S{season}E{episode}",
  "source": "{source}",
  "error": "{error_message}",
  "extra": {}
}
```

- **邮件**：所有事件共用 `templates/notifications/email/default.html` 单文件。邮件主题从 HTML 的 `<title>` 标签提取，纯文本 body 由 HTML 去标签生成作为 fallback。
- **企业微信 / 钉钉**：渠道配置的「消息模板」字段留空时，由代码内置构造消息体（`text` 或 `markdown` 两种格式）。如需自定义格式，在「消息模板」字段填内联 JSON。
- **站内信**：标题使用注册表中类型的 `in_app_title_template`，正文使用 `error_message` 或 `message` 字段。

### 2. 自定义模板（文件方式）

仅 **Webhook** 与 **邮件** 渠道支持自定义模板文件。企业微信 / 钉钉 / 站内信 不走模板目录查找，自定义格式请使用内联 JSON。

按以下步骤操作：

1. 在项目根目录查找 `templates/<channel>/` 目录，其中 `<channel>` 取值为 `webhook` 或 `email`。
2. 在该目录下放置与默认模板**同名**的文件即可覆盖。
3. 渠道配置中模板字段**填入文件名**（不含扩展名）。例如 `default` 表示使用 `templates/webhook/default.json`（或邮件的 `templates/email/default.html`）。

**目录结构示例**：

```
templates/
├── webhook/
│   └── default.json         # Webhook 默认模板
└── email/
    └── default.html         # 邮件默认 HTML 模板
```

### 3. 内联 JSON

在 Webhook / 企业微信 / 钉钉的「消息模板」字段直接粘 JSON 字符串。**仅对本渠道生效**，不影响默认模板和其他渠道。

适合场景：调试时临时改格式；只想给某一条 Webhook 加点装饰；给企业微信 / 钉钉自定义消息体（这两类渠道不支持模板文件，只能用内联 JSON）。

::: warning 内联 JSON 不能用于邮件
邮件使用 HTML 模板渲染，不支持内联 JSON。要给邮件定制请使用「自定义模板（文件）」或修改默认模板 `templates/notifications/email/default.html`。
:::

---

## 可用占位符（变量）

模板中使用 `{变量名}` 引用数据。所有渠道、所有事件都共享以下变量：

### 基础信息

| 占位符                | 说明                                     | 示例                  |
| --------------------- | ---------------------------------------- | --------------------- |
| `{timestamp}`         | 事件触发时间，格式 `YYYY-MM-DD HH:MM:SS` | `2026-07-30 14:23:11` |
| `{user_name}`         | 媒体服务器上的用户名                     | `alice`               |
| `{source}`            | 触发来源（媒体服务器或事件源）           | `plex`、`emby`        |
| `{notification_type}` | 事件类型标识                             | `mark_failed`         |
| `{type_display_name}` | 事件类型中文展示名                       | `同步失败`            |
| `{type_icon}`         | 事件类型图标（emoji）                    | `❌`                  |

### 番剧与集数

| 占位符         | 说明                                           | 示例                |
| -------------- | ---------------------------------------------- | ------------------- |
| `{title}`      | 番剧主标题（Bangumi 主标题，匹配前为原始标题） | `葬送的芙莉莲`      |
| `{ori_title}`  | 媒体服务器传来的原始标题                       | `Frieren S01E12`    |
| `{bgm_title}`  | 匹配到 Bangumi 后的中文标题                    | `葬送的芙莉莲`      |
| `{season}`     | 季号                                           | `1`                 |
| `{episode}`    | 集号                                           | `12`                |
| `{media_type}` | 媒体类型                                       | `episode` / `movie` |
| `{subject_id}` | Bangumi 番剧 ID                                | `425602`            |
| `{episode_id}` | Bangumi 单集 ID                                | `1234567`           |

### 错误信息

| 占位符            | 说明                         | 示例                     |
| ----------------- | ---------------------------- | ------------------------ |
| `{error_message}` | 错误信息（仅失败类事件有值） | `Bangumi API timeout`    |
| `{error_type}`    | 错误类型分类                 | `network`、`auth`、`api` |

::: tip 未提供的变量怎么办？
模板中未提供的占位符在渲染时会被替换为**空字符串**（不会保留 `{xxx}` 字面量）。所以你可以放心地把所有变量都写进模板，没值时自动留空。
:::

---

## 邮件自定义模板教程

邮件是**唯一支持 HTML 富文本**的渠道，特别适合追番总结、每日放送早报等需要排版的内容。本节从零开始介绍如何写一份自定义邮件模板。

### 第 1 步：选择模板来源

邮件模板有 3 种来源，按需求选一种：

| 想要的效果                         | 推荐方式                                                                           |
| ---------------------------------- | ---------------------------------------------------------------------------------- |
| 不想折腾，用默认样式               | 渠道配置的「自定义模板文件路径」**留空**                                           |
| 覆盖默认样式，所有邮件都用我的样式 | 在 `templates/email/default.html` 放文件                                           |
| 多套样式按需切换                   | 在 `templates/email/<name>.html` 放文件，渠道配置「自定义模板文件路径」填 `<name>` |

### 第 2 步：创建模板文件

在项目根目录创建 `templates/email/` 目录，里面放一个 `.html` 文件。文件名自由命名（如 `default.html`、`fancy.html`），不带扩展名的部分就是「模板名」。

**目录结构示例**：

```
项目根目录/
└── templates/
    └── email/
        ├── default.html      # 模板名 = default
        └── fancy.html        # 模板名 = fancy
```

::: tip Docker 部署
Docker 部署时，需要把 `templates` 目录挂载进容器。在 `docker-compose.yml` 的 `volumes` 加一行：

```yaml
- ./templates:/app/templates
```

:::

### 第 3 步：编写 HTML 模板

一个最小的邮件模板示例：

```html
<!DOCTYPE html>
<html>
  <head>
    <title>[Bangumi-Syncer] {type_display_name}</title>
  </head>
  <body style="font-family: sans-serif; padding: 20px;">
    <h2 style="color: #dc3545;">{type_icon} {type_display_name}</h2>
    <p><strong>番剧：</strong>{title} S{season}E{episode}</p>
    <p><strong>用户：</strong>{user_name}</p>
    <p><strong>时间：</strong>{timestamp}</p>
    <p><strong>错误：</strong>{error_message}</p>
  </body>
</html>
```

**关键点**：

- 邮件**主题**从 HTML 的 `<title>` 标签提取。所以 `<title>` 里写什么，邮件标题就是什么。
- 邮件**正文**是 `<body>` 内的内容。
- 所有 [可用占位符](#可用占位符-变量) 都可以用 `{变量名}` 写在 HTML 任意位置。
- 内联 CSS 样式（`style="..."`）兼容性最好，避免用 `<style>` 标签或外部 CSS。

### 第 4 步：在渠道配置中填模板名

打开「配置管理 → 通知配置 → 渠道配置 → 邮件 Tab」，编辑你的邮件渠道：

- **自定义模板文件路径**：填模板名（**不带扩展名**）。
  - 留空 → 用默认模板 `templates/notifications/email/default.html`
  - 填 `default` → 用 `templates/email/default.html`
  - 填 `fancy` → 用 `templates/email/fancy.html`
- **邮件标题模板**：留空则从 HTML 的 `<title>` 提取；填了则覆盖主题。

保存后立即生效，**无需重启程序**。

### 第 5 步：测试模板

1. 在「渠道配置 → 邮件 Tab」点击「测试」按钮，会向该渠道发一条固定测试事件。
2. 收到邮件后检查样式是否符合预期。
3. 改完模板再点「测试」，反复迭代。

::: tip 调试小技巧

- 改模板后**不需要重启程序**，通知系统每次发邮件都会重新读模板。
- 用浏览器开发者工具（F12）预览 HTML，比每次发邮件都快。
- 部分邮件客户端（如 Outlook）对 CSS 兼容性较差，复杂样式建议用表格布局 + 内联样式。
  :::

### 完整示例：追番总结邮件模板

```html
<!DOCTYPE html>
<html>
  <head>
    <title>番剧追番总结 - {timestamp}</title>
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
        📺 追番总结
      </h2>
      <p style="color: #666; font-size: 14px;">
        {timestamp} · 用户：{user_name}
      </p>
      <div
        style="margin-top: 20px; padding: 15px; background: #f8f9fa; border-left: 4px solid #ff6b6b;"
      >
        <strong>番剧：</strong>{title}<br />
        <strong>进度：</strong>S{season}E{episode}<br />
        <strong>来源：</strong>{source}
      </div>
      <p style="margin-top: 20px; color: #999; font-size: 12px;">
        本邮件由 Bangumi-syncer 自动发送
      </p>
    </div>
  </body>
</html>
```

### 邮件模板查找优先级

完整查找顺序（排在前面的优先）：

1. 渠道配置的 `email_subject` 字段（仅覆盖主题，不覆盖正文）
2. 自定义目录 `templates/email/<name>.html`（`<name>` 来自渠道配置的「自定义模板文件路径」）
3. 仓库默认目录 `templates/notifications/email/<name>.html`
4. 代码 fallback（返回 `[Bangumi-Syncer] {type_display_name}` 主题 + 空 body）

::: tip 永远不会报错
查找顺序「自定义目录 → 默认目录 → 代码 fallback」保证**永远不会因为模板问题导致通知发不出去**。最多是「自定义模板没生效」——先确认文件名拼写一致，再确认扩展名是 `.html`。
:::

---

## 自定义模板示例（其他渠道）

### 示例 1：企业微信用 Markdown 显示

企业微信不支持模板文件，需在渠道配置的「消息模板」字段直接粘内联 JSON：

```json
{
  "msgtype": "markdown",
  "markdown": {
    "content": "## {type_icon} {type_display_name}\n\n> **番剧**: {title} S{season}E{episode}\n> **用户**: {user_name}\n> **时间**: {timestamp}\n\n{error_message}"
  }
}
```

同时在企业微信渠道的「消息类型」选 `markdown`。

### 示例 2：钉钉加签 + 自定义标题

钉钉的「Secret」字段填加签密钥（机器人安全设置选「加签」时给的那串字符），URL 会自动附加 `timestamp` 和 `sign` 参数。要替换消息文案，在钉钉渠道的「消息模板」字段填内联 JSON（钉钉不支持模板文件）。

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

`watching_summary_{name}` 是一类动态事件。给这一类事件自定义模板：

- **Webhook**：在 `templates/webhook/default.json` 放模板（Webhook 所有事件共用 `default.json`）
- **邮件**：在 `templates/email/default.html` 放模板（邮件所有事件共用 `default.html`）
- **企业微信 / 钉钉**：在渠道配置的「消息模板」字段填内联 JSON

::: tip Webhook 与邮件当前共用单文件
当前实现中 Webhook 和邮件各自只有一份 `default.*` 模板，所有事件类型共用。如需按事件类型区分格式，请使用内联 JSON（Webhook / 企业微信 / 钉钉）或修改默认模板文件。
:::

### Q：模板调试有什么技巧？

1. Web 界面的「配置管理 → 通知配置 → 测试」按钮：向指定渠道发一条固定测试事件，模板会按占位符替换。
2. 临时改模板后无需重启程序：通知系统**每次事件都重新读模板**。
3. Webhook 类渠道的「消息模板」字段可临时粘 JSON 调试，调好后再固化到 `templates/`。

---

## 相关链接

- [⚙️ 配置说明](/config/configuration) — 通知配置之外的其他全局配置
- [🗄️ Bangumi Archive](/config/bangumi-archive) — 与通知系统的冷热路径解耦
- [🔄 Bangumi Replay](/config/bangumi-replay) — 失败补发与通知的关系
