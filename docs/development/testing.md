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
├── e2e/                    # 端到端测试（Playwright）
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

## CI / CD

`.github/workflows/` 下有 4 个关键工作流：

| 工作流 | 触发 | 内容 |
| --- | --- | --- |
| `lint.yml` | push / PR | `ruff check` + `ruff format --check` + `djlint templates/ --check` |
| `ci-tests.yml` | push（非 `v*` tag）/ PR | `pytest` + 覆盖率上传 Codecov + Docker 权限集成测试 |
| `docs.yml` | PR 合并到 `main` | 部署 VitePress 到 GitHub Pages |
| Docker 发布 | 推 `v*` tag | 构建多架构镜像推到 Docker Hub |

---

## pre-commit 钩子

仓库内置 `.githooks/pre-commit`，安装后每次 `git commit` 自动跑 `ruff check` + `ruff format --check` + 全量 `pytest`：

```bash
git config core.hooksPath .githooks      # 跨平台
```

紧急情况下可用 `git commit --no-verify` 跳过。加速测试：`PRECOMMIT_TEST_ARGS="-x tests/services" git commit`。
