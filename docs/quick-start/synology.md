---
title: 🏠 群晖 NAS
order: 12
---

# 🏠 群晖 NAS

**此处以 DSM 7.2 为例**

## 方式一：通过 Container Manager 项目（docker-compose）

1. 打开 Container Manager，点击「项目」→「新增」
2. 项目名称填写：`bangumi-syncer`
3. 路径选择：`/docker/bangumi-syncer`
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
      - ./config:/app/config
      - ./logs:/app/logs
      - ./data:/app/data
    restart: unless-stopped
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Asia/Shanghai
```

> 如有设置共享文件夹权限，也可以修改文件夹映射为如 `/volume1/docker/bangumi-syncer/...` 等绝对路径。

5. 点击「下一步」，根据需求设置 `Web Station 网页门户`（反向代理）
6. 点击「下一步」，预览摘要，无误后点击「完成」并等待项目启动
7. 浏览器访问 `http://群晖IP:8000` 进入 Web 管理界面
8. **首次使用登录信息**：

   - 用户名：`admin`
   - 密码：`admin`
   - 登录后请立即在「配置管理」页面修改默认密码

9. 点击「配置管理」进行在线配置

## 方式二：通过 Container Manager 镜像仓库（原注册表）

> **注意：** 由于大陆网络问题，请自行更换存储库。

1. 打开 Container Manager，点击「镜像仓库」
2. 搜索 `sanaemio/bangumi-syncer`，镜像并点击「下载」
3. 选择标签：`latest` 并下载
4. 点击「映像」找到下载好的 `sanaemio/bangumi-syncer`，打开点击「运行」
5. 容器名称：`bangumi-syncer`
6. 在「高级设置」中：

   - 端口设置：本地端口 `8000`，容器端口 `8000`，默认 `TCP`
   - 卷：添加以下映射（路径可以根据自己情况调整）
     - `/docker/bangumi-syncer/config` → `/app/config`（默认选可读写）
     - `/docker/bangumi-syncer/logs` → `/app/logs`（默认选可读写）
     - `/docker/bangumi-syncer/data` → `/app/data`（默认选可读写）

7. 点击「下一步」，预览摘要无误后点击「完成」并等待容器启动，浏览器访问 `http://群晖IP:8000` 进入 Web 管理界面
8. **首次使用登录信息**：

   - 用户名：`admin`
   - 密码：`admin`
   - 登录后请立即在「配置管理」页面修改默认密码

9. 点击「配置管理」进行在线配置
