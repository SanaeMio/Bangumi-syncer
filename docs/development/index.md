---
title: 🛠️ 开发文档
order: 35
---

# 🛠️ 开发文档

本章节面向希望参与开发或接入新驱动的贡献者，介绍项目架构与扩展方式。

## 架构概览

Bangumi-syncer 采用「驱动委托」架构：所有媒体服务器驱动最终将各自的数据源转换为统一的 `CustomItem` 模型，再委托给共享的 `SyncService.sync_custom_item()` 完成同步。

驱动分为两类：

- **Webhook 推送型**：媒体服务器主动推送事件（Emby / Jellyfin / Plex / Custom）
- **主动拉取型**：定时调度器读取外部数据源（飞牛 / Fongmi / Trakt）

## 文档索引

- [新驱动接入指南](./new-driver-guide)：从零开始为一个新媒体服务器编写驱动，包含完整的 9 步接入流程与代码示例。
- [AGENTS.md](./agents)：面向 AI 编码助手与仓库协作者的快速上下文，包含项目概述、仓库地图、常用命令与架构约定。
- [CLAUDE.md](./claude)：Claude Code 通用行为补充指南，涵盖编码前思考、简洁优先、外科手术式改动与目标驱动执行。
- [CONTRIBUTING.md](./contributing)：人类贡献流程，包含开发环境搭建、提交前自检、pre-commit 钩子与文档协作说明。

## 相关资源

- [项目路线图](/roadmap)：已实现功能与未来计划。
- [贡献、鸣谢与许可](/community)：参与方式与鸣谢信息。
