---
title: 🔔 通知系统
order: 6
---

# 🔔 通知系统

通知系统采用「事件 → 规则 → 渠道 → 模板」的解耦架构，渠道只负责把已渲染的 payload 发出去。

## 技术栈

- **架构模式**：抽象基类 + 注册表（`ChannelRegistry` / `NotificationRegistry`）
- **HTTP 发送**：[httpx](https://www.python-httpx.org/)（Webhook / 企业微信 / 钉钉）
- **邮件发送**：标准库 `smtplib` + SSL/TLS
- **模板渲染**：`NotificationTemplateManager`（默认模板 / 自定义文件 / 内联 JSON）

---

## NotificationService：路由中枢

`app/services/notification_service.py` 是通知的**路由中枢**：

```
notification_service.notify(event_type, item, source, **kwargs)
  ├─ 构造 payload → 查 NotificationRegistry 取类型元数据
  ├─ 查通知规则 → 命中的规则按 channel_ids 选渠道
  ├─ 渲染模板（默认 / 自定义文件 / 内联 JSON）→ 发送
  └─ 冷却分桶（item 级别 60 秒去重）
```

`NotificationRegistry`（`app/core/notification_registry.py`）是通知类型的注册表，常见分类：`sync_flow`（同步流）、`match_quality`（匹配质量）、`data_source`（数据源）、`scheduler`（调度器）、`bangumi_api`、`system`。

---

## ChannelRegistry：渠道抽象

`app/utils/notifier/channels.py` 定义抽象基类 `NotificationChannel`，`channels_impl.py` 内置 5 个实现：

| channel_type | 类名 | 用途 |
| --- | --- | --- |
| `webhook` | `WebhookChannel` | 通用 HTTP POST/GET |
| `email` | `EmailChannel` | SMTP + HTML 模板 |
| `wecom` | `WeChatWorkChannel` | 企业微信群机器人 |
| `dingtalk` | `DingTalkChannel` | 钉钉群机器人（支持加签） |
| `in_app` | `InAppChannel` | 站内信（写 `in_app_notifications` 表） |

---

## 如何新增通知渠道

以接入「Telegram Bot」为例，需要 4 个改动点：

### 1. 实现渠道类

在 `app/utils/notifier/channels_impl.py` 继承 `NotificationChannel`，设置 `channel_type` / `channel_label`，实现 `send()`：

```python
class TelegramChannel(NotificationChannel):
    channel_type = "telegram"
    channel_label = "Telegram"

    def send(self, notification_type, payload, rendered=None):
        bot_token = self.config.get("bot_token", "")
        chat_id = self.config.get("chat_id", "")
        text = (rendered or {}).get("text") or payload.get("message", "")
        try:
            client = (
                SyncHttpClient(label=f"Telegram#{self.channel_id}", timeout=10.0)
                .prefix("📨")
                .failure_tpl("Telegram 推送失败")
            )
            with client:
                resp = client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                )
            if resp.status_code < 300:
                return ChannelSendResult(True, self.channel_id, self.channel_label)
            return ChannelSendResult(False, self.channel_id, self.channel_label,
                                     f"HTTP {resp.status_code}")
        except Exception as e:
            return ChannelSendResult(False, self.channel_id, self.channel_label, str(e))
```

### 常见参数（基类已提供）

| 配置/方法 | 说明 |
| --- | --- |
| `config["types"]` | 订阅的通知类型（逗号分隔，`"all"` 全订阅）；基类 `supports()` 已实现 |
| `config["enabled"]` | 是否启用；基类构造函数自动读取 |
| `channel_id` | 渠道实例 id（如 `notify-telegram-1`） |
| `send()` 返回 `ChannelSendResult` | 含 `success` / `channel_id` / `message` |

::: tip 渠道只负责"发送"
不要在渠道类里查规则、做路由、管冷却 —— 这些由 `NotificationService` + `NotificationTemplateManager` 完成。
:::

### 2. 注册 SectionMeta

在 `app/core/config_schema.py` 的 `SECTIONS` 字典登记：

```python
"notify-telegram": SectionMeta(
    name="notify-telegram",
    display_name="Telegram 通知",
    order=525,
    is_multi_instance=True,                        # 允许多个 Bot 实例
    sensitive_fields=frozenset({"bot_token"}),     # 自动加密
),
```

### 3. 装配渠道

在 `app/services/notification_service.py::load_channels_from_config` 中仿照 webhook 分支添加：

```python
elif section.startswith("notify-telegram-"):
    cfg = config_manager.get_section(section)
    if not cfg.get("bot_token") or not cfg.get("chat_id"):
        continue
    ch = TelegramChannel(channel_id=section, config=cfg)
    self.channel_registry.register(ch)
    count += 1
```

### 4. 暴露配置 API

在 `app/api/notification.py` 仿照 `/api/notification/webhooks` 的 CRUD 模式，新增 `/api/notification/telegrams` 一组端点（列表 / 创建 / 更新 / 删除 / 测试发送）。测试发送端点可直接实例化 `TelegramChannel` 调用 `send()`。

---

## 完成效果

用户即可在 Web 配置页「Telegram 通知」段创建多个 Bot 实例，并通过「通知规则」绑定到任意通知类型。配置保存后渠道自动热加载，无需重启。
