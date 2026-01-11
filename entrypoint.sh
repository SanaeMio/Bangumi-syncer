#!/bin/bash
set -e

# 注意：因为 Dockerfile 里已经预创建了目录，这里的 mkdir -p 只是防御性编程，不会报错
mkdir -p /app/config /app/logs /app/data /app/config_backups

# 检查配置文件是否存在，不存在则从模板复制
if [ ! -f "/app/config/config.ini" ]; then
    echo "配置文件不存在，从模板创建..."
    cp /app/config.ini.template /app/config/config.ini
    
    # Docker环境下自动调整路径配置
    echo "调整Docker环境路径配置..."
    # 注意：确保 appuser 对 config.ini 有写权限，我们在 Dockerfile 里已经 chown 了
    sed -i "s|local_cache_path = ./bangumi_data_cache.json|local_cache_path = /app/data/bangumi_data_cache.json|g" /app/config/config.ini
    sed -i "s|log_file = ./log.txt|log_file = /app/logs/log.txt|g" /app/config/config.ini
    
    echo "配置文件已创建并调整：/app/config/config.ini"
fi

# 检查自定义映射文件是否存在
if [ ! -f "/app/config/bangumi_mapping.json" ]; then
    echo "自定义映射文件不存在，从模板创建..."
    cp /app/bangumi_mapping.json.template /app/config/bangumi_mapping.json
    echo "自定义映射文件已创建"
fi

# 检查邮件通知模板
if [ ! -f "/app/config/email_notification.html" ]; then
    echo "邮件通知模板不存在，从默认模板创建..."
    # 注意：原 Dockerfile 并没有把 templates 目录复制到 /app/templates，
    # 如果代码里依赖这个，请确保 Dockerfile 里 COPY 了 templates
    if [ -f "/app/templates/email_notification.html" ]; then
        cp /app/templates/email_notification.html /app/config/email_notification.html
        echo "邮件通知模板已创建"
    else
        echo "警告：源模板文件未找到，跳过创建"
    fi
fi

# 执行 Docker 传递进来的 CMD 命令 (比如 uvicorn ...)
# "$@" 代表 Dockerfile 中的 CMD 参数
echo "启动应用..."
exec "$@"