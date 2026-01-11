# ==========================================
# Stage 1: Builder (依赖构建层)
# ==========================================
FROM python:3.9-slim-bookworm AS builder

# 1. 获取 uv, 使用 uv.lock 确保依赖稳定
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# 2. 环境变量：指定安装到系统目录
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    # 【重点】告诉 uv 直接安装到 /usr/local，而不是创建 .venv
    UV_PROJECT_ENVIRONMENT="/usr/local"

WORKDIR /app

# 3. 安装依赖
# --no-install-project: 只安装依赖，不安装当前项目(因为后面我们会手动COPY app)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev


# ==========================================
# Stage 2: Runtime (运行层)
# ==========================================
FROM python:3.9-slim-bookworm

# 1. 环境变量
# 注意：这里不需要再改 PATH 了，因为 /usr/local/bin 默认就在 PATH 里
ENV PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=true \
    CONFIG_FILE=/app/config/config.ini

WORKDIR /app

# 2. 安全优化：创建非 Root 用户
RUN groupadd -r appuser && useradd -r -g appuser --create-home appuser

# 3. 预创建目录
RUN mkdir -p /app/config /app/logs /app/data /app/config_backups && \
    chown -R appuser:appuser /app

# 4. 【关键步骤】从 Builder 拷贝系统 Python 的包
# 拷贝依赖库 (site-packages)
COPY --from=builder /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages
# 拷贝可执行文件 (如 uvicorn, gunicorn 等脚本)
# 注意：这会覆盖 Runtime 层的 /usr/local/bin，但在同版本 slim 镜像间通常是安全的
COPY --from=builder /usr/local/bin /usr/local/bin

# 5. 拷贝脚本与模版
COPY --chown=appuser:appuser entrypoint.sh /app/entrypoint.sh
COPY --chown=appuser:appuser config.ini /app/config.ini.template
COPY --chown=appuser:appuser bangumi_mapping.json /app/bangumi_mapping.json.template
COPY --chown=appuser:appuser version.py  /app/version.py

RUN chmod +x /app/entrypoint.sh

# 6. 拷贝业务代码
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser templates/ ./templates/
COPY --chown=appuser:appuser static/ ./static/

# 7. 切换用户
USER appuser

# 8. 启动
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
# 直接调用 uvicorn，因为他在 /usr/local/bin 里，且该目录在 PATH 中
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]