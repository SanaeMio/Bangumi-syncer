---
title: 🔑 MCP 客户端接入：DCR 与 CIMD
order: 14
---

# 🔑 MCP 客户端接入：DCR 与 CIMD

> 代码：`app/mcp/provider.py`；用户文档：`docs/config/mcp.md`「动态客户端注册（DCR）」。

## 两种客户端身份机制

| 维度 | DCR（RFC 7591） | CIMD（Client ID Metadata Document） |
| --- | --- | --- |
| client_id | 服务端生成的随机串 | 客户端提供的 HTTPS URL |
| 注册 | `POST /register`（未认证可达） | 无注册；每次按 URL 拉取文档 |
| 服务端存储 | `_clients` 内存表，上限 `MAX_CLIENTS=1000` | 无状态，不产生存储 |
| 文档校验 | — | 拉取文档 + `validate_redirect_uri` + client_id 一致性校验 |
| 清理需求 | 有（刷注册/陌生客户端） | 无（天然无注册垃圾） |
| 兼容性 | 被依赖 DCR 的客户端使用 | Claude Desktop 等；关闭 DCR 后仅此路径可用 |

## 安全边界（关键结论）

- **注册 ≠ 授权**：注册成功后仍需 BS 登录会话 + consent 页点 Allow 才能拿到 token；
- `/register` 随 MCP 子应用经 `app.mount("/", mcp_app)` 挂载暴露在根路径，未认证可达；
- 现实风险：公网暴露时被批量注册（占用上限额度）、社工诱导授权；
- 缓解：控制网络暴露面；兜底 = **重启进程清空 `_clients`**（连带清 pending/code/refresh/revoked；access token 在 1h 内仍有效）。

## 重置/管理入口的定位

- **不应做成 MCP 工具**：MCP 不应管理自身会话与授权；
- 若未来实现，应挂 BS 会话鉴权的 Web API（列表/重置/计数展示）。

## 关闭 DCR

- 当前无配置开关；需在装配处传入 `ClientRegistrationOptions(enabled=False)`；
- 关闭后仅 CIMD 客户端可授权，依赖 DCR 的客户端不可用；
- 代码默认：`BangumiOAuthProvider.__init__` 中 `ClientRegistrationOptions(enabled=True, valid_scopes=["read", "write"])`；同时 provider 默认 `required_scopes=["read"]`（`/mcp` 准入底线，与本节的注册/允许集无关）。

## 代码索引

- `get_client()`：CIMD 分支（`self.cimd.is_cimd_client_id`）优先，回落 `_clients`
- `register_client()`：DCR 写入 + 上限校验
- `CIMDClientManager`：`default_scope` 注入（默认 `read`）、文档抓取与校验
