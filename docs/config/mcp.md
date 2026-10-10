---
title: 🔌 MCP 接入
order: 40
---

# 🔌 MCP 接入

Bangumi-syncer（BS）内置 MCP 服务，AI 助手可以通过它直接读取运行日志、查看配置，无需打开 Web 管理页。

## 配置项

### `[dev] mcp_base_url`

MCP 服务公共 URL。反向代理 / 局域网 / 域名访问时填写服务对外可达地址（例如 `https://bs.example.com`），
用于生成 OAuth issuer 及授权 / token 端点，填错会导致 MCP 客户端无法完成授权发现。

解析优先级：`create_mcp_server(base_url=...)` 参数 > `MCP_BASE_URL` 环境变量 > `[dev] mcp_base_url` 配置 > 默认值 `http://localhost:8000`。

该配置在进程启动时读取一次，通过 Web 管理页修改后**需重启 BS 才生效**。
