# 贡献指南

感谢你愿意为 Bangumi-syncer 花时间。无论你是报告问题、讨论想法还是提交代码，我们都很欢迎。

## 参与方式

- **报告缺陷或需求**：在 [Issues](https://github.com/SanaeMio/Bangumi-syncer/issues) 中新建一条，尽量说明环境（系统、Python 或 Docker）、复现步骤和期望行为。
- **提交代码**：先 Fork 本仓库，在分支上完成修改后发起 [Pull Request](https://github.com/SanaeMio/Bangumi-syncer/pulls)。若对应已有 Issue，请在 PR 描述里引用（例如 `Closes #123`）。

## 开发环境

- **Python**：3.9 及以上。
- **包管理**：推荐使用 [uv](https://docs.astral.sh/uv/)。

```bash
# 安装 Python 3.9+ 后，在项目根目录执行
uv sync --group dev
```

若尚未安装 uv，可参考官方文档安装后再执行上述命令。

## 单元测试

**建议在同一 PR 中与代码改动同步提交相关的单元测试**（或保证同一 PR 内包含对应测试提交），而不是长期只合并实现、测试另开 PR。这样便于：

- **验证功能**：用可执行的测试描述预期行为，审查者更容易确认改动是否符合意图。
- **防止回归**：后续修改同一模块时，CI 会尽快发现破坏既有行为的变更。

若受外部依赖等限制难以编写完整测试，请在 PR 中说明原因，并尽量通过 Mock、桩对象等方式补充可稳定运行的最小测试集。

## 提交前自检（与 CI 对齐）

在推送或打开 PR 前，建议在本地执行：

```bash
# 代码风格与静态检查
uv run ruff check .
uv run ruff format .

# 若修改了 Jinja 模板
uv run djlint templates/ --reformat

# 单元测试与覆盖率
uv run pytest tests/ --cov=app --cov-report=term
```

说明：

- **Ruff**：`lint` 工作流会对全仓库做 `ruff check` 与 `ruff format --check`；模板目录由 **djLint** 单独检查。
- **测试**：`ci-tests` 工作流会跑 `pytest tests/` 并生成覆盖率 XML 上传 Codecov；本地至少应保证测试通过。
- **Docker**：合并前 CI 还会构建镜像并跑集成脚本 `tests/integration/test_docker_perms.sh`；若你的改动涉及镜像权限或启动方式，可在本地用同仓库的 Dockerfile 构建后自行验证。

**仅当本次 PR 修改了 `pyproject.toml` 中的运行时依赖时**：在 `uv lock` 更新锁文件之后，再执行下面命令重新生成根目录的 `requirements.txt`，并与改动一并提交，以免与 README、快速开始文档中的 `pip install -r requirements.txt` 不同步。

```bash
uv export --format requirements.txt --no-dev -o requirements.txt
```

## 代码与协作习惯

- 功能新增或 bug 修复尽量**附带或同步更新**单元测试，与上文「单元测试」一节一致。
- 改动尽量聚焦单一目的（一个 PR 解决一类问题），便于审查与回滚。
- 保持与现有代码风格一致；不确定时以同目录邻近文件为准。
- 避免无关格式化或大范围重命名，除非单独说明并与维护者达成一致。

## 文档

用户文档位于仓库根目录的 `docs/`，使用 [VitePress](https://vitepress.dev/) 构建。

如果本次修改内容有配置项变更，或其他需要补充到文档里的内容，请同时更新对应md文件。

文档内配图请放在 [`docs/public/images/`](docs/public/images/) 下，Markdown 使用根路径引用，例如 `![](/images/overview/xxx.png)`。

PR合并到 `main` 后，工作流 [.github/workflows/docs.yml](.github/workflows/docs.yml) 会自动将站点更新部署到 [GitHub Pages](https://sanaemio.github.io/Bangumi-syncer/)。

