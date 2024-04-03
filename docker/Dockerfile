# 使用 Python 3.9 slim 环境作为基础镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 复制上级目录中的 utils 文件夹、bangumi_sync.py、config.ini 到工作目录
COPY ../utils ./utils
COPY ../bangumi_sync.py .
COPY ../config.ini .

# 安装依赖
RUN pip install requests fastapi pydantic uvicorn[standard]

# 暴露端口8000
EXPOSE 8000

# 启动应用
CMD ["uvicorn", "bangumi_sync:app", "--host", "0.0.0.0", "--port", "8000"]
