# Justfile

set shell := ["bash", "-uc"]

# 获取当前项目根目录的名称 (e.g., "my-project")
project_name := `basename "$PWD"`
# 定义同级的 worktree 根目录 (e.g., "../my-project.worktrees")
worktree_root := "../" + project_name + ".worktrees"

# === 1. Environment & Dependencies (uv) ===

install:
    @echo "🚀 Syncing environment..."
    uv sync

add package:
    uv add {{package}}

# 启动开发服务器 (假设入口是 app/main.py 或 uvicorn)
run:
    @echo "▶️ Starting application..."
    uv run uvicorn app.main:app --reload

# === 2. Worktree Management (Sibling Isolation) ===

# [Step 1] 创建完全隔离的新功能环境
# Usage: just new-feature user-login
new-feature name:
    @echo "🌳 Creating sibling worktree for isolation..."
    @echo "   Project: {{project_name}}"
    @echo "   Location: {{worktree_root}}/{{name}}"
    
    # 1. 创建 git worktree (在同级目录)
    git worktree add "{{worktree_root}}/{{name}}" -b feature/{{name}}
    
    # 2. 复制 spec 模板到新环境 (保持 spec-kit 结构)
    # 注意：确保 .specify/templates 存在
    mkdir -p "{{worktree_root}}/{{name}}/specs/{{name}}"
    cp .specify/templates/spec.md "{{worktree_root}}/{{name}}/specs/{{name}}/spec.md"
    
    # 3. 初始化新环境的 uv (可选，也可以让用户进去后自己跑)
    cd "{{worktree_root}}/{{name}}" && uv sync
    
    @echo ""
    @echo "✅ Isolated Environment Ready!"
    @echo "👉 Please run: cd {{worktree_root}}/{{name}} && just install"

# 清理已完成的 Worktree
# Usage: just clean user-login
clean name:
    @echo "🧹 Removing worktree: {{worktree_root}}/{{name}}"
    git worktree remove "{{worktree_root}}/{{name}}"
    # 尝试删除分支 (如果已合并)
    git branch -d feature/{{name}} || echo "⚠️ Branch feature/{{name}} not deleted (might be unmerged or active)."

# 列出所有并行的开发环境
list:
    git worktree list

# === 3. Code Quality (The Astral Loop) ===

lint:
    uv run ruff check --fix .
    uv run ruff format .

types:
    uv run ty check .

test:
    uv run pytest

# 运行 E2E 测试 (Playwright)
test-e2e:
    uv run pytest -m "not unit" 

check: lint types test

# === 4. Spec-Driven Flow ===

plan name:
    cp .specify/templates/plan.md specs/{{name}}/plan.md

tasks name:
    cp .specify/templates/tasks.md specs/{{name}}/tasks.md

# === 5. Ops & Docker (SRE Context) ===

docker-build:
    docker build -t {{project_name}}:latest .

docker-up:
    docker compose up -d

docker-down:
    docker compose down