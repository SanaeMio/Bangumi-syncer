---
title: 🔔 通知系统配置
order: 25
---

# 🔔 通知系统配置
通知系统是 Bangumi-syncer 与外部世界沟通的桥梁：同步成功、失败、收到请求、追番总结完成等事件发生时，可以**自动推送**到 Webhook、邮件、企业微信群机器人、钉钉群机器人等渠道。配置项全部在「配置管理 → 通知配置」中可视化完成，**不需要手写 INI**。
本文档说明：
- 通知系统的整体结构（**事件 → 规则 → 渠道 → 模板**）
- 渠道的类型（Webhook / 邮件 / 企业微信 / 钉钉）与各自配置项
- **模板系统**：默认模板、自定义模板、内联 JSON 三种模式
- 模板中可用的全部占位符
- 自定义模板的存放位置与命名规则
## 整体结构
通知系统由四层组成，按数据流向串联：
```
触发事件 (notification_type)
↓
匹配通知规则 (notify-rule)         ← 可选：未配置规则时按渠道默认订阅
↓
渲染模板 (template_manager)         ← 默认 / 自定义 / 内联 JSON
↓
发送到渠道 (webhook / email / wecom / dingtalk)
```
- **触发事件**：由系统内部产生，如 `mark_success`（同步成功）、`mark_failed`（同步失败）、`request_received`（收到请求）、`bangumi_id_found`（匹配到番剧）、`watching_summary_{name}`（追番总结完成）。
- **通知规则**：在「通知配置」面板中以卡片形式管理。一个规则 = 「勾选若干触发事件 + 选择若干渠道 + 可选自定义模板」。
- **渠道**：实际发送方。同一类渠道（Webhook、邮件、企业微信、钉钉）支持添加**多个实例**。
- **模板**：决定消息长什么样。可用「默认」、可用「自定义模板文件」、也可以在配置里直接粘 JSON 字符串。
::: tip 新格式 vs 旧格式
旧版本中每条渠道各自带一个 `types`（订阅哪些事件）字段。新格式把所有订阅关系**集中到「通知规则」**里，渠道只负责**连接配置**——这样「换事件订阅」和「换渠道」互不干扰。
:::
## 渠道配置
在「通知配置」卡片右上角点击 **「渠道配置」** 打开渠道配置模态框。模态框内有 4 个 Tab：**Webhook / 邮件 / 企业微信 / 钉钉**，每个 Tab 独立管理一类渠道的多个实例。
> 渠道配置在模态框内**直接内联编辑**，不需要弹二级弹窗。「添加配置」展开空白表单，「编辑」加载已有数据，「保存」后立即生效，「删除」通过浏览器原生 `confirm()` 二次确认。
### Webhook
适用于：钉钉、Telegram Bot、飞书、Bark、企业微信、Discord、Slack、自建网关等所有「接收一个 HTTP 请求」的服务。
| 字段 | 必填 | 说明 |
| --- | --- | --- |
| 启用 | 是 | 关闭后该渠道不参与任何事件分发 |
| Webhook URL | 是 | 完整 HTTP(S) 地址 |
| 请求方法 | 否 | `POST`（默认）或 `GET`。`POST` 发送 JSON body；`GET` 把 payload 作为 query string |
| 自定义请求头 | 否 | JSON 格式，例如 `{"Authorization":"Bearer xxx"}`；也支持 `K:V,K:V` 简写 |
| 消息模板 | 否 | 详见下方「模板系统」；留空使用默认模板 |
### 邮件
适用于：QQ 邮箱、Gmail、Outlook、自建 SMTP、企业邮箱等。
| 字段 | 必填 | 说明 |
| --- | --- | --- |
| 启用 | 是 | 关闭后该渠道不参与任何事件分发 |
| SMTP 服务器 | 是 | 例如 `smtp.qq.com`、`smtp.gmail.com` |
| 端口 | 是 | SSL 通常 `465`；STARTTLS 通常 `587`；不加密通常 `25` |
| SMTP 用户名 | 是 | 完整邮箱地址 |
| SMTP 密码/授权码 | 是（新建） | 编辑时**留空表示不修改**；QQ 邮箱等需要「授权码」而不是登录密码 |
| 发件人地址 | 否 | 留空使用 SMTP 用户名 |
| 收件人地址 | 是 | 支持多个收件人，逗号或分号分隔 |
| 使用 TLS 加密 | 是 | 端口 465 时务必开启（SSL on connect） |
| 邮件标题模板 | 否 | 留空使用默认模板 |
| 自定义模板文件路径 | 否 | 高级选项，指向本地 `email/<name>.{subject.txt,body.txt,html}` 三件套；留空使用默认模板 |
### 企业微信
适用于：企业微信群机器人。
| 字段 | 必填 | 说明 |
| --- | --- | --- |
| 启用 | 是 | 关闭后该渠道不参与任何事件分发 |
| Webhook Key | 是 | 群机器人 Webhook URL 中的 `key=` 参数值；也可直接粘完整 URL |
| 消息类型 | 否 | `text`（默认）或 `markdown` |
::: tip 渠道复用模板
企业微信底层是 Webhook 的一种，因此**共用 Webhook 默认模板**（`webhook/default.json`）。如果想给企业微信单独配格式，方法见「模板系统 → 自定义模板」一节。
:::
### 钉钉
适用于：钉钉群机器人（自定义机器人）。
| 字段 | 必填 | 说明 |
| --- | --- | --- |
| 启用 | 是 | 关闭后该渠道不参与任何事件分发 |
| Access Token | 是 | 机器人 Webhook URL 中的 `access_token=` 参数值；也可直接粘完整 URL |
| Secret | 否 | 「加签」模式密钥；填了之后请求会带 HMAC-SHA256 签名 |
| 消息类型 | 否 | `text`（默认）或 `markdown` |
::: tip 渠道复用模板
钉钉底层是 Webhook 的一种，因此**共用 Webhook 默认模板**（`webhook/default.json`）。如果想给钉钉单独配格式，方法见「模板系统 → 自定义模板」一节。
:::
## 通知规则
在「通知配置」卡片右上角点击 **「新建配置」** 打开规则编辑器。
| 字段 | 必填 | 说明 |
| --- | --- | --- |
| 启用此规则 | 是 | 关闭后该规则不参与任何事件分发 |
| 规则名称 | 是 | 自己看的名称，例如「同步失败钉钉告警」 |
| 触发事件 | 否 | 勾选要订阅的事件；**全部不勾选 = 订阅全部事件** |
| 通知渠道 | 是 | 至少选择 1 个渠道实例（多选） |
| 自定义模板 | 否 | 留空则使用渠道自身配置的模板 |
**举例**：
- 「同步失败钉钉告警」：事件勾选 `mark_failed`，渠道勾选 `notify-dingtalk-1`
- 「追番总结每天邮件」：事件勾选 `watching_summary` 前缀的所有事件，渠道勾选 `notify-email-1`
- 「所有事件 → Webhook + 邮件双通道」：事件不勾选，渠道勾选所有
> 当**未创建任何规则**时，系统会回退到「传统模式」：每个启用渠道按自身配置的 `types` 字段订阅事件（`all` 或留空表示订阅全部）。这种兜底行为是为了兼容升级前的旧配置。
## 模板系统
模板决定「消息长什么样」。本系统支持**三种模板来源**：
| 模式 | 配置方式 | 适用场景 |
| --- | --- | --- |
| **默认模板** | 渠道配置中模板字段**留空** | 绝大多数用户；随程序升级自动优化 |
| **自定义模板（文件）** | 在 `custom_templates/<channel>/<name>.{json,subject.txt,body.txt,html}` 放文件，渠道配置中填入文件名 | 多人/多设备共享同一份自定义格式 |
| **内联 JSON** | 渠道配置中直接粘 JSON 字符串 | 临时调试、单条渠道单独定制 |
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
- **企业微信 / 钉钉**：渠道配置的「消息模板」字段留空时，由代码内置构造消息体（`text` 或 `markdown` 两种格式），不读取模板目录。如需自定义格式，在「消息模板」字段填内联 JSON（见「内联 JSON」一节）。
- **站内信**：标题使用注册表中类型的 `in_app_title_template`（如「同步失败：{title} {ep_label}」），正文使用 `error_message` 或 `message` 字段。
### 2. 自定义模板（文件方式）
仅 **Webhook** 与 **邮件** 渠道支持自定义模板文件。企业微信 / 钉钉 / 站内信 不走模板目录查找，自定义格式请使用内联 JSON 或修改渠道代码。
按以下步骤操作：
1. 在项目根目录创建 `custom_templates/<channel>/` 目录，其中 `<channel>` 取值为 `webhook` 或 `email`。
2. 在该目录下放置与默认模板**同名**的文件即可覆盖。
3. 渠道配置中模板字段**填入文件名**（不含扩展名）。例如 `default` 表示使用 `custom_templates/webhook/default.json`（或邮件的 `custom_templates/email/default.html`）。
**目录结构示例**：
```
custom_templates/
├── webhook/
│   └── default.json         # 覆盖 Webhook 默认模板
└── email/
    └── default.html         # 覆盖邮件默认 HTML 模板
```
> 优先级：**自定义目录同名文件 > 仓库默认模板 > 渠道内置 fallback**。也就是说只要在 `custom_templates/` 里放文件就会自动覆盖同名默认模板，**不需要改任何渠道配置**。
### 3. 内联 JSON
在 Webhook / 企业微信 / 钉钉的「消息模板」字段直接粘 JSON 字符串。**仅对本渠道生效**，不影响默认模板和其他渠道。
适合场景：调试时临时改格式；只想给某一条 Webhook 加点装饰；给企业微信 / 钉钉自定义消息体（这两类渠道不支持模板文件，只能用内联 JSON）。
::: warning 内联 JSON 不能用于邮件
邮件使用 HTML 模板渲染，不支持内联 JSON。要给邮件定制请使用「自定义模板（文件）」或修改默认模板 `templates/notifications/email/default.html`。
:::
### 模板查找优先级总结
不同渠道的查找行为不同：

