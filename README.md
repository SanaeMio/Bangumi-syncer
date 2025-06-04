<p align="center">
  <a href="https://github.com/SanaeMio/Bangumi-syncer">
    <img alt="Bangumi-syncer Logo" width="250" src="https://p.sda1.dev/16/7b48f7a38f0deb790f0fdc17390e0d93/logo.png">
  </a>
</p>
<p align="center">
  <a href="https://github.com/SanaeMio/Bangumi-syncer"><img alt="languages" src="https://img.shields.io/github/languages/top/SanaeMio/Bangumi-syncer"/></a>
  <a href="https://github.com/SanaeMio/Bangumi-syncer/releases"><img alt="release" src="https://img.shields.io/github/v/release/SanaeMio/Bangumi-syncer"/></a>
  <a href="https://github.com/SanaeMio/Bangumi-syncer/blob/main/LICENSE"><img alt="license" src="https://img.shields.io/github/license/SanaeMio/Bangumi-syncer"/></a>
</p>

## 🔖目录
- [🌟 简介](#-简介)
- [🧰 安装](#-安装)
  - [Windows](#Windows)
  - [Docker](#Docker)
  - [群晖NAS](#群晖NAS)
- [🔧 配置](#-配置)
- [🥰 使用](#-使用)
  - [方式一：自定义Webhook](#自定义Webhook)
  - [方式二：Plex(Tautulli)](#Tautulli)
  - [方式三：Plex Webhooks](#Plex-Webhooks)
  - [方式四：Emby通知](#Emby通知)
  - [方式五：Jellyfin Webhook插件](#Jellyfin插件)
- [📖 计划](#-计划)
- [😘 贡献](#-贡献)
- [👏 鸣谢](#-鸣谢)
- [📄 许可](#-许可)

## 🌟 简介
通过Webhook调用 [Bangumi Api](https://bangumi.github.io/api/)，实现在客户端看完后自动同步打格子。

已适配Plex、Emby、Jellyfin。

![QQ%E5%9B%BE%E7%89%8720240319171758.png](https://p.sda1.dev/16/bd3803efe27dc9a27f85d01f7e771a06/QQ图片20240319171758.png)

## 🧰 安装

### Windows
1. 请保证Python版本3.7以上，并安装以下依赖
```
pip install requests fastapi pydantic uvicorn[standard] ijson
```

2. 下载 zip并解压到任意文件夹。 [发布页](https://github.com/SanaeMio/Bangumi-syncer/releases)

3. 双击 `start.bat`，无报错即可

4. 如果你希望修改默认端口号，可以用文本编辑器打开`start.bat`，修改`--port 8000`为`--port 你的自定义端口号`

### Docker

docker-compose:
```yaml
version: '3.8'

services:
  bangumi-syncer:
    image: sanaemio/bangumi-syncer:latest
    container_name: bangumi-syncer
    network_mode: bridge
    ports:
      - "8000:8000"
    volumes:
      - /docker/bangumi-syncer/config:/app/config
      - /docker/bangumi-syncer/logs:/app/logs
      - /docker/bangumi-syncer/data:/app/data
    restart: unless-stopped
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Asia/Shanghai
```
|            参数名            |      默认值       |                             说明                             |
| :--------------------------: | :---------------: | :----------------------------------------------------------: |
|             PUID             |         0         |                           用户 ID                            |
|             PGID             |         0         |                            组 ID                             |
|              TZ              |   Asia/Shanghai   |                             时区                             |

#### 群晖NAS

**方式一：通过 Container Manager（推荐）**

1. 打开 Container Manager，点击「项目」→「新增」
2. 项目名称填写：`bangumi-syncer`
3. 路径选择：`/volumeX/docker/bangumi-syncer`（X为你的存储空间编号，如存储空间1则为`/volume1/docker/bangumi-syncer`）
4. 来源选择「创建 docker-compose.yml」，内容填写：

```yaml
version: '3.8'

services:
  bangumi-syncer:
    image: sanaemio/bangumi-syncer:latest
    container_name: bangumi-syncer
    network_mode: bridge
    ports:
      - "8000:8000"
    volumes:
      - /volumeX/docker/bangumi-syncer/config:/app/config
      - /volumeX/docker/bangumi-syncer/logs:/app/logs
      - /volumeX/docker/bangumi-syncer/data:/app/data
    restart: unless-stopped
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Asia/Shanghai
```

> **注意**：请将上述配置中的 `volumeX` 替换为你的实际存储空间编号：
> - 第一个存储空间：`/volume1/docker/bangumi-syncer/...`
> - 第二个存储空间：`/volume2/docker/bangumi-syncer/...`
> - 以此类推

5. 点击「启动」，等待容器创建完成
6. 通过 File Station 进入 `/volumeX/docker/bangumi-syncer/config/` 目录
7. 编辑 `config.ini` 文件（参考下方配置说明）
8. 回到 Container Manager，重启 `bangumi-syncer` 容器

**方式二：通过 Docker 注册表**

1. 打开 Container Manager，点击「注册表」
2. 搜索 `sanaemio/bangumi-syncer`，下载镜像
3. 点击「映像」→「启动」
4. 容器名称：`bangumi-syncer`
5. 在「高级设置」中：
   - 端口设置：本地端口 `8000`，容器端口 `8000`
   - 卷：添加以下映射（路径可以根据自己情况调整）
     - `/docker/bangumi-syncer/config` → `/app/config`
     - `/docker/bangumi-syncer/logs` → `/app/logs`
     - `/docker/bangumi-syncer/data` → `/app/data`
6. 启动容器后，通过 File Station 编辑配置文件（参考下方配置说明）
7. 重启容器使配置生效

## 🔧 配置
修改config.ini，根据注释说明，填写如下三个必填项：

**Bangumi 用户名 (`username`)** (必填)
- 访问你的 Bangumi 个人主页：`https://bgm.tv/user/你的用户名`
- 填写用户名或者 URL 中的数字 ID

**访问令牌 (`access_token`)** (必填)
- 访问：https://next.bgm.tv/demo/access-token
- 登录后点击「创建令牌」
- 复制生成的令牌（注意保存，只显示一次）

**媒体服务器用户名 (`single_username`)** (必填)
- Plex：Plex 设置中的用户名
- Emby：Emby 用户管理中的用户名  
- Jellyfin：Jellyfin 用户管理中的用户名

**自定义映射 (`bangumi-mapping`)** (可选)
- 用于处理程序无法自动匹配的番剧
- 格式：`番剧名 = bangumi_subject_id`，示例：`魔王学院的不适任者 = 292222`
- 如果没有发现匹配失败的条目则无需填写

**代理 (`script_proxy`)** (可选)
- 如果需要通过代理访问 Bangumi API，请填写代理地址
- 格式：`http://127.0.0.1:7890` 或 `socks5://127.0.0.1:1080`
- 留空则不使用代理



## 🥰 使用
### 自定义Webhook

1. 运行Bangumi-syncer
2. 在观看平台完成播放后，构建以下json格式的Webhook
```bash
{
  "media_type": 媒体类型（目前写死episode）,
  "title": 中文名,
  "ori_title": 原名（取不到就给空）,
  "season": 季度,
  "episode": 集数,
  "release_date": 发布日期（取不到第一集的给当前集数的也行，格式YYYY-MM-DD）,
  "user_name": 用户名（同步发起方的用户名）
}
```
比如
```bash
{
  "media_type": "episode",
  "title": "我心里危险的东西",
  "ori_title": "僕の心のヤバイやつ",
  "season": 2,
  "episode": 12,
  "release_date": "2023-04-01",
  "user_name": "SanaeMio"
}
```
3. 将以上json发送到`http://{ip}:8000/Custom`，ip根据本机情况填写

4. 播放完成后，查看`控制台日志`或`log.txt`是否同步成功

### Tautulli
**（默认您已将Plex与Tautulli绑定完成，以下内容只需要设置一次）**

1. 运行Bangumi-syncer

2. 打开Tautulli控制面板，右上角`Settings` -> `Notification Agents` -> `Add a new notification agent` -> 选择`Webhook`
![](https://p.sda1.dev/16/c01e9de56892498c0163a0ffb7d112fe/1.jpg)

3. 在弹出页面的`Configuration`中的`Webhook URL`填写`http://{ip}:8000/Custom`，ip根据本机情况填写
![](https://p.sda1.dev/16/3e08440dbe4c35c35ba4981a4c8945ed/2.jpg)

4. `Triggers`勾选`Watched`
![](https://p.sda1.dev/16/330e03b24a4c0e1987818955faf68e6b/3.jpg)

5. `Conditions`建议填写，以减少Webhook请求次数。这里我限制了用户名和单集的时候才会触发Webhook。
第一个条件是限制用户名，改成自己的。第二个条件是限制媒体类型为单集，写死为`episode`。`Condition Logic`填写为`{1} and {2}`，表示两个条件同时满足时才触发。
![](https://p.sda1.dev/16/9867047ad2c133ec5e47fdf8ad9256ed/4.jpg)

6. `Data`中展开`Watched`，在`JSON Data`中填写如下通知模版，然后点击右下角`Save`保存设置

```bash
{"media_type": "{media_type}", "title": "{show_name}", "ori_title": " ", "season": "{season_num}", "episode": "{episode_num}", "release_date": "{air_date}", "user_name": "{username}"}
```

![](https://p.sda1.dev/16/6870cf7c4167203114bc4df7eac4b41a/5.jpg)

7. 在Plex播放完成后，观察`控制台日志`或`log.txt`是否同步成功

### Plex Webhooks
**（默认您的账号已拥有Plex Pass，以下内容只需要设置一次）**

1. 运行Bangumi-syncer
2. 打开Plex控制面板，右上角`设置` -> `Webhooks` -> `添加 Webhook`
![](https://p.sda1.dev/16/e68729e1d454bdd23a7c9fe76ca71251/1.jpg)

3. 填写网址为`http://{ip}:8000/Plex`，ip根据本机情况填写，点击`保存修改`

4. 在Plex播放完成后，查看`控制台日志`或`log.txt`是否同步成功

### Emby通知

1. 运行Bangumi-syncer
2. 打开Emby控制面板 -> `应用程序设置` -> `通知` -> `添加通知` -> 选择`Webhooks`
![](https://p.sda1.dev/16/ba2ca4af8b382aebd6e9782c7971f703/1.jpg)
3. 名称随意填写，URL填写`http://{ip}:8000/Emby`，ip根据本机情况填写，请求内容类型选择`application/json`，Events里勾选`播放-停止`和`用户-标记为已播放`，`将媒体库事件限制为`根据自己情况，建议只勾选包含动画的库，最后点击`储存`
4. 在Emby播放完成 或 手动标记为已播放后，查看`控制台日志`或`log.txt`是否同步成功

### Jellyfin插件

1. 运行Bangumi-syncer
2. 打开Jellyfin控制台 -> `插件` -> `目录` -> 拉到最下面找到点进`Webhook` -> 选择`8.0.0.0`版本，点击`Install`安装此插件然后 **重启服务器**
![](https://p.sda1.dev/16/be346724555f34a98b5dc16c73df794f/1.jpg)
3. 打开Jellyfin控制台 -> `插件` -> `我的插件` -> 点进`Webhook`。`Server Url`里输入你的Jellyfin地址，点击`Add Generic Destination`
![](https://p.sda1.dev/16/038568513c591f785d10ee745f254966/2.jpg)
4. 展开下方的`Generic`,`Webhook Name`随便填，`Webhook Url`输入`http://{ip}:8000/Jellyfin`，ip根据本机情况填写。
`Notification Type`只选中`Playback Stop`，`Item Type`只选中`Episodes`。`Template`填写如下模版，然后点击`Save`保存设置

```bash
{"media_type": "{{{ItemType}}}","title": "{{{SeriesName}}}","ori_title": " ","season": {{{SeasonNumber}}},"episode": {{{EpisodeNumber}}},"release_date": "{{{Year}}}-01-01","user_name": "{{{NotificationUsername}}}","NotificationType": "{{{NotificationType}}}","PlayedToCompletion": "{{{PlayedToCompletion}}}"}
```

5. 在Jellyfin播放完成后，查看`控制台日志`或`log.txt`是否同步成功

## 📖 计划
✅ 支持自定义Webhook同步标记

✅ 支持Plex（Tautulli）同步标记

✅ 支持指定单用户同步

✅ 适配Plex原生Webhook（需要Plex Pass）

✅ 适配Emby通知

✅ 适配Jellyfin（需要jellyfin-plugin-webhook插件）

✅ 支持通过 bangumi-data 匹配番剧 ID，减少 API 请求

✅ 支持Docker部署

⬜️ 支持多账号同步

⬜️ 支持 剧场版动画/电影 同步标记

⬜️ ……

## 😘 贡献
因为我不是专业python开发者，纯兴趣，代码比较垃圾请见谅

如果存在bug或想增加功能，欢迎 [提一个 Issue](https://github.com/SanaeMio/Bangumi-syncer/issues/new/choose) 或者提交一个 Pull Request

## 👏 鸣谢

- [kjtsune/embyToLocalPlayer](https://github.com/kjtsune/embyToLocalPlayer)
- [bangumi-data/bangumi-data](https://github.com/bangumi-data/bangumi-data)

## 📄 许可

[MIT](https://github.com/SanaeMio/Bangumi-syncer/blob/main/LICENSE) © SanaeMio
