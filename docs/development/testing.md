---
title: 🧪 测试与 CI
order: 7
---

# 🧪 测试与 CI

## 技术栈

- **测试框架**：[pytest](https://docs.pytest.org/) + [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio)
- **HTTP mock**：[respx](https://github.com/lundberg/respx)（mock httpx 请求）
- **E2E**：[pytest-playwright](https://playwright.dev/python/)
- **覆盖率**：[pytest-cov](https://pytest-cov.readthedocs.io/)，上传 [Codecov](https://about.codecov.io/)
- **并行**：`pytest -n auto`（pytest-xdist）

---

## 测试组织

```
tests/
├── conftest.py             # 全局 fixture（配置隔离、数据库 mock）
├── api/                    # API 层测试（端到端 HTTP，含鉴权）
├── core/                   # Config / Database / Security / Scheduler
├── services/               # SyncService / 驱动 / 通知规则
├── utils/                  # BangumiApi / Archive / Notifier
├── models/                 # Pydantic 模型测试
├── trakt/                  # Trakt 专项测试
├── e2e/                    # 端到端测试（Playwright，默认不跑）
└── integration/
    └── test_docker_perms.sh  # Docker 权限兼容性集成测试
```

---

## 运行测试

```bash
# 全量单元测试 + 覆盖率（默认排除 e2e）
uv run pytest tests/ --cov=app --cov-report=term

# 仅跑某个目录
uv run pytest tests/services/

# 并行加速
uv run pytest tests/ -n auto

# 跑 E2E（需先启动服务器 + 安装浏览器）
uv run playwright install chromium
uv run pytest tests/e2e/ -m e2e --browser chromium

# 跑 Docker 集成测试
docker build -t bangumi-syncer:test .
./tests/integration/test_docker_perms.sh bangumi-syncer:test
```

---

## 测试约定

- **配置隔离**：`conftest.py` 在导入 `app` 模块前把 `CONFIG_FILE` 重定向到临时目录，并强制禁用 `bangumi-archive` 和 `bangumi-replay`，避免单例在导入时启动后台线程卡死测试。需要真实 `config.ini` 调试时设 `BS_TEST_USE_LOCAL_CONFIG=1`。
- **HTTP mock**：用 respx mock httpx，不要真实请求 Bangumi API。
- **数据库 mock**：用 `tmp_path` fixture 创建临时 SQLite，或 patch `database_manager._conn`。
- **异步测试**：`asyncio_mode = "auto"`，直接写 `async def test_xxx()` 即可。
- **测试污染恢复**：`pre-commit` 钩子会在测试跑完后自动恢复被污染的 `config.ini`。

---

## 手动触发的测试

CI 只覆盖「机器能自动判定对错」的部分：单元 / API 测试、lint、E2E 冒烟、Docker 权限。下面这些**必须人工发起**，因为它们的判定依赖真实数据、真实外部服务，或需要人来看结果。

### 场景速查表

| 想验证什么 | 用什么 | 额外前置条件 | 会写库 / 写 Bangumi 吗 |
| --- | --- | --- | --- |
| 改匹配逻辑有没有静默改变既有行为 | `scripts/golden_check.py` | L2 需 archive 归档库 | 否 |
| 裁决层（Arbiter）值不值得开 | `scripts/eval_arbiter.py` | 需 archive 归档库 | 否 |
| 记忆三件套是否健康 | `scripts/memory_selfcheck.py` | 需真实 `sync_records.db` | 否（只读） |
| 某条标题到底能不能匹配对 | 调试工具 → **匹配测试** | 无 | 否 |
| 端到端跑通一次同步（含标记看过） | 调试工具 → **同步测试** | 需 Bangumi Token | **是** |
| 飞牛 / fongmi 能不能拉到数据 | 调试工具对应卡片 | 需真实设备或飞牛库 | 否（扫描）/ **是**（同步） |
| 失败记录重试能不能成功 | 同步记录 → **重试同步** | 无 | **是** |
| 通知渠道配通了没有 | 配置管理 → 通知 → 测试 | 无 | 发一条真实通知 |
| 候选确认后会不会走映射 | 候选确认 → 确认 / 拒绝 | 需有沉淀候选 | 写自定义映射 / 屏蔽词 |
| Archive 能不能更新成功 | Archive 离线库 → 强制更新 | 需网络 | 否（写本地库） |
| 补发队列能不能清空 | 待同步队列 → 批量补发 | 需有 `pending_sync_queue` | **是** |
| 容器权限兼容性 | `tests/integration/test_docker_perms.sh` | 需 Docker | 否（临时容器） |

### 一、匹配基线检查（改动匹配逻辑前后必跑）

`scripts/golden_check.py` 把「生成基线时的实际行为」逐条比对，报出差异与 oracle 命中率。

```bash
uv run python scripts/golden_check.py            # L1 离线集（自带 fixture，无需 archive）
uv run python scripts/golden_check.py --l2       # 追加 L2 全量管线集（需 archive 库）
uv run python scripts/golden_check.py --l2 --quiet   # 只输出结论与命中率
```

- **L1**（`bangumi_data_cases.json`，360 条 / 12 场景）：只跑 bangumi-data 单层匹配，仓库自带精简 fixture，**任何时候都能跑**。
- **L2**（`matching_cases.json`，240 条 / 8 场景）：真实 Archive 数据上的完整管线，需要本地已导入归档库（约 300MB，不在仓库中）。
- 退出码：`0` = 与基线一致；`1` = 有差异或命中率低于护栏（`MIN_ORACLE_HIT_RATE = 0.90`）。

::: warning 没有归档库时 `--l2` 会报失败，不会静默跳过
L2 走子进程复用生成脚本，而生成脚本在找不到归档库时返回码为 `1`，`golden_check.py` 把它当作「重跑失败」处理，最终退出码 `1`、结论是「与基线存在差异」。
首次在干净环境跑 `--l2` 看到这个结论时，先确认 `data/archive/` 下确实有归档库，再怀疑匹配退化 —— 否则会误判。
只想跑离线集就用不带 `--l2` 的默认调用。
:::

::: tip L2 读你 `config.ini` 里的 `data_dir`
L2 走子进程复用生成脚本，生成脚本会复制**你的 `config.ini`** 到临时目录（只覆盖 `cache_ttl_days`、`bangumi-archive.enabled=false`、日志相关项），因此 `data_dir` 用的是你本地配置的值 —— 默认 `./data/archive`，改过就按你改的来。
注意 L1 与 L2 的配置来源不同：`golden_check.py` 自身用 `config.example.ini` 跑 L1，L2 子进程用 `config.ini`。两边都不会污染你的真实配置。
:::

::: warning 基线不是「正确答案」
基线的期望值是**生成时的行为快照**，不是「应当如此」的断言。报出差异只说明"行为变了"，不说明"变坏了"——要结合 oracle 命中率判断方向。
:::

**确认改动是有意为之后**，用生成脚本刷新基线：

```bash
uv run python scripts/gen_golden_cases.py --mode l1    # 只刷 L1
uv run python scripts/gen_golden_cases.py --mode l2    # 只刷 L2
uv run python scripts/gen_golden_cases.py --mode all   # 两个都刷
```

常用参数：`--n`（每场景用例数，代码默认 33；**仓库内现有基线是 30**）、`--seed`（默认 `20260908`）、`--distractors`（L1 每个目标附带的干扰条目数，默认 10）、`--out`（改写到别处，不覆盖仓库基线）。

::: tip 采样必须确定性
用例集用固定种子 + 按 bangumi id 排序的候选池生成。改 `--n` 或 `--seed` 会让新旧用例集不可比，A/B 数字失去意义。
:::

### 二、裁决层 A/B 评估

想量化 `[matching] arbiter_enabled` 开关的收益时用它。同一批用例分别跑「裁决关闭」与「裁决开启」，对比自动采用率 / 精确率 / 召回率与逐条行为变化。

```bash
uv run python scripts/eval_arbiter.py        # 每场景 30 条（默认）
uv run python scripts/eval_arbiter.py 60     # 放大用例规模
```

两次运行都走子进程 + 临时配置，因此差异只来自 `arbiter_enabled` 这一个开关。结果 JSON 落在系统临时目录，脚本结束时打印路径便于复核。**需要 archive 归档库。**

### 三、记忆功能自检（只读）

端到端验收记忆三件套（热层 / 冷层 / FTS + 消费标记）时用。输出表结构健康度、最近记忆条目、消费标记一致率、`llm_usage` 归属统计，全部只读，可反复执行。

```bash
uv run python scripts/memory_selfcheck.py                      # 基础检查
uv run python scripts/memory_selfcheck.py --titles 芙莉莲 葬送的芙莉莲   # 同剧关联演示
uv run python scripts/memory_selfcheck.py --keywords 芙莉莲      # FTS 关键词演示
uv run python scripts/memory_selfcheck.py --db /path/to.db     # 指定库路径
```

退出码：`0` = 通过；`1` = 表结构缺失等问题。

### 四、WebUI「调试工具」的手动触发

打开 `http://localhost:8000/debug`（需登录），页面上的按钮等价于下列接口。

| 面板 | 动作 | 等价接口 | 说明 |
| --- | --- | --- | --- |
| 番剧测试 | 开始匹配测试 | `POST /api/test-match` | **只跑匹配**，不写库、不发通知，返回完整 `MatchTrace` |
| 番剧测试 | 开始同步测试 | `POST /api/test-sync?async_mode=false` | **完整同步**并写入同步记录 |
| 飞牛同步测试 | 立即触发飞牛同步 | `POST /api/feiniu/sync/manual` | 可带 `?user=<guid>` 只测某个飞牛用户 |
| fongmi 同步测试 | 搜寻设备并拉取状态 | `POST /api/fongmi/debug/scan` | 不管观看进度，只验证设备发现与集数解析；20s 超时 |
| fongmi 同步测试 | 行内「同步」按钮 | `POST /api/fongmi/debug/sync` | 对指定设备执行一次真实同步，返回前后对比；30s 超时 |
| 网络诊断工具 | 开始诊断 | `POST /api/network/diagnose` | 检测到 Bangumi API 的连通性、代理、TLS |
| 网络诊断工具 | 测试 | `POST /api/proxy/test-host` | 测到任意主机端口的连通性 |
| 网络诊断工具 | 代理测试 | `POST /api/proxy/test` | 测代理可用性与延迟 |

匹配测试的请求体字段：`title`、`ori_title`、`season`、`episode`、`release_date`、`user_name`、`media_type`。

::: tip 用户名没配好时怎么测
开启 `[sync] test_skip_permission_check = true` 后，`/api/test-sync`、`/api/fongmi/debug/sync` 等测试接口不再校验 `media_server_username` / 用户映射，方便先验证匹配与标记。**只对测试来源生效**，生产 webhook 路径不受影响。
:::

::: danger 同步测试会真的写 Bangumi
「同步测试」和 fongmi 的「同步」按钮都会实际调用 Bangumi API 标记看过（幂等）。想只看匹配结果就用「匹配测试」。
:::

### 五、业务页面上的手动触发

这些入口在各自的业务页面里，属于「重跑一次已经配好的流程」。

| 页面 | 动作 | 接口 | 备注 |
| --- | --- | --- | --- |
| 同步记录 | 重试同步 | `POST /api/records/{id}/retry` | 弹窗展示实时 debug 日志（`GET /api/records/{id}/retry/stream`，SSE） |
| 同步记录 · 匹配详情 | 查看匹配过程 | `GET /api/match-records/{id}/trace` | 逐 step 展示输入 / 输出、候选列表、各阶段耗时 |
| 候选确认 | 确认 | `POST /api/pending-candidates/{id}/confirm` | 写入自定义映射 |
| 候选确认 | 拒绝 | `POST /api/pending-candidates/{id}/reject` | 候选标题写入屏蔽关键词（`source=reject`） |
| 映射管理 · 正则匹配测试 | 测试正则 | 纯前端（`new RegExp`） | 不调后端，验证正则能否命中给定标题 |
| Archive 离线库 | 强制更新 / 上传 zip | `POST /api/bangumi_archive/trigger?force=true`、`POST /api/bangumi_archive/import_local` | `trigger` 返回 `task_id` 供 SSE 订阅进度 |
| 待同步队列 | 批量补发 / 单条补发 / 探测 API | `POST /api/bangumi_replay/replay?limit=20`、`/replay/{record_id}`、`/probe` | `probe` 只探 API 可达性，不补发 |
| Trakt | 手动同步 | `POST /api/trakt/sync/manual` | 请求体 `full_sync` 决定是否全量 |
| 配置管理 · 摘要任务 | 测试 | `POST /api/summary/jobs/{name}/test` | 只生成摘要并返回，**不发通知**，用于调 `system_prompt` |
| 配置管理 · 摘要任务 | 立即触发 | `POST /api/summary/jobs/{name}/trigger` | 完整执行（生成 + 发通知），等同定时触发；任务在执行中时返回 `skipped` |
| 配置管理 · 通知 | 测试 | `POST /api/notification/test` | 也可按渠道单测：`/notification/webhooks/{id}/test`、`/emails/{id}/test`、`/wecoms/{id}/test`、`/dingtalks/{id}/test` |
| 配置管理 · LLM | 测试连接 | `POST /api/llm/test` | 发一个 `ping` 验证 LLM 连通性，只看延迟不看正文 |

### 六、pytest 侧的手动开关

```bash
# 用真实 config.ini 跑测试（默认用 config.example.ini，保证可复现）
# PowerShell
$env:BS_TEST_USE_LOCAL_CONFIG=1; uv run pytest tests/services/test_sync_service.py
# bash
BS_TEST_USE_LOCAL_CONFIG=1 uv run pytest tests/services/test_sync_service.py

# 只跑某些用例
uv run pytest tests/ -k "blacklist or gate"

# 并行（注意 --dist=loadscope 让同一文件的测试落在同一 worker）
uv run pytest tests/ -n 4 --dist=loadscope

# E2E：默认被 addopts 的 -m 'not e2e' 排除，需显式 -m e2e
uv run playwright install chromium
uv run pytest tests/e2e/ -m e2e --browser chromium

# 失败时留档（CI 用的参数）
uv run pytest tests/e2e/ -m e2e --browser chromium \
  --screenshot=only-on-failure --video=retain-on-failure \
  --tracing=retain-on-failure --output=test-results/e2e
```

E2E 会自己拉起 `uvicorn app.main:app`（`tests/conftest.py` 的 `test_server` fixture，端口 8000），并用 `admin/admin` 登录。共 25 条，覆盖冒烟、登录、页面渲染、LLM 配置表单、摘要记忆表单、同步重试与通知规则 UI。

### 七、跳过 pre-commit

`pre-commit` 每次都会跑全量 `ruff check` + `ruff format` + `djlint` + `pytest`（`-n 4 --dist=loadscope -m 'not slow and not e2e'`），没有内置的"只跑部分测试"开关。赶时间时用 `--no-verify` 跳过：

```bash
git commit --no-verify -m "..."
```

::: warning 跳过钩子前请自行跑过检查
`--no-verify` 只是不跑本地的钩子，CI 照样会跑同样的检查并可能卡住 PR。至少先本地执行 `uv run pytest tests/ -x`。
:::

---

## CI / CD

`.github/workflows/` 下有这些关键工作流：

| 工作流 | 触发 | 内容 |
| --- | --- | --- |
| `lint.yml` | push / PR | `ruff check` + `ruff format --check` + `djlint templates/ --check` |
| `ci-tests.yml` | push（非 `v*` tag）/ PR | `pytest` + 覆盖率上传 Codecov + Docker 权限集成测试 |
| `e2e-tests.yml` | push / PR（限 `app/` `templates/` `static/` `tests/e2e/` `tests/conftest.py` `pyproject.toml`） | Playwright E2E（chromium），失败上传截图 / 视频 / trace |
| `docs.yml` | PR 合并到 `main` | 部署 VitePress 到 GitHub Pages |
| Docker 发布 | 推 `v*` tag | 构建多架构镜像推到 Docker Hub |

::: tip 手动脚本不在 CI 里
`golden_check.py` / `gen_golden_cases.py` / `eval_arbiter.py` / `memory_selfcheck.py` 都**不接 CI**：它们要么依赖本地 archive 归档库，要么需要人工判断结果。CI 绿了不等于匹配质量没退化——改匹配逻辑时请自行跑一遍基线检查。
:::

---

## pre-commit 钩子

仓库内置 `.githooks/pre-commit`，安装后每次 `git commit` 自动跑依赖同步、`ruff check`、`ruff format`、`djlint --reformat` 和全量 `pytest`：

```bash
git config core.hooksPath .githooks      # 跨平台
```

紧急情况下可用 `git commit --no-verify` 跳过（CI 仍会跑同样检查）。
