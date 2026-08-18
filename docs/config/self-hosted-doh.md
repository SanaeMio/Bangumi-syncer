---
title: 🛠️ 自建 ECH DoH（进阶）
order: 999
---

# 🛠️ 自建 ECH DoH（进阶）

::: warning 需要一定门槛
本节面向**有一定折腾能力的进阶用户**：需要注册 Cloudflare 账号、会基础的 Workers 部署操作（复制粘贴代码、绑定域名）。如果只是想让 ECH 生效，直接用默认的 `https://dns.alidns.com/resolve`（阿里公共 DNS）即可，**无需阅读本节**。
:::

## 为什么自建 DoH？

默认使用公共 DoH 服务（如 Google）就能正常获取 ECH 配置。自建的理由通常是：

- 公共 DoH 在你的网络环境下**不可达或太慢**；
- 想用**优选 IP**（自测更快的 Cloudflare 地址）替换默认解析结果；
- 不想把 DNS 查询交给第三方服务。

## 可选方案

| 方案 | 难度 | 说明 |
| --- | --- | --- |
| 国内公共 DoH（默认） | 无 | 阿里公共 DNS：`https://dns.alidns.com/resolve`，国内直连稳定、无需自建，实测可正常返回 ECH 配置 |
| 公共 DoH（备选） | 无 | `https://dns.google/resolve`（Google 公共 DNS），海外网络建议改用此项 |
| 自建 DoH 转发 | 低 | 把 DoH 请求转发到上游（如 dns.google），配置项只填自建地址 |
| Total-ECH（本页方案） | 中 | Cloudflare Workers 上的 DoH，自动为 CDN 域名附加 ECH 配置，支持优选 IP 替换 |

## Total-ECH 是什么

基于 **Cloudflare Workers** 的开源 DoH 服务（[GitHub 仓库](https://github.com/RememberOurPromise/Total-ECH)）。你查询某个域名时，它判断该域名是否由 Cloudflare / Meta CDN 托管，若是就自动在 HTTPS 记录中附加 ECH 配置，让浏览器或程序的 TLS 握手隐藏真实访问目标。它还能把 CDN 域名解析到你自己测出的**优选 IP**。

::: tip 与 Bangumi-syncer 的关系
它只是「ECH 配置的获取来源」，把配置页的 **DoH 端点** 指到自建地址即可，同步功能不受影响；获取失败会自动降级为普通 TLS。
:::

## 部署步骤

1. 注册并登录 [Cloudflare](https://dash.cloudflare.com/) 控制台，进入 **Workers & Pages**；
2. 点击 **创建 Worker**，随便起个名字；
3. 打开 [Total-ECH 的 `_worker.js`](https://github.com/RememberOurPromise/Total-ECH/blob/main/_worker.js)，**全选复制**，粘贴到 Worker 在线编辑器中（可自定义顶部 `API_PATH` 路径）；
4. 点击 **保存并部署**；
5. 建议在 Worker 的「设置 → 域名和路由」**绑定一个自己的域名**（如 `ech.example.com`），之后使用 `https://ech.example.com/doh-ech-test` 访问。绑定自己的域名是因为 Workers 免费域名（`*.workers.dev`）可能在国内网络不可达。

## 接口与参数

| 接口 | 行为 |
| --- | --- |
| `/doh-ech-test` | 标准 ECH DoH：解析 DNS 查询，CDN 域名自动附加 ECH 配置（路径可自定义） |
| `/doh-test` | 纯净转发：不改任何记录，直通上游 DoH |

可选查询参数（拼在 DoH 地址后，如 `?ip4=104.18.11.118`）：

- `?ip4=` / `?ip6=`：把 CDN 域名解析结果**替换为优选 IP**（优先级最高）；
- `?cf=域名`：按域名解析出 IP 做替换（优先级低于 `ip4`/`ip6`）；
- `?ech=域名`：自定义 ECH 配置来源（默认 `cloudflare-ech.com`）。

## 在 Bangumi-syncer 中使用

在 `config.ini` 的 `[dev]` 段填写：

```ini
[dev]
ech_mode = doh
ech_doh_url = https://ech.example.com/doh-ech-test
```

::: warning 格式兼容说明
本项目按「先 JSON、后二进制」的顺序向 DoH 端点查询。Total-ECH 只支持**二进制格式**（DNS wireformat），JSON 查询会被忽略——这是正常的，程序会自动改走二进制查询，无需额外设置。对接成功后，日志里会出现类似 `[ECH] DoH(wireformat) 获取 ECH 配置成功（cloudflare-ech.com）` 的提示。
:::

## 验证是否生效

重启程序后查看运行日志，出现 `[ECH] ... 获取 ECH 配置成功` 即对接成功；若看到 `[ECH] ... 降级为普通 TLS`，检查域名是否可访问、是否已正确绑定 Worker。
