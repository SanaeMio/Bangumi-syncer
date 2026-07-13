---
title: 📺 fongmi 局域网同步
order: 17
---

# 📺 fongmi 是什么

fongmi是一个在android上面非常好用的爬虫类播放器，可以支持很多非常方便的源订阅，源于tvbox变种。

# 📺 fongmi 局域网同步

通过定时轮询局域网内 [fongmi](https://github.com/fongmi) 设备的内置 HTTP API，检测「看完」的剧集并自动提交到 Bangumi。无需修改 APP 或安装插件。

## 1. 适用范围

本驱动依赖 fongmi 扩展的 `/media` 与 `/device` 端点，**仅适用于 fongmi 及其 fork**：

- [fongmi](https://github.com/fongmi)（蜂蜜影视，本体）
- [TV-K](https://github.com/kknifer7/TV-K)（影视-K版）
- [OK影视](https://github.com/okcaptain/TV)
- [WebHomeTV](https://github.com/motao123/webtv)
- 其他基于 fongmi 的二开版本

::: tip 判断方法
在设备上打开 fongmi APP，用浏览器访问 `http://设备IP:9978/media`，若返回 JSON 则支持。
:::

::: warning 不兼容原版 TVBoxOSC
原版 TVBoxOSC 谱系（q215613905/TVBoxOS、takagen99/Box 等）没有 `/media` 端点，无法使用本驱动。
:::

## 2. 工作原理

```
Bangumi-syncer  ──轮询──>  fongmi APP（电视/手机）
     │                         │
     │  GET /device             │  设备信息（uuid/name/ip/type）
     │  GET /media              │  播放状态（title/url/position/duration）
     │                          │
     └── 检测播放完成(≥80%) ──>  标记 Bangumi 单集为看过
```

- **设备发现**：扫描局域网默认端口 9978（通过 `/device` 端点识别 fongmi 设备）；非标端口的设备请在配置中手动指定 `ip:port`
- **集数解析**：从 `/media` 的 `url` 与 `artist` 字段解析集号（支持 `S01E001`、`EP01`、`第N集` 等格式）
- **完成判定**：`position / duration ≥ min_percent`（默认 80%），直播流不判定
- **去重**：每个设备每集只同步一次（进程内去重，重启后重置，Bangumi 标记操作幂等）

## 3. 配置

### 在 WebUI「配置管理」中设置

- 在配置区找到 **fongmi**。
- 勾选 **启用**。
- **设备 IP**：手动指定设备 IP（逗号分隔，可带端口），例如 `192.168.1.100` 或 `192.168.1.100:9979`。
- **自动扫描**：开启后会扫描下方网段。
- **网段**：例如 `192.168.1`（会扫描 `192.168.1.1-254` 的默认端口 9978）。
- **视为看完的最低进度**：默认 80%。
- **定时 Cron**：默认每 3 分钟 `*/3 * * * *`。
- 保存配置后，定时任务会随配置热更新，无需重启。

### Docker 部署注意事项

fongmi 设备发现依赖局域网扫描，Docker 部署时需使用 `host` 网络模式：

```yaml
services:
  bangumi-syncer:
    network_mode: host
    environment:
      - FONGMI_ENABLED=true
      - FONGMI_DEVICES=192.168.1.100
      # 或自动扫描
      - FONGMI_AUTO_SCAN=true
      - FONGMI_SUBNET=192.168.1
```

::: warning
bridge 网络模式下容器只能扫描到 `172.x.x.x` 网段，无法发现局域网设备。
:::

### 环境变量

| 环境变量               | 说明                           |
| ---------------------- | ------------------------------ |
| `FONGMI_ENABLED`       | 启用同步（`true`/`false`）     |
| `FONGMI_DEVICES`       | 设备 IP 列表（逗号分隔）       |
| `FONGMI_SUBNET`        | 自动扫描网段                   |
| `FONGMI_AUTO_SCAN`     | 启用自动扫描（`true`/`false`） |
| `FONGMI_SYNC_INTERVAL` | 定时 Cron 表达式               |
| `FONGMI_MIN_PERCENT`   | 完成阈值百分比                 |

## 4. API

| 端点                      | 方法 | 说明                                 |
| ------------------------- | ---- | ------------------------------------ |
| `/api/fongmi/status`      | GET  | 查看配置状态                         |
| `/api/fongmi/sync/manual` | POST | 手动触发一次同步                     |
| `/api/fongmi/debug/scan`  | POST | 调试：搜寻设备并拉取 /media 状态     |
| `/api/fongmi/debug/sync`  | POST | 调试：指定设备执行同步，返回前后对比 |

## 5. 手动同步测试

- 调用 `POST /api/fongmi/sync/manual` 或在 WebUI 调试工具中触发。
W
