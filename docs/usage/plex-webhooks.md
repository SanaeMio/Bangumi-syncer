---
title: 📡 Plex Webhooks
order: 12
---

# 📡 Plex Webhooks

**（默认您的账号已拥有 Plex Pass，以下内容只需要设置一次）**

1. 运行 Bangumi-syncer。

2. 打开 Plex 控制面板，右上角 `设置` → `Webhooks` → `添加 Webhook`。

![](/images/usage/plex-webhooks/step-01.jpg)

3. 填写网址为 `http://{ip}:8000/Plex`，`ip` 根据本机情况填写，点击 `保存修改`。

4. 在 Plex 播放完成后，可在 Web 界面「日志管理」页面查看同步结果。
