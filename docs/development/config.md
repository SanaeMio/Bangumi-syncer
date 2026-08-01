---
title: ⚙️ 配置系统
order: 4
---

# ⚙️ 配置系统

配置文件是 INI 格式（`config.ini`，首次运行从 `config.example.ini` 自动复制）。`SectionMeta` 注册表是配置的**单一真相源**。

## 技术栈

- **配置格式**：INI（Python 标准库 `ConfigParser`）
- **加密**：`config_secret_crypto.py`（敏感字段写入时自动加密、读取时自动解密）
- **环境变量覆盖**：`env_overrides` 映射（Docker 部署用）

---

## ConfigManager

`app/core/config.py` 的 `ConfigManager` 是 `ConfigParser` 的薄包装，提供：

| 方法 | 用途 |
| --- | --- |
| `get(section, option, fallback=...)` | 读单个配置项 |
| `get_<driver>_config()` | 按驱动聚合配置（如 `get_feiniu_config()`） |
| `get_active_bangumi_config(user_name)` | 按媒体服务器用户名路由到 Bangumi 账号 |
| `get_section(section)` | 读整段为 dict |
| `save_config()` / `reload_config()` | 保存 / 热重载 |

配置路径查找顺序：

1. `CONFIG_FILE` 环境变量
2. `/app/config/config.ini`（Docker 挂载）
3. `config.dev.ini`（开发）
4. `config.ini`（默认）

---

## SectionMeta：注册表

`app/core/config_schema.py` 中的 `SECTIONS` 字典是配置的**单一真相源**。新增配置段只需登记一行，自动覆盖：

- 前端配置页 TOC 与卡片排序（`order`）
- 敏感字段加密白名单（`sensitive_fields`）
- 多实例段标记（`is_multi_instance`，如 `notify-webhook-1`）
- 关联调度器 id（`scheduler_id`，配置保存后联动）
- 关联通知类型（`notification_types`）
- env 覆盖映射（`env_overrides`）
- 字段默认值与布尔语义（`fields`）

### 常见参数

```python
"feiniu": SectionMeta(
    name="feiniu",
    display_name="飞牛影视",
    order=100,                          # 前端 TOC 排序权重，越小越靠前
    scheduler_id="feiniu",              # 配置保存后联动 feiniu_scheduler
    env_overrides={"db_path": "FEINIU_DB_PATH"},  # Docker 用环境变量覆盖
    fields=(
        FieldMeta(name="min_percent", default=85),
        FieldMeta(name="sync_interval", default="*/15 * * * *"),
    ),
),
```

| SectionMeta 字段 | 含义 |
| --- | --- |
| `name` | 段名（INI 中的 `[xxx]`） |
| `display_name` | 前端展示名 |
| `order` | 前端排序权重（越小越靠前） |
| `scheduler_id` | 关联调度器，配置保存后自动联动 |
| `is_multi_instance` | 多实例段（如 `notify-webhook-1`、`summary-daily`） |
| `sensitive_fields` | 敏感字段集合，写入时自动加密 |
| `env_overrides` | 环境变量覆盖映射 |
| `fields` | 字段元数据列表（`FieldMeta`） |
| `notification_types` | 关联通知类型（如 `("airing_today",)`） |

### 常见场景

- **新增配置段**：在 `SECTIONS` 字典登记 `SectionMeta` + 在 `config.example.ini` 加默认节 + 在 `ConfigManager` 加 `get_<driver>_config()` 方法
- **多实例段**：`is_multi_instance=True` 允许用户在 Web 界面创建多个独立实例（如多个 Webhook 渠道）
- **多账号段**：`bangumi-{username}` 通过前缀匹配父段 `bangumi` 的 `sensitive_fields` 继承敏感字段加密

---

## FieldMeta：布尔语义

`FieldMeta` 有三种布尔语义，新增字段时需选择合适的：

| 参数 | 含义 | 适用场景 |
| --- | --- | --- |
| `default=...` | 字段缺失或空字符串时回填的默认值 | 普通字段（数值/字符串） |
| `default_true=True` | 「默认 true」：仅当显式 `false` 时取消勾选（undefined 也视为 true） | 默认启用的开关 |
| `loose_true=True` | 字符串 `'true'` 宽松匹配：INI 中可能存为字符串，undefined 视为 false | INI 中以字符串存储的开关 |

---

## 敏感字段加密

`config_secret_crypto.py` 在写入 INI 时自动加密、读取时自动解密。被 `SectionMeta.sensitive_fields` 标记的字段（如 `access_token`、`smtp_password`、`api_key`）都会自动走加密流程，开发者无需手动处理。

```python
"trakt": SectionMeta(
    name="trakt",
    display_name="Trakt 同步",
    order=120,
    sensitive_fields=frozenset({"client_secret"}),  # 自动加密
),
```

`config.ini` 已加入 `.gitignore`，勿将 Token / 密码 / 私钥写入仓库。
