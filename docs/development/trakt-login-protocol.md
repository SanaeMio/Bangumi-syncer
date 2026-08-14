---
title: 📧 Trakt 邮箱登录协议
order: 11
---

# 📧 Trakt 邮箱登录协议

`app/services/trakt/email_login.py` 实现的「邮箱 + 验证码一键登录」所依赖的
Trakt 认证协议逆向说明。该链路让**非会员/无法创建 OAuth 应用**的用户也能拿到
Bearer 凭证（access_token / refresh_token），从而使用 `apiz.trakt.tv` 数据域
同步，无需 Client ID / Secret / 回调地址。

> 本页面向维护者，属于逆向协议文档。用户侧使用说明见
> [Trakt 使用文档](/usage/trakt)「Bearer 凭证模式」一节。

## 背景

- Trakt 限制免费用户：非会员无法创建 OAuth 应用，且授权应用数量受限。
- 网页版（app.trakt.tv）登录后，前端会持有 `access_token` / `refresh_token`，
  数据请求通过 `Authorization: Bearer <access_token>` 认证。
- 本项目复用 Trakt 官方静态 client_id（`TRAKT_API_KEY`）与官方 OAuth2 token
  端点，通过「邮箱登录」在服务端复刻网页版登录流程，自动换取并落库凭证。

## 认证链（4 步）

整体流程见 `email_login.py`，各步骤协议细节如下。

### ① 发送登录邮件

```
POST https://auth.trakt.tv/auth/magic
Content-Type: application/x-www-form-urlencoded

email=<urlencoded>
```

- 向邮箱发送一封含 **6 位 OTP** 的登录邮件，**5 分钟**有效。
- 同一邮箱重复发信会**作废旧 OTP**（以最新邮件为准）。
- 响应头 `Set-Cookie: __Secure-better-auth.session_token=...`（Better Auth 会话）。

### ② 提交 OTP

```
POST https://auth.trakt.tv/auth/magic/submit
Content-Type: application/x-www-form-urlencoded

email=<urlencoded>&otp=<6位>
```

- `200`：OTP 有效，响应头 `Set-Cookie: __Secure-better-auth.session_token=<value>`
  为已登录会话 cookie，后续 authorize 请求需携带。
- `401`：OTP 无效或已过期（服务端对错误 OTP 返回 401，可据此区分重试与重发）。

### ③ OIDC 授权码流（PKCE S256）

```
GET https://auth.trakt.tv/api/auth/oauth2/authorize
Cookie: __Secure-better-auth.session_token=<value>

response_type=code
client_id=<TRAKT_API_KEY>
redirect_uri=https://app.trakt.tv/callback
scope=public openid profile email offline_access
code_challenge=<S256(verifier)>
code_challenge_method=S256
state=<随机>
```

- 已登录（带会话 cookie）时直接下发授权码，无确认页。
- 授权码通过两种方式之一回传：
  - **302 跳转**：`Location: https://app.trakt.tv/callback?code=...&state=...`
  - **200 + JSON**（SvelteKit 实测路径）：
    `{"redirect": true, "url": "https://app.trakt.tv/callback?code=...&state=..."}`
- 必须校验回跳中的 `state` 与发起时一致（**fail-closed**，缺失或不等一律拒绝），
  防止 CSRF / 授权码注入。

### ④ 授权码换 token

```
POST https://auth.trakt.tv/oauth/token
Content-Type: application/json

{
  "grant_type": "authorization_code",
  "code": <授权码>,
  "redirect_uri": "https://app.trakt.tv/callback",
  "client_id": <TRAKT_API_KEY>,
  "code_verifier": <PKCE verifier>
}
```

- `200` 响应：
  `{access_token, refresh_token, expires_in, expires_at, token_type, scope, id_token}`。
- `access_token` 约 7 天有效；`refresh_token` 为**旋转式**（每次刷新旧值立即作废）。

## 凭证续期（Bearer 模式）

`app/services/trakt/token_refresher.py` 实现续期：

```
POST https://auth.trakt.tv/oauth/token
Content-Type: application/json

{
  "grant_type": "refresh_token",
  "refresh_token": <refresh_token>,
  "client_id": <TRAKT_API_KEY>
}
```

- `200`：旋转换新 `access_token` / `refresh_token` / `expires_at`，落库新值。
- `400` + body 含 `invalid_grant`：refresh 已失效（旋转作废 / 被吊销），
  判定凭证失效并通知用户重新填写。
- 数据请求 401 时：`Authorization: Bearer <access_token>` 失效，刷新后重试即恢复。

## 实现要点（对应评审修复）

| 关注点 | 实现 |
| --- | --- |
| 并发旋转安全 | 按 user_id 持 `asyncio.Lock`，刷新/保存凭证持锁后重读配置 double-check，防止并发互相覆盖新 token |
| 401 强制刷新 | 同步层 401 路径以 `force=True` 调刷新，忽略本地 `expires_at` 的 slack 判断 |
| 心跳智能续期 | 每日心跳按 `expires_at` 剩余 <1 天才真正调 `/oauth/token` |
| OTP 防爆破 | 提交失败计数，超 `MAX_OTP_ATTEMPTS`（5 次）作废会话需重发 |
| 发信限流 | 同一用户 60s 冷却，冷却期返回 429 + `retry_after` |
| state 校验 | 回跳 `state` 缺失或与发起时不一致一律拒绝（fail-closed） |
| 密钥防泄漏 | `AsyncHttpClient` DEBUG 日志对 Authorization / Cookie / Set-Cookie / token / otp / code / verifier 等字段脱敏 |

## 相关代码

| 文件 | 职责 |
| --- | --- |
| `app/services/trakt/email_login.py` | 邮箱登录 4 步协议（发信 → OTP → OIDC → 换 token） |
| `app/services/trakt/token_refresher.py` | Bearer 凭证验证保存 / 续期 / 心跳 |
| `app/services/trakt/client.py` | `TRAKT_API_KEY`、`TRAKT_OAUTH_TOKEN_URL`、`TraktClient`（apiz 数据域） |
| `app/api/trakt.py` | `POST /api/trakt/email-login/start` 与 `/complete` 端点 |
