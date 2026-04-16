<!-- 请务必在创建PR前，在右侧 Labels 选项中加上label的其中一个: [feature]、[bug] 。以便于Actions自动生成Releases时自动对PR进行归类。-->

## 📝 PR 描述

### 变更类型
<!-- 请从以下选项中选择保留本次变更类型 -->
✨ 新功能 (feature)
🐛 Bug 修复 (bug)
🚀 优化/重构 (perf/refactor)
📚 文档更新 (docs)
🧰 维护相关 (chore)

### 变更内容
<!-- 简要描述本次 PR 的主要变更 -->


### 相关 Issue
<!-- 如果有相关 Issue，请在此引用，例如: Closes #123 -->


## 📋 提交前检查清单
<!-- 提交前请逐项勾选：将 `- [ ]` 改为 `- [x]`，或在 GitHub 编辑器的预览中直接勾选。未勾选项视为尚未完成。 -->

### 规范与静态检查
<!-- 请在提交 PR 前完成以下检查 -->
- [ ] 已运行 `uv run ruff check .` 并修复所有问题
- [ ] 已运行 `uv run ruff format .` 格式化代码
- [ ] 如有页面模板变更，已运行 `uv run djlint templates/ --reformat` 格式化模板

### 测试
- [ ] 已运行 `uv run pytest tests/ --cov=app --cov-report=term`且全部通过
- [ ] 新功能已在本地测试通过
- [ ] 现有功能未受影响

## 🔍 额外说明
<!-- 其他需要审查者注意的事项 -->
