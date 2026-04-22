---
title: 🎬 Jellyfin Webhook 插件
order: 14
---

# 🎬 Jellyfin Webhook 插件

1. 运行 Bangumi-syncer。

2. 打开 Jellyfin 控制台 → `插件` → `目录` → 拉到最下面找到点进 `Webhook` → 选择 `18.0.0.0` 版本，点击 `Install` 安装此插件然后 **重启服务器**。

![](/images/usage/jellyfin/step-01.jpg)

3. 打开 Jellyfin 控制台 → `插件` → `我的插件` → 点进 `Webhook`。`Server Url` 里输入你的 Jellyfin 地址，点击 `Add Generic Destination`。

![](/images/usage/jellyfin/step-02.jpg)

4. 展开下方的 `Generic`，`Webhook Name` 随便填，`Webhook Url` 输入 `http://{ip}:8000/Jellyfin`，`ip` 根据本机情况填写。`Notification Type` 只选中  `Playback Start`和`Playback Stop`，`Item Type` 选中 `Movies`和`Episodes`。`Template` 填写如下模版，然后点击 `Save` 保存设置：

```json
{"media_type": "{{{ItemType}}}","title": "{{{SeriesName}}}","ori_title": " ","season": {{{SeasonNumber}}},"episode": {{{EpisodeNumber}}},"release_date": "{{{Year}}}-01-01","user_name": "{{{NotificationUsername}}}","NotificationType": "{{{NotificationType}}}","PlayedToCompletion": "{{{PlayedToCompletion}}}", "source": "jellyfin"}
```

5. 在 Jellyfin 播放完成后，可在 Web 界面「日志管理」页面查看同步结果。
