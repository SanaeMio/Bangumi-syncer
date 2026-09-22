---
title: 🔌 MCP Server（内置模块）
order: 13
---

# 🔌 MCP Server（内置模块）

MCP Server 是 Bangumi-syncer 的**内置模块**，通过 MCP 协议（Model Context Protocol）让 LLM 客户端读写 BS 配置、查询日志。它随 BS 单进程启动，**不是独立的 workspace 成员**，没有独立 `pyproject.toml` / `uv.lock`；依赖（如 `fastmcp`）统一声明在根 `pyproject.toml`。

## 技术栈

- **Python**：`>=3.10`（与 BS 主项目一致，全仓已升级至 3.10）
- **MCP SDK**：[`fastmcp>=4.0.3`](https://github.com/jlowin/fastmcp)（Streamable HTTP 传输，见根 `pyproject.toml`）
- **认证**：`cryptography`（RSA 密钥对）+ `PyJWT`（RS256 JWT 签发）
- **测试**：`pytest` + `pytest-asyncio`（嵌入用 `TestClient` 做端点行为验证）

---

## 项目结构

```
app/mcp/
├── __init__.py
├── server.py               # FastMCP 服务工厂 + 工具注册
├── provider.py             # OAuth AS（authorize/token/register）+ CIMD
├── keys.py                 # RSA 密钥管理（生成/加载/签名/验签）
├── consent.py              # consent 授权页渲染与处理
└── tools.py                # 工具实现（get_logs / get_current_config / update_config）

tests/
├── test_mcp_tools.py       # 工具函数测试
├── test_mcp_auth.py        # OAuth 流程测试
├── test_mcp_cimd_real.py   # CIMD（Client ID Metadata Document）流程测试
├── test_mcp_embed.py       # FastMCP 嵌入 FastAPI 测试
├── test_mcp_integration.py # 端到端集成测试
├── test_mcp_assembly.py    # 装配唯一性与 provider 同一性断言
├── test_mcp_key_paths.py   # RSA 默认 data/ 路径、env 覆盖、父目录创建 + 0600
└── test_main_mcp.py        # main.py MCP 集成测试
```

---

## 架构概览

```
┌─────────────────┐     MCP (Streamable HTTP)     ┌─────────────────────────────┐
│   AI 助手        │ ◄────────────────────────────► │  Bangumi-syncer (BS)        │
│                 │    OAuth 2.1 + RS256 JWT      │  (port 8000)                │
└─────────────────┘                                │                             │
                                                   │  内置 FastMCP 服务           │
                                                   │  - app/mcp/server.py        │
                                                   │  - app/mcp/provider.py      │
                                                   │  - app/mcp/tools.py         │
                                                   │  工具直接调用同进程业务层      │
                                                   └─────────────────────────────┘
```

职责分布：

| 路径 | 职责 |
| --- | --- |
| `app/mcp/server.py` | FastMCP 服务工厂 + 工具注册 |
| `app/mcp/provider.py` | OAuth AS（authorize/token/register）+ RSA 密钥管理 + CIMD |
| `app/mcp/tools.py` | 工具实现（get_logs / get_current_config / update_config） |
| `app/main.py` | FastAPI 应用入口，`app.mount("/", mcp_app)` 挂载 FastMCP 子应用 + `combine_lifespans` |

### 挂载方式：独立 ASGI 子应用（mount，而非 include_router）

MCP 与其它 API 的装配方式不同，这是 ASGI 嵌套语义决定的，不是代码设计缺陷：

| 维度 | 其它 API（`app.include_router(...)`） | MCP（`app.mount("/", mcp_app)`） |
| --- | --- | --- |
| 本质 | 同一 app 内的**路由集合** | `mcp.http_app()` 产出的**独立 ASGI 子应用** |
| lifespan | 天然共享外层 lifespan | 外层**不会**代跑子应用 lifespan，须 `combine_lifespans(lifespan, mcp_app.lifespan)`（`app/main.py`） |
| 异常处理 | 共享外层 exception handler | 子应用 `Router.not_found` 在 `"app" in scope` 时自行 raise `HTTPException(404)`，由子应用自身中间件处理，外层 404 处理器拦不到；须 `mcp_app.add_exception_handler(404, _mcp_json_404)` 注册 JSON 404 |
| middleware | 共享外层中间件栈 | 子应用有自己的中间件栈（`Mount` 自动生效的 `user_middleware` 除外） |

挂载点必须是放在所有具体路由之后的 catch-all（`/`），以保持 MCP 的原始根路径端点（`/.well-known/*`、`/authorize`、`/token`、`/consent`、`/mcp`）不被前缀改写。

::: warning 状态均为进程内存储，当前仅支持单进程
`BangumiOAuthProvider`（`app/mcp/provider.py`）的客户端注册表、授权码、Refresh Token、待授权请求与吊销记录全部保存在**进程内存**中。仓库启动方式（`start.bat`、Dockerfile `CMD`）均为 `uvicorn app.main:app` 单进程，**不支持 `--workers N` / gunicorn 多进程**：否则授权码 / Refresh Token 会因请求落到不同进程而失效，吊销状态也会各进程不一致。Access Token 的 JWT 验签本身无状态（RS256），不受进程数影响。
:::

各状态表的 TTL 与清理策略（均为进程内存态，由 `_cleanup_expired_state()` **惰性清理**，调用时机为 `authorize` / `get_consent_context` / `exchange_authorization_code` / `exchange_refresh_token` / `revoke_token`）：

| 状态表 | 键 | TTL | 说明 |
| --- | --- | --- | --- |
| `_pending_auths` | `request_token` | 10 分钟（`PENDING_AUTH_TTL`） | 待 consent 的授权请求；过期即删除 |
| `_auth_codes` | 授权码 | 5 分钟（`AUTH_CODE_TTL`） | 已兑换/过期的授权码被清除；SDK 兑换时另行校验过期 |
| `_refresh_tokens` | Refresh Token | 30 天（`REFRESH_TOKEN_TTL`，环境变量 `MCP_REFRESH_TOKEN_TTL` 可配） | 每次轮换**重新计时**（滑动窗口）；`expires_at=None` 视为不过期 |
| `_revoked_tokens` | `jti` → access token `exp` | 随 access token 到期 | 吊销记录在对应 access token 过期后清理，避免只增不减 |

---

## 认证链路

### 1. OAuth Authorization Server（BS 侧）

`app/mcp/provider.py` 的 `BangumiOAuthProvider`（继承 FastMCP 4 的 `OAuthProvider`）实现 OAuth 授权服务：

| 端点 | 功能 |
| --- | --- |
| `/authorize` | 发起授权请求，重定向到 `/consent` |
| `/token` | 用 authorization code 换 JWT access_token |
| `/register` | 动态客户端注册（RFC 7591） |
| `/revoke` | 吊销 access / refresh token（`RevocationOptions(enabled=True)`） |
| `/consent` | 用户确认页面（allow/deny）；未登录时 302 到 `/login`（登录后回跳） |

JWT claims：

```python
{
    "sub": "admin",  # 用户名
    "scope": "read write",  # 权限范围
    "iss": "http://localhost:8000",  # = 解析后的 base_url（不可单独配置）
    "aud": "bangumi-syncer",  # 硬编码
    "iat": 1700000000,
    "exp": 1700003600,  # 硬编码 3600 秒
    "jti": "...",  # 唯一标识（sign_jwt 自动添加）
}
```

::: tip 默认 scope 为 read
上面的 `"scope": "read write"` 仅为示例。实际实现中 `_default_scopes = ["read"]`（`provider.py`）：客户端**不请求 scope** 时只发放 `read`（authorize 的 `params.scopes or self._default_scopes`、DCR 注册时未声明 scope 也回填 `read`、CIMD 的 `default_scope` 同为 `read`）。`write` 需客户端在授权请求中**显式请求**，且 SDK（`OAuthClientInformationFull.validate_scope`）会校验请求的 scope 属于客户端已注册/声明的 scope，否则返回 `invalid_scope`。

权限对照：`read` → `get_logs` / `get_current_config`（敏感字段已掩码）；`write` → `update_config`（`auth` 段始终禁写）。

`BangumiOAuthProvider` 默认另声明 `required_scopes=["read"]`（`provider.py`），它是 `/mcp` 的**传输层准入底线**：FastMCP `RequireAuthMiddleware` 据此校验 access token 的 scope，**仅含 `write` 的 token 会被 403 `insufficient_scope` 拒绝**。因此 `write` 建立在 `read` 之上，不存在只含 `write` 的可用 token；工具侧 `_require_read_scope` 与 `update_config` 的 write 校验保留为 defense-in-depth。
:::

### 2. auth.enabled 分流

认证分流由 **BS 侧** `auth.enabled` 配置决定，`handle_consent` 在**同进程内**通过 `security_manager.validate_session()` 校验 BS 会话，不走 HTTP：

| 模式 | 行为 |
| --- | --- |
| BS `auth.enabled=true` | consent 时读取请求 Cookie 中的 `session_token`，调用 `security_manager.validate_session()` 复用 BS 会话，身份为 BS 当前用户 |
| BS `auth.enabled=false` | 不校验会话，直接使用 `auth_username`（来自 BS 认证配置），无需登录 |

### 3. JWT 验签（FastMCP provider 侧）

`app/mcp/provider.py` 的 `RSAKeyManager.verify_jwt()` 用公钥验证 RS256 签名：

1. 检查 `exp`（过期时间）
2. 检查 `aud`（必须为 `"bangumi-syncer"`）
3. 公钥验签（RS256）

公钥由 `RSAKeyManager` 本地生成/加载，默认路径为 `data/mcp_public.pem`（相对 cwd），可通过 `MCP_RSA_PUBLIC_KEY` 环境变量覆盖。

### 4. consent 未登录行为

`auth.enabled=true` 且请求未携带有效 `session_token` 时，`handle_consent`（GET 与 POST allow）返回 **302**，`Location` 为 `/login?next=<urlencode(/consent?request_token=...)>`；其中 `next` 为**不含 base_path** 的站内路径，整体 urlencode，登录成功后由前端 `static/js/auth.js` 校验并自动回跳 consent 页继续授权，**无需手动重新触发**。这是复用 BS 页面级登录重定向约定（`app/api/pages.py::_login_redirect`）。

### 5. 动态客户端注册（DCR）

`BangumiOAuthProvider` 默认传入 `ClientRegistrationOptions(enabled=True, valid_scopes=["read", "write"])`（`provider.py`），因此 `/register`（RFC 7591）默认开启且未认证可达（子应用在 `app/main.py` 中通过 `app.mount("/", mcp_app)` 挂载）。同一处 provider 还默认声明 `required_scopes=["read"]` 作为 `/mcp` 准入底线——它与 `valid_scopes`（scope 允许集）、`_default_scopes`（发放默认值）是三个独立概念，不要混用。

- **注册 ≠ 授权**：注册只登记客户端元数据，不授予任何数据权限；仍需 BS 登录会话（`auth.enabled=true` 时）+ consent 页点 Allow 才能拿到 Token
- **存储与上限**：已注册客户端存于进程内存 `_clients`，上限 `MAX_CLIENTS=1000`，达到上限后注册抛 `RegistrationError`
- **兜底**：重启进程即清空 `_clients`（及 `_auth_codes` / `_refresh_tokens` / `_pending_auths` / `_revoked_tokens`）；RSA 密钥若已持久化则保留。已签发 Access Token 在 1 小时有效期内仍有效（JWT 自包含验签，不查注册表）
- **关闭方式**：当前无配置开关。DCR 选项的实际来源是 `app/mcp/provider.py::BangumiOAuthProvider.__init__` 的默认值（`client_registration_options or ClientRegistrationOptions(enabled=True, valid_scopes=["read", "write"])`）；装配侧 `app/mcp/server.py::_create_provider` 目前不显式传入该选项，因此以 provider 默认值为准。要关闭需改该默认值（或在构造 provider 时显式传 `ClientRegistrationOptions(enabled=False)`）。关闭后仅 CIMD 客户端可授权（`get_client` / `authorize` 对 CIMD client_id 走独立分支，不依赖 `_clients`）

---

## 密钥管理

### RSA 密钥对生成

`RSAKeyManager` 在 BS 启动时调用 `load_or_generate()`：

- 若磁盘已有密钥对 → 加载
- 若不存在 → 生成 2048 位 RSA 密钥对并写入磁盘

### 密钥路径

| 密钥 | 路径（默认） | 说明 |
| --- | --- | --- |
| 私钥 | `data/mcp_private.pem` | **仅 BS 持有**，本地磁盘（相对 cwd） |
| 公钥 | `data/mcp_public.pem` | 本地生成，用于验签 JWT（相对 cwd） |

默认路径相对进程启动时的工作目录（cwd）解析，位于项目 `data/` 目录；Docker 镜像内即 `/app/data`（Dockerfile 已预建该目录）。路径可通过环境变量 `MCP_RSA_PRIVATE_KEY` / `MCP_RSA_PUBLIC_KEY` 配置。

::: warning 建议挂载 `data/` 以持久化密钥
密钥默认写入项目 `data/` 目录，但**容器未挂载该目录时**，容器重建仍会导致密钥丢失：已签发的 Access Token（有效期 1 小时）将验签失败，客户端需重新授权。生产部署应将宿主机目录挂载到容器 `/app/data`（或把 `MCP_RSA_PRIVATE_KEY` / `MCP_RSA_PUBLIC_KEY` 指向持久卷）；多实例部署还需各实例共享同一密钥。
:::

---

## 工具实现

工具函数位于 `app/mcp/tools.py`，直接调用 BS 同进程业务层（不经 HTTP）。所有响应包络格式：

```json
{"status": "success", "data": {...}}
```

### get_logs

获取日志内容。

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `level` | string? | 日志级别：DEBUG / INFO / WARNING / ERROR（WARN 自动映射为 WARNING） |
| `search` | string? | 关键字搜索 |
| `limit` | int? | 返回行数（1-10000，默认 50） |
| `since` | string? | 起始时间（ISO 格式） |
| `until` | string? | 结束时间（ISO 格式） |

### get_current_config

获取全量配置（敏感字段已脱敏为 `***`）。无参数。

### update_config

修改配置（需要 `write` scope）。

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `section` | string | 配置段名（支持下划线，自动归一化为连字符） |
| `key` | string | 配置键名 |
| `value` | any | 配置值 |

段名下划线自动归一化为连字符（`notify_webhook` → `notify-webhook`）。`auth` 段禁止通过 MCP 修改。

---

## 测试方式

### 工具函数测试

`tests/test_mcp_tools.py` 直接测试工具函数（不经 MCP server），用 mock/patch 隔离 config 写入。

### OAuth 测试

`tests/test_mcp_auth.py` 使用 `tests.mcp_helpers.build_test_mcp_app()`（复用生产入口 `create_mcp_app`）创建带认证的 Starlette app，直接测试 authorize/token/consent 流程。

### 嵌入测试

`tests/test_mcp_embed.py` 以 `TestClient` 行为断言验证 FastMCP 子应用正确挂载到 FastAPI app：根路径端点可达、未认证/无效 Bearer 返回 401、未匹配路径返回 JSON 404；另覆盖传输层 scope 准入——仅含 `write`（无 `read`）的合法 token 请求 `/mcp` → 403 `insufficient_scope`（携带 scope 挑战头，指引客户端补足 `read`）。

### 集成测试

`tests/test_mcp_integration.py` 端到端验证：
- `list_tools` → 3 工具且 schema 正确
- 未认证调工具 → 401
- 走完授权流程 → token → 调工具成功
- `/.well-known/oauth-authorization-server` 含 `client_id_metadata_document_supported: true`

---

## 本地开发

### 安装依赖

```bash
# 根目录
uv sync --group dev
```

### 运行 BS（含内置 MCP）

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 运行测试

```bash
# 全部测试
uv run pytest tests/

# 仅 MCP 相关
uv run pytest tests/test_mcp_*.py -v
```

### 环境限制

- **Python 版本**：`>=3.10`，CI 使用 3.10
- **UV_PYTHON**：若系统 Python < 3.10，需通过 `uv python install 3.10` 或设置 `UV_PYTHON` 环境变量指定解释器

---

## Docker 构建

```bash
docker build -t bangumi-syncer:latest .
```

BS 单容器部署，内置 MCP 服务。

密钥默认写入容器内 `/app/data`（Dockerfile 已预建该目录）。**建议挂载宿主机目录到 `/app/data`** 以持久化 RSA 密钥——未挂载时容器重建会丢失密钥，已签发的 Access Token 验签失败、客户端被迫重新授权。

环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MCP_RSA_PRIVATE_KEY` | `data/mcp_private.pem`（Docker 内 `/app/data/mcp_private.pem`） | RSA 私钥路径（相对 cwd；建议挂载 `data/` 持久化） |
| `MCP_RSA_PUBLIC_KEY` | `data/mcp_public.pem`（Docker 内 `/app/data/mcp_public.pem`） | RSA 公钥路径（相对 cwd；建议挂载 `data/` 持久化） |
| `MCP_BASE_URL` | `http://localhost:8000` | 服务公共 URL，用作 OAuth issuer / metadata 端点；解析优先级：`create_mcp_server(base_url=...)` 参数 > `MCP_BASE_URL` > `dev.mcp_base_url` 配置 > 默认值 |
| `MCP_REFRESH_TOKEN_TTL` | `2592000`（30 天） | Refresh Token 有效期，单位：秒；每次轮换后重新计时（滑动窗口） |

::: tip 环境变量与相关配置在启动时读取一次
`MCP_BASE_URL`、配置项 `dev.mcp_base_url` 与 `auth.enabled` 均在**进程启动时读取一次**：`mcp_app` 是模块导入时创建的单例（`app/mcp/server.py:176`），运行中修改不会热生效，需重启 BS。
:::

::: warning 不可配置项
以下值当前在 `app/mcp/server.py` 中硬编码，**没有对应环境变量**：

- OAuth `issuer` = 解析后的 `base_url`
- JWT `audience` = `"bangumi-syncer"`
- Access Token 有效期 = `3600` 秒
:::
