# 文档配图目录（`docs/public/images`）

构建后可通过站点根路径引用。明暗模式截图分别为 `screenshot.png` 和 `screenshot-dark.png`。

- GitHub README：用 `#gh-light-mode-only` / `#gh-dark-mode-only` fragment
- 文档站点：用 `<picture>` + `<source media="(prefers-color-scheme: ...)">`

建议子目录：

- `branding/`：Logo、吉祥物、Bangumi 官方图标（`bangumi-icon.png`）等
- `overview/`：简介、总览类截图
- `usage/<功能名>/`：各接入方式的分步截图（文件名建议 `step-01.jpg` 递增）

新增图片请放入本目录并提交仓库，勿再依赖外链图床（除非版权或体积特殊原因）。