**Webhook 渠道**：
1. 渠道配置的 `template` 字段（若以 `{` 开头则按内联 JSON 解析）
2. 自定义目录 `custom_templates/webhook/default.json`
3. 仓库默认目录 `templates/notifications/webhook/default.json`
4. 代码 fallback（返回原始 payload 字典）

**邮件渠道**：
1. 渠道配置的 `template` 字段（作为模板名，如 `default`）
2. 自定义目录 `custom_templates/email/<name>.html`
3. 仓库默认目录 `templates/notifications/email/<name>.html`
4. 代码 fallback（返回 `[Bangumi-Syncer] {type_display_name}` 主题 + 空 body）
5. 渠道配置的 `email_subject` 字段（若设置则覆盖主题）

**企业微信 / 钉钉渠道**：
1. 渠道配置的 `template` 字段（按内联 JSON 解析；解析失败则走代码构造）
2. 代码内置构造（`text` 或 `markdown` 格式，由 `msg_type` 配置决定）

**站内信**：
1. 自定义目录 `custom_templates/in_app/<type>.title.txt` 与 `body.txt`
2. 仓库默认目录 `templates/notifications/in_app/<type>.title.txt` 与 `body.txt`（当前未内置）
3. 注册表中类型的 `in_app_title_template`（标题）+ `error_message`/`message`（正文）
## 可用占位符
模板中使用 `{变量名}` 引用数据。所有渠道、所有事件都共享以下变量：
| 占位符 | 说明 | 示例 |
| --- | --- | --- |
| `{timestamp}` | 事件触发时间，格式 `YYYY-MM-DD HH:MM:SS` | `2026-07-30 14:23:11` |
| `{user_name}` | 媒体服务器上的用户名 | `alice` |
| `{title}` | 番剧主标题（Bangumi 主标题，匹配前为原始标题） | `葬送的芙莉莲` |
| `{ori_title}` | 媒体服务器传来的原始标题 | `Frieren S01E12` |
| `{bgm_title}` | 匹配到 Bangumi 后的中文标题 | `葬送的芙莉莲` |
| `{season}` | 季号 | `1` |
| `{episode}` | 集号 | `12` |
| `{source}` | 触发来源（媒体服务器或事件源） | `plex`、`emby` |
| `{notification_type}` | 事件类型标识 | `mark_failed` |
| `{type_display_name}` | 事件类型中文展示名 | `同步失败` |
| `{type_icon}` | 事件类型图标（emoji） | `❌` |
| `{error_message}` | 错误信息（仅失败类事件有值） | `Bangumi API timeout` |
| `{error_type}` | 错误类型分类 | `network`、`auth`、`api` |
| `{subject_id}` | Bangumi 番剧 ID | `425602` |
| `{episode_id}` | Bangumi 单集 ID | `1234567` |
| `{media_type}` | 媒体类型 | `episode` / `movie` |
模板中未提供的占位符在渲染时会被替换为**空字符串**（不会保留 `{xxx}` 字面量）。
## 自定义模板示例
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
### 示例 3：邮件用 HTML 富文本
在 `custom_templates/email/default.html` 放 HTML（覆盖默认模板），或在渠道配置的「模板」字段填自定义名称（如 `my_style`，对应 `custom_templates/email/my_style.html`）。HTML 模板中可用 `{title}` 等全部占位符，邮件主题从 `<title>` 标签提取。
```html
<!DOCTYPE html>
<html>
    <head><title>[Bangumi-Syncer] {type_display_name}</title></head>
    <body style="font-family: sans-serif; padding: 20px;">
        <h2 style="color: #dc3545;">❌ 同步失败</h2>
        <p>
            <strong>番剧：</strong>{title} S{season}E{episode}
        </p>
        <p>
            <strong>用户：</strong>{user_name}
        </p>
        <p>
            <strong>时间：</strong>{timestamp}
        </p>
        <p>
            <strong>错误：</strong>{error_message}
        </p>
    </body>
</html>
```
## 常见问题
### Q：企业微信 / 钉钉能否用模板文件自定义格式？
不能。当前只有 **Webhook** 和 **邮件** 渠道会读取 `custom_templates/` 目录下的模板文件。企业微信和钉钉的消息体由代码内置构造，自定义格式请在渠道配置的「消息模板」字段填**内联 JSON**。
### Q：怎么知道当前是「默认」还是「自定义」模板？
在渠道配置或通知规则中，模板字段：
- **留空** = 默认（Webhook 走 `webhook/default.json`，邮件走 `email/default.html`，企业微信/钉钉走代码内置构造，站内信走注册表 `in_app_title_template`）
- **Webhook / 邮件**：填了纯字母（如 `default`）= 引用 `custom_templates/<channel>/default.*` 文件
- **Webhook / 企业微信 / 钉钉**：填了 `{` 开头 = 内联 JSON
### Q：自定义模板放错了位置会怎样？
查找顺序「自定义目录 → 默认目录 → 代码 fallback」保证**永远不会报错**。最多是「自定义模板没生效」——先确认文件名拼写一致，再确认扩展名正确（Webhook 是 `.json`，邮件是 `.html`）。
### Q：怎么给「追番总结」单独配模板？
`watching_summary_{name}` 是一类动态事件。给这一类事件自定义模板：
- Webhook：在 `custom_templates/webhook/default.json` 放模板（Webhook 所有事件共用 `default.json`）
- 邮件：在 `custom_templates/email/default.html` 放模板（邮件所有事件共用 `default.html`）
- 企业微信 / 钉钉：在渠道配置的「消息模板」字段填内联 JSON
::: tip Webhook 与邮件当前共用单文件
当前实现中 Webhook 和邮件各自只有一份 `default.*` 模板，所有事件类型共用。如需按事件类型区分格式，请使用内联 JSON（Webhook / 企业微信 / 钉钉）或修改默认模板文件。
:::
### Q：模板调试有什么技巧？
1. Web 界面的「配置管理 → 通知配置 → 测试」按钮：向指定渠道发一条固定测试事件，模板会按占位符替换。
2. 临时改模板后无需重启程序：通知系统**每次事件都重新读模板**。
3. Webhook 类渠道的「消息模板」字段可临时粘 JSON 调试，调好后再固化到 `custom_templates/`。
## 相关链接
- [⚙️ 配置说明](/configuration) — 通知配置之外的其他全局配置
- [🗄️ Bangumi Archive](/bangumi-archive) — 与通知系统的冷热路径解耦
- [🔄 Bangumi Replay](/bangumi-replay) — 失败补发与通知的关系
