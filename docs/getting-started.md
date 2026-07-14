---
title: ⚡ 快速上手
order: 3
---

# ⚡ 快速上手

用最少的步骤跑通「看完一集 → 自动打格子」的完整流程。

## 第 1 步：部署运行

选择一种方式启动 Bangumi-syncer，详见 [快速开始](/quick-start/)。

- **Docker**（推荐）：一行 `docker-compose` 拉起，见 [Docker 部署](/quick-start/docker)。
- **Windows**：下载 zip 包，双击 `start.bat`，见 [Windows 部署](/quick-start/windows)。
- **群晖 NAS**：通过 Container Manager 部署，见 [群晖部署](/quick-start/synology)。

启动后浏览器访问 `http://主机IP:8000`，首次登录账号密码均为 `admin`，请立即修改。

## 第 2 步：填写 Bangumi 账号

进入 **「配置管理」** → **同步配置**，选择「单用户」模式，然后在 **Bangumi 账号配置** 中填写：

- **用户名**：Bangumi 个人主页 `@` 后面的数字
- **访问令牌**：[点击生成](https://next.bgm.tv/demo/access-token)
- **媒体服务器用户名**：你在 Plex / Emby / Jellyfin 等媒体库里的登录名

::: warning 媒体服务器用户名是必须的
媒体服务器用户名是同步与否的关键依据，请不要忘记设置，这关系到是否会触发同步动作。
fongmi 的用户名比较特殊，为设备名，在日志里可以看到相关信息。
:::

完整字段说明见 [配置说明](/configuration)。

## 第 3 步：接入媒体源

选择你使用的播放端，按对应文档配置 Webhook：

| 播放端 | 文档 | Webhook 地址 |
|--------|------|-------------|
| Plex（Tautulli） | [文档](/usage/tautulli) | `http://主机IP:8000/Custom` |
| Plex Pass 原生 | [文档](/usage/plex-webhooks) | `http://主机IP:8000/Plex` |
| Emby | [文档](/usage/emby) | `http://主机IP:8000/Emby` |
| Jellyfin | [文档](/usage/jellyfin) | `http://主机IP:8000/Jellyfin` |
| 通用 Webhook | [文档](/usage/custom-webhook) | `http://主机IP:8000/Custom` |


| 同步源 | 文档 |
|--------|------|
| Trakt 同步 | [文档](/usage/trakt) |
| 飞牛同步 | [文档](/usage/feiniu) |
| fongmi | [文档](/usage/fongmi) |

更多媒体源移步 [接入使用](/usage/)。

## 第 4 步：验证同步

1. 在媒体库中播放完成一集番剧。
2. 打开 Bangumi-syncer 的 **「日志管理」** 页面查看同步记录。
3. 前往 [Bangumi](https://bgm.tv/) 个人主页确认观看进度已更新。

::: tip 标题对不上怎么办
如果媒体库的番剧名称和 Bangumi 条目对不上，可在 **「映射管理」** 手动指定「标题 → Bangumi 条目 ID」，详见 [自定义映射](/mapping)。
:::
