---
title: 🐳 Docker
order: 11
---

# 🐳 Docker

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
      # 以下可选：仅在使用「飞牛影视」同步时挂载飞牛 trimmedia.db（只读）。不用飞牛则不要加。
      # - /usr/local/apps/@appdata/trim.media/database:/app/data/feiniu-db:ro
    restart: unless-stopped
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Asia/Shanghai
```

环境变量说明
| 参数名 | 默认值 | 说明 |
| :----: | :----: | :--: |
| PUID | 1000 | 用户 ID |
| PGID | 1000 | 组 ID |
| TZ | Asia/Shanghai | 时区 |

1. 等待镜像拉取与容器启动完成后，浏览器访问 `http://localhost:8000`（若在远程主机部署，将 `localhost` 改为该主机 IP 或域名）进入 Web 管理界面。

2. **首次使用登录信息**：

   - 用户名：`admin`
   - 密码：`admin`
   - 登录后请立即在「配置管理」页面修改默认密码

3. 点击「配置管理」进行在线配置
