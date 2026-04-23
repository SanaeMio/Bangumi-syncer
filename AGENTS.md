# AGENTS.md

面向 AI 编码助手与本仓库协作者的快速上下文（人类贡献流程仍以 [README.md](README.md) 与 [CONTRIBUTING.md](CONTRIBUTING.md) 为准）。

## 项目概述

Bangumi-syncer 将常见媒体库（Plex、Emby、Jellyfin、Trakt、飞牛等）的观看进度同步到 [Bangumi（番组计划）](https://bgm.tv/) 官方 API。提供基于 **FastAPI** 的 Web 管理与同步接口，默认本地访问 `http://localhost:8000`。

## 技术栈与环境

- **Python**：`>=3.9`（见 [pyproject.toml](pyproject.toml) 中 `requires-python`）。
- **运行时**：FastAPI、Uvicorn、Jinja2、Pydantic、APScheduler 等（依赖见 `pyproject.toml`）。
- **包管理**：推荐 [uv](https://docs.astral.sh/uv/)。

## 仓库地图

| 路径 | 说明 |
| --- | --- |
| `app/` | 应用代码：`api/` 路由、`services/` 业务与调度、`core/` 配置与基础设施、`models/`、`utils/` |
| `app/main.py` | FastAPI 应用入口，`uvicorn app.main:app` |
| `tests/` | 单元测试与 API 测试 |
| `docs/` | 用户文档（VitePress），配图在 `docs/public/images/` |
| `templates/`、`static/` | Jinja 模板与静态资源 |
| `Dockerfile`、`entrypoint.sh` | 容器构建与启动 |
| `.github/workflows/` | CI：如 `lint.yml`、`ci-tests.yml`、Docker 相关工作流、`docs.yml` |

## 安装、构建与常用命令

在项目根目录执行：

```bash
uv sync --group dev
```

```bash
# 代码风格与静态检查（CI 中 lint 工作流使用 ruff format --check）
uv run ruff check .
uv run ruff format .

# 仅当修改了 Jinja 模板时
uv run djlint templates/ --reformat

# 单元测试与覆盖率
uv run pytest tests/ --cov=app --cov-report=term
```

**仅当修改了 `pyproject.toml` 中的运行时依赖时**：在 `uv lock` 之后执行下面命令生成根目录 `requirements.txt` 并一并提交，避免与 README、用户文档里的 `pip install -r requirements.txt` 不同步。

```bash
uv export --format requirements.txt --no-dev -o requirements.txt
```

本地启动 Web 服务（等价于仓库内 `start.bat` / 镜像内 uvicorn 目标模块）：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

更完整的说明（Fork、PR、Docker 集成测试路径等）见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 测试说明

- 行为或逻辑变更应**尽量附带或更新** `tests/` 下相关用例；难以覆盖时请在 PR 中说明，并优先用 Mock 等保持稳定。
- CI：`ci-tests` 工作流运行 `pytest tests/` 并上传覆盖率；`lint` 工作流负责 Ruff；模板由 djLint 单独检查。
- 若改动 **Dockerfile、entrypoint、镜像内权限或启动方式**，请关注 CONTRIBUTING 中提到的集成脚本 `tests/integration/test_docker_perms.sh` 及 Docker 相关 workflow。

## 代码风格与架构约定

- **风格**：以 [pyproject.toml](pyproject.toml) 中 **Ruff**、**djlint** 配置为准，勿在本文重复粘贴规则全文。
- **分层**：新 HTTP 接口放在 `app/api/`；复杂业务放在 `app/services/`（可参考现有 `sync_service`、`mapping_service` 及各媒体子包）。体量小的只读端点可参考 [`app/api/health.py`](app/api/health.py) 的组织方式。
- **文档**：配置项或面向用户的行为变更需同步更新 `docs/` 中对应 Markdown；图片放在 `docs/public/images/`，文内用根路径如 `![](/images/overview/xxx.png)`（见 CONTRIBUTING）。
- **协作**：与邻近文件保持一致的命名与注释习惯；避免无关大范围格式化或重命名。

## 安全与敏感信息

勿将 Bangumi Token、密码、私钥等**写入仓库**或提交到 Git。运行时密钥通过应用配置与环境管理；细节见在线文档与 CONTRIBUTING。

## PR 与提交前自检

推送或打开 PR 前建议至少执行上文 **Ruff + pytest**（若改模板则加 **djlint**）。**若改运行时依赖**，在 `uv lock` 后按上文单独一节执行 **`uv export` 并提交 `requirements.txt`**。单个 PR 尽量聚焦单一目的，便于审查与回滚。