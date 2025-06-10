# 使用 Python 3.9 slim 环境作为基础镜像
FROM python:3.9-slim

# 安装curl用于下载数据
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制requirements.txt并安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY bangumi_sync.py .
COPY utils/ ./utils/

# 复制配置模板
COPY config.ini /app/config.ini.template
COPY bangumi_mapping.json /app/bangumi_mapping.json.template

# 创建启动脚本
RUN echo '#!/bin/bash\n\
set -e\n\
\n\
# 创建必要目录\n\
mkdir -p /app/config /app/logs /app/data\n\
\n\
# 检查配置文件是否存在，不存在则从模板复制\n\
if [ ! -f "/app/config/config.ini" ]; then\n\
    echo "配置文件不存在，从模板创建..."\n\
    cp /app/config.ini.template /app/config/config.ini\n\
    \n\
    # Docker环境下自动调整路径配置\n\
    echo "调整Docker环境路径配置..."\n\
    sed -i "s|local_cache_path = ./bangumi_data_cache.json|local_cache_path = /app/data/bangumi_data_cache.json|g" /app/config/config.ini\n\
    sed -i "s|log_file = ./log.txt|log_file = /app/logs/log.txt|g" /app/config/config.ini\n\
    \n\
    echo "配置文件已创建并调整：/app/config/config.ini"\n\
fi\n\
\n\
# 检查自定义映射文件是否存在，不存在则从模板复制\n\
if [ ! -f "/app/config/bangumi_mapping.json" ]; then\n\
    echo "自定义映射文件不存在，从模板创建..."\n\
    cp /app/bangumi_mapping.json.template /app/config/bangumi_mapping.json\n\
    echo "自定义映射文件已创建：/app/config/bangumi_mapping.json"\n\
fi\n\
\n\
# 确保日志文件存在并有正确权限\n\
touch /app/logs/log.txt\n\
chmod 666 /app/logs/log.txt\n\
\n\
# 显示配置信息用于调试\n\
echo "=== 配置信息 ==="\n\
echo "配置文件: $CONFIG_FILE"\n\
echo "工作目录: $(pwd)"\n\
echo "Python路径: $PYTHONPATH"\n\
ls -la /app/config/ /app/logs/ /app/data/ || true\n\
echo "==============="\n\
\n\
# 启动应用\n\
echo "启动应用..."\n\
exec uvicorn bangumi_sync:app --host 0.0.0.0 --port 8000' > /app/start.sh && chmod +x /app/start.sh

# 设置环境变量
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV CONFIG_FILE=/app/config/config.ini

# 暴露端口8000
EXPOSE 8000

# 使用启动脚本
CMD ["/app/start.sh"] 