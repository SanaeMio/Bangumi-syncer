---
title: 🔐 OAuth 集成
order: 6
---

# 🔐 OAuth 集成

`app/services/oauth/` 是通用 OAuth 2.0 抽象层，让 Bangumi、Trakt 等不同授权方共用同一套授权 URL 构建 / 令牌交换 / 刷新 / CSRF state 管理逻辑，仅需登记一个 `OAuthProvider` 即可接入新来源。

## 技术栈

- **协议**：OAuth 2.0 Authorization Code Flow
- **HTTP 客户端**：[httpx](https://www.python-httpx.org/)（同步，30s 超时）
- **state 存储**：SQLite `oauth_states` 表（带 TTL，原子消费）
- **回调策略**：动态 redirect_uri（前端传入，绑定到 state 落库）

---

## OAuthProvider：提供方配置

`app/services/oauth/provider.py` 用 dataclass 定义单个 OAuth 提供方的静态配置，`OAuthProviderRegistry` 是提供方注册表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | `str` | 提供方标识（如 `"bangumi"`、`"trakt"`） |
| `authorize_url` | `str` | 授权端点 |
| `token_url` | `str` | 令牌端点 |
| `redirect_path` | `str` | 回调路径 |
| `scopes` | `list[str]` | OAuth scopes |
| `extra_auth_params` | `dict` | 额外授权参数 |
| `get_credentials` | `Callable[[], tuple[str, str]]` | 返回 `(client_id, client_secret)` |
| `get_redirect_uri` | `Callable[[], str]` | 返回默认回调地址 |

`get_credentials` / `get_redirect_uri` 以可调用形式提供，便于延迟导入，避免与具体媒体源模块形成循环依赖。内置提供方在 `app/services/oauth/providers.py` 登记：

```python
BANGUMI_PROVIDER = OAuthProvider(
    name="bangumi",
    authorize_url="https://bgm.tv/oauth/authorize",
    token_url="https://bgm.tv/oauth/access_token",
    redirect_path="/api/oauth/bangumi/callback",
    scopes=[],
    extra_auth_params={"response_type": "code"},
    get_credentials=_bangumi_credentials,   # 延迟导入 app.services.bangumi.auth
    get_redirect_uri=_bangumi_redirect,
)
```

---

## OAuthService：通用服务

`app/services/oauth/service.py` 是无状态的服务层，所有 state 落库到 `oauth_states` 表，通过 `get_oauth_service()` 获取共享实例。核心能力：`create_state` / `consume_state`（CSRF 防护）、`build_authorize_url`、`exchange_code`、`refresh_token`。

### 常见参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `OAUTH_STATE_TTL` | `600` | state 有效期（秒），防止重放 |
| 刷新提前量 | `3600` | token 临近过期阈值（提前 1 小时刷新） |
| HTTP 超时 | `30.0` | 令牌交换/刷新的 HTTP 超时 |

::: tip state 原子消费
`consume_state` 通过 `delete_oauth_state` 的 `rowcount` 判断是否消费成功，避免 SELECT-then-DELETE 在并发下双重消费 state 导致 CSRF 防护失效。
:::

---

## 如何接入新 OAuth 提供方

以接入「MyAnimeList」为例，需要 3 个改动点：

### 1. 登记提供方

在 `app/services/oauth/providers.py` 追加一个 `OAuthProvider`，并在 `__init__.py` 注册：

```python
MAL_PROVIDER = OAuthProvider(
    name="mal",
    authorize_url="https://myanimelist.net/v1/oauth2/authorize",
    token_url="https://myanimelist.net/v1/oauth2/token",
    redirect_path="/api/oauth/mal/callback",
    scopes=["read"],
    extra_auth_params={"response_type": "code"},
    get_credentials=_mal_credentials,   # 延迟导入，返回 (client_id, client_secret)
    get_redirect_uri=_mal_redirect,     # 延迟导入，返回默认回调地址
)
```

### 2. 实现特有 auth 服务

参考 `app/services/bangumi/auth.py` 的 `BangumiAuthService`，实现：

- `get_app_credentials()` / `get_redirect_uri()`：凭证解析（配置文件 → 环境变量优先级）
- 令牌落地：将 token 写入对应数据库表（upsert 账号，保留用户配置）
- 用户信息补全：若提供方令牌响应不含 username，需调用用户信息接口补全（参考 `_fetch_me_info`）
- Token 刷新：per-section 锁 + double-check 避免并发重复刷新

### 3. 实现 API 路由

参考 `app/api/bangumi_oauth.py`，实现 `/start`、`/callback`、`/close`、`/disconnect` 四个接口。通用 `OAuthService` 已处理 state 管理、授权 URL 构建、令牌交换，路由层只需串联流程。

::: warning redirect_uri 白名单
`/start` 接口需校验 `redirect_uri` 白名单（必须以指定回调路径结尾），防止授权码被导向外部站点。
:::

---

## 完成效果

新提供方即可复用通用 state 管理、授权 URL 构建、令牌交换与刷新流程，路由层只需实现特有逻辑（账号落地、用户信息补全），无需重复造轮子。
