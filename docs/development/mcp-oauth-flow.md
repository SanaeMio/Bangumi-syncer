---
title: 🔁 MCP OAuth 授权流程与内存状态
order: 15
---

# 🔁 MCP OAuth 授权流程与内存状态

> 代码：`app/mcp/provider.py`（`BangumiOAuthProvider`）。全部状态为**进程内存态**，重启即清空。

## 一、授权码流程（内嵌 FastMCP）

```
① 客户端身份
   ├─ DCR：POST /register ──────────────→ _clients（注册表，上限 MAX_CLIENTS=1000）
   └─ CIMD：client_id 为 URL，无注册表；每次按 URL 拉取文档并校验（无状态）

② 发起授权 GET /authorize
   provider.authorize() 校验 client / redirect_uri / scope
   └─ 创建 _pending_auths[request_token]（client_id、redirect_uri、state、
      PKCE challenge、scopes、created_at + CSRF token）→ 302 到 /consent

③ 用户确认 GET/POST /consent
   GET：用 _pending_auths 渲染授权页（顺带 TTL 清理）
   POST Allow：校验 CSRF + BS 会话 → 生成 _auth_codes[code]（+300s，一次性）→ 消费 pending

④ 换取令牌 POST /token
   SDK 校验 code（过期 / 一次性 / PKCE）→ 签发 JWT access token（+1h）
   └─ 写入 _refresh_tokens[不透明字符串]；删除已用 code

⑤ 续期 POST /token（refresh_token grant）
   └─ _refresh_tokens 校验 + 轮换（旧的删、新的存，重新计时）→ 新 access token

⑥ 吊销 POST /revoke
   ├─ access token ──→ _revoked_tokens[jti] = exp
   └─ refresh token ─→ _refresh_tokens.pop()
```

访问校验（`load_access_token`）：RSA 验签 → 检查 `exp` → 检查 `jti ∈ _revoked_tokens`。Access Token 本身不存表。

## 二、状态表角色与生命周期

| 表 | 角色 | 生命周期 | 是否一次性 |
| --- | --- | --- | --- |
| `_clients` | DCR 客户端注册表（CIMD 无） | 长期（重启清） | — |
| `_pending_auths` | consent 交互"暂存单"：绑定 `/authorize` 参数到用户点 Allow | 10 分钟 TTL | 消费即删 |
| `_auth_codes` | 一次性换票凭证：浏览器重定向 → 客户端后端换 token 的安全中转（配合 PKCE） | 5 分钟 | 是 |
| `_refresh_tokens` | 长期续期锚点：JWT 无法"续命"，需服务端钥匙记录 | 30 天（滑动，env `MCP_REFRESH_TOKEN_TTL`） | 轮换 |
| `_revoked_tokens` | JWT 吊销名单：无状态 JWT 的撤销补丁 | 随该 access token 的 `exp` | — |

**为什么不能合并**：各表对应流程不同阶段与不同安全语义——pending 是用户交互态（可重复展示）、code 是兑换凭证（必须一次性）、refresh 是可用钥匙（轮换）、revoked 是作废名单（只读判定）。这是 RFC 6749 / 7009 / 7591 引入实体的固有形态。

## 三、清理策略（内存态，惰性 sweep）

`_cleanup_expired_state()` 在全部状态变更入口调用：`authorize()`、`get_consent_context()`、`exchange_authorization_code()`、`exchange_refresh_token()`、`revoke_token()`。

| 表 | 过期依据 | 过期效果 |
| --- | --- | --- |
| pending | `created_at + 600s` | consent 无法再确认 |
| auth_codes | `expires_at < now` | `/token` 拒绝兑换（SDK 校验）+ sweep 删除 |
| refresh | `expires_at < now`（30 天） | SDK 拒绝刷新 + sweep 删除 |
| revoked | `exp < now` | token 已自然过期，吊销记录删除 |

设计要点：
- 只做惰性清理、不加周期任务（可选 APScheduler，项目已有依赖，但非必需）；
- `_revoked_tokens` 不能设固定周期——必须跟随 access token 的 `exp`，删早了被吊销 token 会在剩余寿命内"复活"；
- 全内存态 → 仅支持单进程部署（多 worker 会导致 refresh/revoked 不一致）。

## 四、重启语义（内存态的边界）

- 重启 = 硬重置：DCR 客户端、refresh、吊销名单全清；access token 在 1h 内仍有效（若 RSA 密钥已持久化）；
- **吊销名单清空 ⇒ 已吊销 access token 会"复活"到自然过期（≤1h）**；
- 唯一落盘的 MCP 认证状态：RSA 密钥对（页面按 `MCP_RSA_PRIVATE_KEY` 路径）。

## 五、代码索引

- `BangumiOAuthProvider`（provider.py）：`authorize` / `register_client` / `exchange_authorization_code` / `exchange_refresh_token` / `load_access_token` / `revoke_token` / `_cleanup_expired_state`
- 构造参数 `required_scopes`（默认 `["read"]`）：传给基类作为 `/mcp` 传输层准入——access token 不含 `read` 时 `RequireAuthMiddleware` 返回 403 `insufficient_scope`（与发放默认值 `_default_scopes` 无关）
- 常量：`PENDING_AUTH_TTL=600`、`AUTH_CODE_TTL=300`、`REFRESH_TOKEN_TTL=30d`、`MAX_CLIENTS=1000`
- 装配：`app/mcp/server.py::_create_provider`（env：`MCP_BASE_URL`、`MCP_REFRESH_TOKEN_TTL`、`MCP_RSA_PRIVATE_KEY/PUBLIC_KEY`）
