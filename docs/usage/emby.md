---
title: 📺 Emby 通知
order: 13
---

# 📺 Emby 通知

1. 运行 Bangumi-syncer。

2. 打开 Emby 控制面板 → `应用程序设置` → `通知` → `添加通知` → 选择 `Webhooks`。

![](/images/usage/emby/step-01.jpg)

3. 名称随意填写，URL 填写 `http://{ip}:8000/Emby`，`ip` 根据本机情况填写，请求内容类型选择 `application/json`，Events 里勾选 `播放-开始` 、 `播放-停止` 和 `用户-标记为已播放`，`将媒体库事件限制为` 根据自己情况，建议只勾选包含动画的库，最后点击 `储存`。

4. 在 Emby 播放完成或手动标记为已播放后，可在 Web 界面「日志管理」页面查看同步结果。
