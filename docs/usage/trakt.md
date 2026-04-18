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

## 4. 同步配置

- **启用同步**：开启定时同步功能
- **同步间隔**：设置 Cron 表达式（如 `0 */6 * * *` 表示每 6 小时）
- **同步数据类型**：目前支持「观看历史（剧集）」
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

## 注意事项

- 首次全量同步可能需要较长时间（取决于历史记录数量）
- 系统会自动处理重复记录，避免重复同步
- Token 过期时会自动刷新，无需手动重新授权
- 目前仅支持剧集（Episode）类型的观看历史，电影记录会被跳过
- 增量同步基于最后同步时间，只获取新记录
