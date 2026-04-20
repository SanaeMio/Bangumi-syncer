---
title: 🔗 自定义 Webhook
order: 10
---

# 🔗 自定义 Webhook

1. 运行 Bangumi-syncer。
2. 在观看平台完成播放后，构建以下 JSON 格式的 Webhook：

```json
{
  "media_type": "episode",
  "title": "中文名",
  "ori_title": "原名（取不到就给空）",
  "season": 1,
  "episode": 1,
  "release_date": "YYYY-MM-DD",
  "user_name": "用户名（同步发起方的用户名）",
  "source": "custom（根据实际情况定义一个来源名称）"
}
```

**电影/剧场版动画**：将 `media_type` 设为 `"movie"`，季、集填 `1`。

示例：

```json
{
  "media_type": "episode",
  "title": "我心里危险的东西",
  "ori_title": "僕の心のヤバイやつ",
  "season": 2,
  "episode": 12,
  "release_date": "2023-04-01",
  "user_name": "SanaeMio",
  "source": "custom（根据实际情况定义一个来源名称）"
}
```

3. 将以上 JSON 发送到 `http://{ip}:8000/Custom`，`ip` 根据本机情况填写。

4. 播放完成后，可在 Web 界面「日志管理」页面查看同步结果。
