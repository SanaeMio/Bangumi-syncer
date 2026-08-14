---
title: ⏱️ Trakt.tv 定时同步
order: 15
---

# ⏱️ Trakt.tv 定时同步

通过定时任务从 Trakt.tv 获取观看历史并同步到 Bangumi。

## 1. 准备工作

- 确保已安装最新版本的 Bangumi-syncer（支持 Trakt 功能）
- 拥有 Trakt.tv 账号（[注册 Trakt](https://trakt.tv/)）

## 2. Trakt 应用配置

- 访问 [Trakt API 应用页面](https://trakt.tv/oauth/applications)
- 点击「新建应用」创建 OAuth 应用
- 填写应用信息：
  - **Name**：Bangumi-syncer（或自定义名称）
  - **Redirect uri**：`http://localhost:8000/api/trakt/auth/callback`（`localhost` 需替换为 Bangumi-syncer 实际的 IP + 端口）
  - 其他字段可选填
- 创建后获取 **Client ID** 和 **Client Secret**

## 3. Bangumi-syncer 配置

- 访问 Web 管理界面（`http://localhost:8000`）
- 登录后进入「Trakt 配置」页面（左侧菜单）
- 填写第 2 步获取的 Trakt 的 Client ID 和 Client Secret
- **Redirect uri** 与第 2 步保持一致
- 在「连接状态」区域点击「授权 Trakt」按钮
- 在弹出的窗口中点击「开始授权」，系统将打开 Trakt 授权页面
- 在 Trakt 页面授权应用访问您的观看历史
- 授权成功后返回配置页面

::: tip 授权成功后会自动补充用户名
授权成功后，系统会自动把你登录本程序的用户名追加到**当前激活的 Bangumi 账号**的「媒体服务器用户名」列表里（如果尚未存在），并在成功页弹出提示。这是为了让 Trakt 同步的观看记录能正确路由到对应的 Bangumi 账号，避免被用户名过滤拦截。如果你不希望如此，可在「Bangumi 账号配置」里手动移除该用户名。
:::

## Bearer 凭证模式（备选，无需创建 Trakt 应用）

不想创建 Trakt 应用 / 无法配置回调地址时，可改用 **Bearer 凭证模式**：
直接提供 Trakt 账号的 refresh_token，数据走官方数据域
`apiz.trakt.tv`，无需 Client ID / Secret / 回调地址。

### 方式一：通过邮箱一键登录（推荐）

1. 进入「Trakt 配置」页面，在顶部「凭证模式」卡片选择 **Bearer Token**
2. 在「Bearer 凭证」卡片点击 **通过邮箱登录**
3. 弹窗中输入 **Trakt 账号邮箱** 并点击「发送验证码」
4. 到邮箱查收 **6 位验证码**（5 分钟内有效），输入后点击「登录并保存」
5. 登录成功会自动获取并保存 Access Token / Refresh Token，并切换为
   Bearer 模式；token 全程在服务端流转，不会回显到页面

::: tip 自动补充用户名
邮箱登录成功后，同样会自动把你登录本程序的用户名追加到激活的 Bangumi
账号的「媒体服务器用户名」列表（与 API 应用模式授权成功行为一致），确保
Trakt 同步记录能正确路由。
:::

### 方式二：从浏览器手动粘贴

1. 用浏览器登录 [app.trakt.tv](https://app.trakt.tv)
2. 按 F12 打开开发者工具 → Application → Local Storage
3. 展开 `https://auth.trakt.tv`，找到 `oidc.user:https://auth.trakt.tv:201dc70c...`
   键，复制其中的 `refresh_token`
4. 在「Bearer 凭证」卡片中粘贴并保存（只需 refresh_token，保存时后端会自动换取新的 access/refresh）

::: tip 凭证说明
- 只需提供 **refresh_token**：保存时后端会立即验证并旋转刷新，自动换取新的
  access_token / refresh_token；refresh_token 为旋转式，旧值立即作废
  （保存后你刚从浏览器复制的那份 refresh_token 不再可用）
- 已配置时输入框留空表示不修改；token 绝不回显
- 凭证剩余不足 1 天时，每日定时任务会自动续期，无需手动刷新
- 同一邮箱 60 秒内重复发信会被限流（429，前端自动倒计时）；验证码连续输错
  5 次会话作废，需重新发送
:::

::: warning 凭证模式切换
API 应用与 Bearer 两种模式数据域不同（api.trakt.tv / apiz.trakt.tv），
**切换必须提供对应模式的新凭证，不能复用另一模式的 token**：

- 切换至 **Bearer**：需填写 refresh_token（或用「通过邮箱登录」）后保存
- 切换至 **API 应用**：需重新点击「授权 Trakt」完成 OAuth 授权

仅切换界面上的 radio 而未保存不会改变已保存的模式。
:::

## 4. 同步配置

- **启用同步**：开启定时同步功能
- **同步间隔**：设置 Cron 表达式（如 `0 */6 * * *` 表示每 6 小时）
- **同步数据类型**：支持「观看历史」中的**剧集**与**电影**（剧场版动画）。
- **同步数据过滤**：仅同步动画条目（基于 Trakt 分类 anime / donghua / animation），过滤真人剧类型，自定义映射的条目不受此限制。
- 点击「保存配置」应用设置

## 5. 手动同步测试

- 在「同步控制」区域点击「手动同步」进行测试
- 首次同步建议选择「全量同步」获取全部历史记录
- 后续定时任务将自动执行「增量同步」
- 可在「同步历史」表格查看同步结果

## 6. 定时任务管理

- 调度器将在设定的时间间隔自动执行同步
- 支持多用户独立配置和同步
- 可在「同步控制」区域查看下次同步时间
- 支持随时手动触发同步或全量同步

## Trakt 严格校验回调地址的解决方案

Trakt 要求回调地址必须是 HTTPS，仅对 loopback 地址（`127.0.0.1` / `localhost`）允许 HTTP。如果你的部署地址不是 localhost，在授权时会遇到以下报错：

```
Redirect URI must use HTTPS (HTTP allowed only for loopback hosts)
```

这是因为 Trakt 要求回调地址必须是 HTTPS，仅对 loopback 地址（`127.0.0.1` / `localhost`）允许 HTTP。可以使用 SSH 端口转发，让 Trakt 以为回调发生在本地，完成一次性授权（授权后即可长期使用，无需保持转发）：

1. **修改回调地址**：在 [Trakt 开发者后台](https://app.trakt.tv/settings/apps/api?mode=media) 和 Bangumi-syncer 的 Trakt 配置页面中，将回调地址均填为 `http://127.0.0.1:8000/api/trakt/auth/callback`
2. **建立 SSH 转发**：在电脑终端中执行以下命令（将 `用户名` 和 `NAS的局域网IP` 替换为实际值，`8000` 替换为 Bangumi-syncer 实际监听的端口）：
   ```bash
   ssh -L 8000:127.0.0.1:8000 用户名@NAS的局域网IP
   ```
   保持该终端窗口不要关闭
3. **完成授权**：在电脑浏览器中打开 Bangumi-syncer 的 Trakt 配置页面，点击授权按钮，跳转至 Trakt 同意授权后，Trakt 会将浏览器重定向到 `http://127.0.0.1:8000/api/trakt/auth/callback?...`，通过 SSH 转发到达 NAS，授权成功
4. **关闭终端**：授权完成后关闭终端即可，后续应用使用已获取的 Token 正常运行，无需保持转发

## 注意事项

- 首次全量同步可能需要较长时间（取决于历史记录数量）
- 系统会自动处理重复记录，避免重复同步
- Token 过期时会自动刷新，无需手动重新授权
- 支持剧集（Episode）与电影（Movie）两种观看历史；电影按剧场版动画处理
- 增量同步基于最后同步时间，只获取新记录
