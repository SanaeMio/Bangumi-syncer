---
title: 📊 Plex（Tautulli）
order: 11
---

# 📊 Plex（Tautulli）

**（默认您已将 Plex 与 Tautulli 绑定完成，以下内容只需要设置一次）**

1. 运行 Bangumi-syncer。

2. 打开 Tautulli 控制面板，右上角 `Settings` → `Notification Agents` → `Add a new notification agent` → 选择 `Webhook`。

![](/images/usage/tautulli/step-01.jpg)

3. 在弹出页面的 `Configuration` 中的 `Webhook URL` 填写 `http://{ip}:8000/Custom`，`ip` 根据本机情况填写。

![](/images/usage/tautulli/step-02.jpg)

4. `Triggers` 勾选 `Watched`。

![](/images/usage/tautulli/step-03.jpg)

5. `Conditions` 建议填写，以减少 Webhook 请求次数。这里限制了用户名和单集的时候才会触发 Webhook。第一个条件是限制用户名，改成自己的。第二个条件是限制媒体类型为单集，写死为 `episode`。`Condition Logic` 填写为 `{1} and {2}`，表示两个条件同时满足时才触发。

![](/images/usage/tautulli/step-04.jpg)

6. `Data` 中展开 `Watched`，在 `JSON Data` 中填写如下通知模版，然后点击右下角 `Save` 保存设置：

```json
{"media_type": "{media_type}", "title": "{show_name}", "ori_title": " ", "season": "{season_num}", "episode": "{episode_num}", "release_date": "{air_date}", "user_name": "{username}", "source": "plex"}
```

![](/images/usage/tautulli/step-05.jpg)

7. 在 Plex 播放完成后，可在 Web 界面「日志管理」页面查看同步结果。

## ⚠️ v3.7.0版本后需额外操作
**请注意：v3.7.0版本前的用户升级后需继续按如下操作额外新增一个通知代理才能支持「剧场版打格子」功能，新用户上下两个都要加。**

1. 按照上面操作继续`Add a new notification agent`。

2. 在弹出页面的 `Configuration` 中的 `Webhook URL` 填写 `http://{ip}:8000/Custom`，`ip` 根据本机情况填写。

![](/images/usage/tautulli/step-02.jpg)

3. `Triggers` 勾选 `Watched`。

![](/images/usage/tautulli/step-03.jpg)

4. `Conditions` 建议填写，以减少 Webhook 请求次数。这里限制了用户名和单集的时候才会触发 Webhook。第一个条件是限制用户名，改成自己的。第二个条件是限制媒体类型为单集，写死为 `movie`。`Condition Logic` 填写为 `{1} and {2}`，表示两个条件同时满足时才触发。

![](/images/usage/tautulli/movie-step-04.jpg)

5. `Data` 中展开 `Watched`，在 `JSON Data` 中填写如下通知模版，然后点击右下角 `Save` 保存设置：

```json
{"media_type": "{media_type}", "title": "{title}", "ori_title": " ", "season": "1", "episode": "1", "release_date": "{air_date}", "user_name": "{username}", "source": "plex"}