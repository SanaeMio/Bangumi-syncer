---
title: 🔌 MCP 接入
order: 40
---

# 🔌 MCP 接入

Bangumi-syncer（BS）内置 MCP 服务，AI 助手可以通过它直接读取运行日志、查看与修改配置，无需打开 Web 管理页。

::: tip 能做什么
- 查看、搜索运行日志，定位同步失败原因
- 查看当前配置（Token、密码等敏感字段自动脱敏）
- 修改配置项并即时生效（如调整同步阈值、开关媒体源）
:::

## 接入：让 AI 助手自己完成

把下面这段话粘贴给你的 AI 助手（需支持技能 / Skill 机制）：

```
请从 GitHub 仓库 SanaeMio/Bangumi-syncer 的 skills/bangumi-syncer/ 目录安装 bangumi-syncer 技能：
下载 SKILL.md 到你自己平台的技能目录（个人级或项目级），安装后加载该技能，
并按它的引导帮我接入和配置 Bangumi-syncer。
```

安装后，助手会主动确认你的部署状态、BS 地址与目标，并逐步引导完成：部署检查 → Bangumi 授权 → MCP 接入 → 媒体源配置 / 排障。

::: tip 没有技能机制的助手
也可以手动接入：将 `http://<你的 BS 地址>:8000/mcp` 注册为远程 MCP 服务（Streamable HTTP）。OAuth 会自动发现，无需静态 Token。
:::

## 授权与权限

- 首次连接会进入授权页（consent）：若尚未登录 BS，会自动跳转到登录页，登录成功后自动回到授权页，确认后发放 Access Token + Refresh Token（自动续期）
- **默认只发放只读权限**；需要 AI 修改配置时，应授予读写权限（授权页会展示本次授予的范围）。写权限建立在只读之上：`/mcp` 端点要求 token 含 `read`，**不能只授予 write**，否则连接会被 403 拒绝
- 停止授权 / 切换账号：在 AI 助手中清除该服务的认证后重新连接
- BS 重启会清空授权状态（客户端需重新授权）；已签发的 Access Token 在有效期内仍可用

## 常见客户端接入

BS 内置的 MCP 服务随主进程启动（同一进程，并非独立 sidecar），其地址就是 BS 自身地址。不同客户端走不同的 OAuth 客户端机制，接入前请确认 `MCP_BASE_URL` 与客户端实际访问地址一致（反向代理 / 端口映射场景尤其注意）。

| 客户端 | 客户端机制 | 需要准备 | 典型配置 |
| --- | --- | --- | --- |
| Claude Code | CIMD（Client ID Metadata Document） | BS 可达地址；**BS 能出网访问 `https://claude.ai`** | `claude mcp add --transport http bangumi-syncer http://<你的 BS 地址>:8000/mcp` |
| OpenCode | DCR（Dynamic Client Registration） | BS 可达地址（**无需 BS 出网**） | `opencode.json` 中声明 `type: remote` 服务 |

机制差异一句话：**CIMD 无需预注册**——`client_id` 就是一个 HTTPS URL，授权时由 BS 实时抓取并校验文档；**DCR 由客户端先动态注册**（`POST /register`）再授权。两者都只是「注册/识别客户端」，**不授予数据权限**，仍需 BS 登录 + consent 页 Allow。

### Claude Code（走 CIMD）

1. 登记 MCP 服务（默认仅当前项目，加 `--scope user` 可全局）：
   ```bash
   claude mcp add --transport http bangumi-syncer http://<你的 BS 地址>:8000/mcp
   ```
2. 触发授权，在浏览器完成 BS 登录 + consent 页允许：
   ```bash
   claude mcp login bangumi-syncer
   # 或在交互式 claude 中执行 /mcp → 选中服务 → Authenticate
   ```
3. 验证：`claude mcp list` 应显示 `✔ Connected`。

要点：

- Claude Code 为公开客户端 + PKCE（无 client_secret），全程**不应出现 `POST /register`**（这是与 DCR 路径的关键区别）
- BS 需能出网抓取 CIMD 文档；若所在网络把 `claude.ai` 解析到 fake-ip / 保留网段，会被 SSRF 防护拒绝，需改用真实 DNS 或为该域名配置 hosts
- 需要写权限（`update_config`）时，客户端需在授权请求中显式请求 `read write`；仅只读无需额外配置

### OpenCode（走 DCR）

