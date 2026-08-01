---
title: 📦 开发环境
order: 8
---

# 📦 开发环境

## 技术栈

- **包管理**：[uv](https://docs.astral.sh/uv/)
- **静态检查**：[Ruff](https://docs.astral.sh/ruff/)（Python）、[djLint](https://www.djlint.com/)（Jinja 模板）
- **文档**：[VitePress](https://vitepress.dev/)

---

## 安装与运行

```bash
# 安装依赖
uv sync --group dev

# 运行开发服务器
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# 或用 start.bat（Windows）
```

### 配置文件

首次运行会自动从 `config.example.ini` 复制到 `config.ini`。开发时可复制为 `config.dev.ini`，程序会优先读取（避免覆盖真实 `config.ini`）。

配置路径查找顺序：

1. `CONFIG_FILE` 环境变量
2. `/app/config/config.ini`（Docker 挂载）
3. `config.dev.ini`（开发）
4. `config.ini`（默认）

---

## 代码风格

以 [pyproject.toml](https://github.com/SanaeMio/Bangumi-syncer/blob/main/pyproject.toml) 中的配置为准：

- **Ruff**：`target-version = "py39"`，`line-length = 88`，规则集 `E/W/F/I/UP/B`
- **djLint**：`profile = "jinja"`，`indent = 4`，排除 `templates/config/_.*\.html` 子模板
- **import 顺序**：Ruff isort 自动整理

提交前自检（与 CI 对齐）：

```bash
uv run ruff check .
uv run ruff format .
uv run djlint templates/ --reformat    # 仅改了模板时
uv run pytest tests/ --cov=app --cov-report=term
```

---

## 依赖管理

### 新增运行时依赖

修改 `pyproject.toml` 的 `[project] dependencies`，然后：

```bash
uv lock                                    # 更新锁文件
uv export --format requirements.txt --no-dev -o requirements.txt   # 同步 requirements.txt
```

`requirements.txt` 必须与 `pyproject.toml` 同步提交。

### 新增开发依赖

修改 `[dependency-groups] dev`，然后 `uv sync --group dev`。开发依赖**不需要**更新 `requirements.txt`。

---

## 文档协作

用户文档位于 `docs/`，使用 VitePress 构建。

```bash
cd docs
npm install        # 首次
npm run docs:dev   # 启动开发服务器
```

- 配置文件：`docs/.vitepress/config.ts`（侧边栏顺序由 `manualSortFileNameByPriority` 数组控制）
- 配图：放 `docs/public/images/`，Markdown 用根路径引用 `![](/images/overview/xxx.png)`

PR 合并到 `main` 后，`docs.yml` 工作流自动部署到 GitHub Pages。

---

## 安全与敏感信息

- **勿将 Token / 密码 / 私钥写入仓库**，`config.ini` 已加入 `.gitignore`
- 敏感字段（如 `access_token`、`smtp_password`）写入 INI 时自动加密（`config_secret_crypto.py`）
- Web 界面附加 CSP / `X-Frame-Options: DENY` / `X-Content-Type-Options: nosniff` 响应头（见 `app/main.py` 的 `csp_middleware`）
- 登录限流：失败次数超阈值后锁定账号（`SecurityManager`）
