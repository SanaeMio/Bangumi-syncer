---
title: 🛡️ MCP Scope 模型与权限边界
order: 16
---

# 🛡️ MCP Scope 模型与权限边界

> 代码：`app/mcp/provider.py`、`app/mcp/tools.py`；用户文档：`docs/config/mcp.md`「授权与权限」「安全建议」。

## 现状（read / write 两档）

| scope | 工具 | 说明 |
| --- | --- | --- |
| `read`（默认） | `get_logs`、`get_current_config` | 配置读取时敏感字段已掩码（含 `auth.password` / `auth.secret_key`） |
| `write` | `update_config` | 可写配置，**当前包含敏感字段**（`bangumi.access_token`、`llm.api_key`、`notify-*.smtp_password` 等） |

## 三层 scope 概念（勿混用）

| 层 | 字段/常量 | 作用 | 落点 |
| --- | --- | --- | --- |
| 允许集 | `_ALLOWED_SCOPES` / `valid_scopes` | DCR / CIMD 校验请求 scope 是否合法（越权 → `invalid_scope`） | `provider.py` 构造参数 + 装配函数 |
| 发放默认值 | `_default_scopes` | 客户端未显式请求 scope 时实际签发的值（`read`） | `authorize` / `register_client` / CIMD `default_scope` 三处 |
| 准入底线 | `required_scopes`（默认 `["read"]`） | `/mcp` 传输层校验 access token 的 scope，不含 `read` 即 403 `insufficient_scope` | `provider.py` 构造参数 + `server.py` 装配 |

硬性边界（与 scope 无关）：

- `auth` 段永远禁写；
- MCP 工具集固定为 3 个，不存在任意 API / 命令执行；
- 客户端请求的 scope 必须 ⊆ 其注册/声明的 scope（SDK `validate_scope` 校验，越权返回 `invalid_scope`）；
- 客户端不请求 scope 时默认只发 `read`（DCR 注册、CIMD `default_scope`、authorize 三处一致）；
- **`/mcp` 传输层要求 `read`**（`required_scopes=["read"]`）：仅含 `write` 的 token 被 403 拒绝；工具级 `_require_read_scope` 为 defense-in-depth（传输层已先拦一道）。

## 未来可选：三档分级（暂缓）

| 档位 | scope | 授予能力 |
| --- | --- | --- |
| 只读（默认） | `read` | get_logs + get_current_config |
| 读写 | `read write` | + update_config，但拒绝敏感字段（`is_sensitive_field` 命中即拒） |
| 全权 | `read write admin` | + 允许写敏感字段（auth 段仍禁） |

更细粒度（`logs:read` / `config:read` / `config:write` / `secrets:write`）暂不考虑：OAuth scope 无继承语义、consent 页与客户端适配成本高。

## 代码索引

- `_require_read_scope()`（tools.py）：read 校验；`update_config` 内 write 校验（均为 defense-in-depth）
- `valid_scopes`：provider.py 两处（provider 默认参数、装配函数）
- `required_scopes`：provider.py 构造参数（默认 `["read"]`）+ `app/mcp/server.py::_create_provider` 装配
- consent 页展示 scopes（provider.py `handle_consent`）