在 `opencode.json`（v1.x 格式）中声明远程 MCP 服务：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "bangumi-syncer": {
      "type": "remote",
      "url": "http://<你的 BS 地址>:8000/mcp",
      "oauth": { "scope": "read write" }
    }
  }
}
```

- `oauth` 为对象或 `false`，**不能写 `"oauth": true`**（无效值）；省略即自动 OAuth
- 仅只读时可省略 `scope`（服务端按安全默认只发 `read`）；需要 `update_config` 时必须包含 `read write`
- `type: remote` 时 OpenCode 自动完成「发现 AS → DCR 注册 → PKCE → 授权」，无需手工填写 client 凭据
- 触发与验证：
  ```bash
  opencode mcp auth bangumi-syncer   # 浏览器完成 BS 登录 + Allow
  opencode mcp list                  # 期望 ✓ connected (OAuth)
  ```
- v2 配置结构不同：为 `"mcp": { "servers": { "<name>": { ... } } }`，OAuth 字段用 snake_case（`client_id` / `client_secret` / `scope` / `callback_port`）
- 注册表与 Refresh Token 为进程内存态：BS 重启后需重新 `opencode mcp auth`（已签发的 Access Token 在 1 小时有效期内仍可用）

## 环境变量（部署相关）

MCP 服务随 BS 主进程启动，下面几个变量通过**启动 BS 的进程环境**传入（Docker 用 `-e` / `environment`，裸机用 shell 环境变量或 `.env`）。它们决定客户端能否正确发现并授权，反向代理、局域网、域名访问场景请务必检查。

| 变量 | 默认值 | 不设的后果 |
| --- | --- | --- |
| `MCP_BASE_URL` | `http://localhost:8000` | 授权页返回的 issuer、metadata、authorize / token 端点地址全部基于它生成。反代、局域网、域名访问时客户端会拿到 `localhost` 地址，OAuth 发现失败、连接被拒 |
| `MCP_RSA_PRIVATE_KEY` | `data/mcp_private.pem`（Docker 下即 `/app/data/mcp_private.pem`） | 密钥默认落项目数据目录 `data/`，容器重建不会丢失；**若未挂载该目录**，重建后仍会重新生成密钥对，此前签发的 Access Token 全部验签失败，客户端被迫重新授权。建议挂载 `data/` 以持久化密钥 |
| `MCP_RSA_PUBLIC_KEY` | `data/mcp_public.pem`（Docker 下即 `/app/data/mcp_public.pem`） | 同上，与私钥成对使用；建议把 `data/` 目录挂载到持久卷 |
| `MCP_REFRESH_TOKEN_TTL` | `2592000`（30 天） | Refresh Token 有效期，单位秒；每次轮换后重新计时（滑动窗口）。不设即使用 30 天默认值 |

::: tip MCP_BASE_URL 的取值优先级
`MCP_BASE_URL` 环境变量 > 配置项 `[dev] mcp_base_url` > 默认值 `http://localhost:8000`。

不想改环境变量时，也可以在 Web 管理页的「开发与代理」段填写 `mcp_base_url`，效果相同（环境变量优先级更高，两者都设时以环境变量为准）。

该配置与 `auth.enabled` 均在进程启动时读取一次，通过 Web 管理页修改 `mcp_base_url`（或 `auth.enabled`）后**需重启 BS 才生效**。
:::

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 授权页跳到 BS 登录页 | 正常流程：登录后会自动回到授权页，确认授权即可，无需重新触发 |
| 提示「权限不足」 | 当前授权不含写权限：清除认证后重新授权，并确保本次授权包含读写范围 |
| 返回 403 `insufficient_scope` | token 缺少 `read` scope（例如客户端只申请了 `write`）：重新授权并包含只读范围 |
| 连接失败 | 确认地址与端口（默认 8000）可达；反向代理场景需保证服务公开地址与实际访问地址一致（见 [开发文档](/development/mcp)） |
| 出现陌生客户端 | 重启 BS 即可清空全部已注册客户端（授权状态为进程内存态） |

## 安全建议

- 不要将 BS 端口直接暴露到公网；公网访问请走 VPN 或反向代理 + TLS
- AI 助手无法修改 `auth` 段；配置读取时敏感字段自动脱敏
- 更多技术细节（OAuth 流程、Scope 机制、单进程约束、RSA 密钥、环境变量）见 [开发文档：MCP Server](/development/mcp)
