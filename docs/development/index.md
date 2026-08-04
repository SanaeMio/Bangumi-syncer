---
title: 🛠️ 开发文档
order: 100
---

# 🛠️ 开发文档

本章节面向希望参与开发或接入新驱动的贡献者。普通用户无需阅读。

各篇文档聚焦一个核心模块，介绍其技术栈、扩展方式（如何实现子类）与常见参数。

| 文档                                    | 内容                                                          |
| --------------------------------------- | ------------------------------------------------------------- |
| [🔄 同步服务](./sync-service)           | `CustomItem` 统一模型、`SyncService` 主流程、三段式匹配数据流 |
| [⏰ 调度器框架](./scheduler)            | `BaseScheduler` 继承、`SchedulerRegistry` 注册、cron 与时区   |
| [⚙️ 配置系统](./config)                 | `ConfigManager`、`SectionMeta` 注册表、`FieldMeta` 布尔语义   |
| [🗄️ 数据库仓储层](./database)           | SQLite 表结构、Repository 模式、迁移机制                      |
| [🔐 OAuth 集成](./oauth)                | `OAuthProvider` 注册表、`OAuthService` 通用流程、如何接入新提供方 |
| [🔔 通知系统](./notifications)          | `NotificationChannel` 抽象、如何新增通知渠道                  |
| [🧪 测试与 CI](./testing)               | pytest 组织、HTTP mock、CI 工作流                             |
| [📦 开发环境](./environment)            | uv 安装、代码风格、依赖管理、文档协作                         |
| [🧩 新驱动接入指南](./new-driver-guide) | 从零开始接入新媒体服务器的完整流程                            |
| [📝 贡献指南](./contributing)           | 开发环境搭建、提交前自检、pre-commit 钩子                     |

---

## 技术栈

- **语言**：Python ≥ 3.9
- **Web 框架**：[FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/)
- **模板**：[Jinja2](https://jinja.palletsprojects.com/)
- **数据校验**：[Pydantic](https://docs.pydantic.dev/) v2
- **定时任务**：[APScheduler](https://apscheduler.readthedocs.io/)（AsyncIOScheduler）
- **HTTP 客户端**：[httpx](https://www.python-httpx.org/)
- **数据库**：SQLite（标准库 `sqlite3`，无 ORM）
- **模糊匹配**：[rapidfuzz](https://github.com/maxbachmann/RapidFuzz)
- **包管理**：[uv](https://docs.astral.sh/uv/)
- **静态检查**：[Ruff](https://docs.astral.sh/ruff/)、[djLint](https://www.djlint.com/)
- **测试**：[pytest](https://docs.pytest.org/) + [respx](https://github.com/lundberg/respx) + [pytest-playwright](https://playwright.dev/python/)

---

## 架构概览

**分层 + 驱动委托**：所有媒体服务器驱动最终将各自的数据源转换为统一的 `CustomItem` 模型，再委托给共享的 `SyncService.sync_custom_item()` 完成同步。

```
API 层 (app/api/)          FastAPI 路由：鉴权 → 委托 Service → 响应
    │
Services 层 (app/services/)  sync_service / mapping / notification + 驱动子包
    │                         Webhook 型：extractor → sync_service
    │                         拉取型：reader/client → sync_service + scheduler
    │
Core 层 (app/core/)         Config / Database / SchedulerRegistry / Security
    │
Utils 层 (app/utils/)       BangumiApi / Archive / Notifier / HTTP Client
```

### 驱动分类

- **Webhook 推送型**：媒体服务器主动推送事件（Emby / Jellyfin / Plex / Custom）
- **主动拉取型**：定时调度器读取外部数据源（飞牛 / Fongmi / Trakt）

---

## 目录结构

```
app/
├── main.py                  # FastAPI 入口（lifespan、路由注册、CSP）
├── api/                     # HTTP 路由层
├── core/                    # 配置 / 数据库 / 调度器注册表 / 安全 / 日志
│   ├── config.py / config_schema.py / config_secret_crypto.py
│   ├── database/            # SQLite 仓储层
│   ├── scheduler_registry.py / notification_registry.py
│   └── security.py / logging.py
├── models/                  # Pydantic 模型（sync.py 含 CustomItem）
├── services/
│   ├── sync_service/        # 核心同步服务（多个 Mixin 拆分）
│   ├── base/                # 驱动基类（scheduler.py / notifier_helpers.py）
│   ├── custom/ emby/ jellyfin/ plex/   # Webhook 型驱动
│   ├── feiniu/ fongmi/ trakt/          # 拉取型驱动
│   ├── notification_service.py / mapping_service.py
│   └── scheduler_bootstrap.py          # 调度器注册入口
├── utils/
│   ├── bangumi_api/         # Bangumi API 客户端
│   ├── bangumi_archive/     # 本地归档
│   ├── bangumi_data/        # 离线匹配
│   └── notifier/            # 通知渠道实现
├── templates/               # Jinja2 模板
└── static/                  # 静态资源

tests/                       # 测试（api / core / services / utils / e2e / integration）
docs/                        # VitePress 文档
```
