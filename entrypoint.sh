#!/bin/bash
set -e

# ============================================================================
# 用户和权限处理（仅当以root身份运行时执行）
# ============================================================================

# 如果当前用户是root（UID=0），则处理用户切换
if [ "$(id -u)" = "0" ]; then
    # 默认用户ID和组ID
    PUID=${PUID:-1000}
    PGID=${PGID:-1000}

    echo "配置用户权限: UID=${PUID}, GID=${PGID}"

    # 检查并修改用户组ID（如果PGID不等于默认值）
    CURRENT_GID=$(id -g appuser)
    if [ "${CURRENT_GID}" != "${PGID}" ]; then
        echo "需调整用户组ID为 ${PGID}..."
        
        # 1. 检查目标 GID 是否已被占用
        EXISTING_GROUP_NAME=$(getent group ${PGID} | cut -d: -f1)
        
        if [ -n "${EXISTING_GROUP_NAME}" ]; then
            # --- 分支 A: 组已存在 (例如 users 组) ---
            echo "注意: GID ${PGID} 已被系统组 '${EXISTING_GROUP_NAME}' 占用。"
            echo "策略: 不修改组定义，直接将 appuser 的主组改为 '${EXISTING_GROUP_NAME}'。"
            
            # 使用 usermod -g 直接修改用户的主组
            usermod -g ${PGID} appuser
        else
            # --- 分支 B: 组不存在 (安全) ---
            echo "策略: 目标 GID 未占用，修改 appuser 组 ID。"
            groupmod -g ${PGID} appuser
        fi
    fi

    # 检查并修改用户ID（如果PUID不等于默认值）
    if [ "$(id -u appuser)" != "${PUID}" ]; then
        echo "调整用户ID为 ${PUID}..."
        usermod -u ${PUID} appuser
    fi

    # 确保挂载目录的所有权正确（以root身份运行，可以修改目录所有权）
    # 注意：指定 PGID 时，容器启动时会尝试将数据目录的所有权修改为该 GID。
    echo "设置目录所有权..."
    chown -R ${PUID}:${PGID} /app/config /app/logs /app/data /app/config_backups 2>/dev/null || true

    # 切换到非root用户执行后续操作
    echo "切换到用户 appuser (UID=${PUID}, GID=${PGID}) 执行应用..."
    exec gosu appuser "$0" "$@"
    # 注意：上面的 exec 会替换当前进程，所以下面的代码只有在没有切换时才会执行
    # 实际上，gosu 会执行相同的脚本，但以 appuser 身份
fi

# ============================================================================
# 以下代码以非root用户身份执行（可能是appuser或其他用户）
# ============================================================================

# 注意：因为 Dockerfile 里已经预创建了目录，这里的 mkdir -p 只是防御性编程，不会报错
mkdir -p /app/config /app/logs /app/data /app/config_backups

# 检查配置文件是否存在，不存在则从模板复制
if [ ! -f "/app/config/config.ini" ]; then
    echo "配置文件不存在，从模板创建..."
    cp /app/config.ini.template /app/config/config.ini

    # Docker环境下自动调整路径配置
    echo "调整Docker环境路径配置..."
    # 注意：确保 appuser 对 config.ini 有写权限，我们已经设置过所有权
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