#!/bin/bash
# ==============================================================================
# 测试用例：验证 Docker 容器的 PUID/PGID 权限自动修复功能
# 目标：确保当用户从旧版本(Root运行)升级到新版本(Appuser运行)时，挂载文件的权限能被自动修正
# 新增测试：验证当指定已有 GID（如系统 users 组）时，容器能正常启动
# 本地运行: docker build -t bangumi-syncer:test . && chmod +x ./tests/integration/test_docker_perms.sh && ./tests/integration/test_docker_perms.sh
# ==============================================================================

set -e  # 遇到错误立即退出

# 定义变量
IMAGE_NAME="${1:-bangumi-syncer:test}" # 默认镜像名，支持传参覆盖

echo "🔍 开始运行 Docker 权限兼容性测试..."
echo "Target Image: $IMAGE_NAME"

# 全局变量
CONTAINER_NAMES=()
TEST_VOLUMES=()

# 清理函数
cleanup() {
    echo "🧹 清理测试环境..."
    for container in "${CONTAINER_NAMES[@]}"; do
        docker rm -f "$container" >/dev/null 2>&1 || true
    done
    for volume in "${TEST_VOLUMES[@]}"; do
        docker volume rm "$volume" >/dev/null 2>&1 || true
    done
}
# 注册清理钩子，脚本退出时自动清理
trap cleanup EXIT

# ==============================================================================
# 测试函数 1: 基本权限修复测试
# 验证当从 Root 运行升级到 Appuser 运行时，权限能正确修复
# ==============================================================================
test_basic_permission_fix() {
    echo "🧪 开始测试：基本权限修复功能"

    local TEST_VOL_NAME="bangumi_syncer_test_vol_basic_$(date +%s)"
    local CONTAINER_NAME="syncer_test_container_basic"
    local TARGET_PUID=1000
    local TARGET_PGID=1000

    TEST_VOLUMES+=("$TEST_VOL_NAME")
    CONTAINER_NAMES+=("$CONTAINER_NAME")

    # --------------------------------------------------------------------------
    # 步骤 1: 模拟“旧版本”环境
    # --------------------------------------------------------------------------
    echo "👉 [Step 1] 模拟旧版本数据 (Root Owner)..."
    docker volume create $TEST_VOL_NAME >/dev/null

    # 强行创建一个权限为 600 (只有Root可读写) 的文件
    docker run --rm -v $TEST_VOL_NAME:/data alpine sh -c \
        "echo 'legacy_data' > /data/old_config.json && chown 0:0 /data/old_config.json && chmod 600 /data/old_config.json"

    # 验证一下真的是 Root 拥有的
    local PRE_UID=$(docker run --rm -v $TEST_VOL_NAME:/data alpine stat -c '%u' /data/old_config.json)
    if [ "$PRE_UID" != "0" ]; then
        echo "❌ 测试环境准备失败：无法创建 Root 权限文件。"
        return 1
    fi
    echo "✅ 旧数据准备完毕，文件 Owner 为 Root (UID=0)。"

    # --------------------------------------------------------------------------
    # 步骤 2: 启动“新版本”容器
    # --------------------------------------------------------------------------
    echo "👉 [Step 2] 启动新版本容器 (PUID=$TARGET_PUID, PGID=$TARGET_PGID)..."

    docker run -d --name $CONTAINER_NAME \
        -v $TEST_VOL_NAME:/app/data \
        -e PUID=$TARGET_PUID \
        -e PGID=$TARGET_PGID \
        $IMAGE_NAME

    # 等待 entrypoint.sh 执行
    echo "⏳ 等待容器初始化和权限修复..."
    sleep 5

    # 检查容器是否存活
    if ! docker ps | grep -q $CONTAINER_NAME; then
        echo "❌ 测试失败：容器启动后意外退出！可能是权限错误导致的 Crash。"
        echo "=== 容器日志 ==="
        docker logs $CONTAINER_NAME
        return 1
    fi

    # --------------------------------------------------------------------------
    # 步骤 3: 验证结果
    # --------------------------------------------------------------------------
    echo "👉 [Step 3] 验证权限修复结果..."

    # 再次使用 Alpine 挂载 Volume 查看文件属性
    local POST_UID=$(docker run --rm -v $TEST_VOL_NAME:/data alpine stat -c '%u' /data/old_config.json)

    if [ "$POST_UID" == "$TARGET_PUID" ]; then
        echo "✅ 基本权限修复测试通过！"
        return 0
    else
        echo "❌ 基本权限修复测试失败！文件 Owner 仍然是 $POST_UID (预期: $TARGET_PUID)。"
        echo "=== 🔴 容器日志 (Debug) ==="
        docker logs $CONTAINER_NAME
        echo "=========================="
        return 1
    fi
}

# ==============================================================================
# 测试函数 2: 已有 GID 处理测试
# 验证当指定的 PGID 已被系统组占用时，容器能正常启动
# 注：使用 GID=1 (通常为 daemon 组) 进行测试，保证在 Debian 系统中存在
# ==============================================================================
test_existing_gid() {
    echo "🧪 开始测试：已有 GID 处理功能"

    local TEST_VOL_NAME="bangumi_syncer_test_vol_existing_gid_$(date +%s)"
    local CONTAINER_NAME="syncer_test_container_existing_gid"
    local TARGET_PUID=1000
    local TARGET_PGID=1  # 使用 GID=1，通常为 daemon 组（保证在 Debian 系统中存在）

    TEST_VOLUMES+=("$TEST_VOL_NAME")
    CONTAINER_NAMES+=("$CONTAINER_NAME")

    # --------------------------------------------------------------------------
    # 步骤 1: 模拟“旧版本”环境
    # --------------------------------------------------------------------------
    echo "👉 [Step 1] 模拟旧版本数据 (Root Owner)..."
    docker volume create $TEST_VOL_NAME >/dev/null

    # 强行创建一个权限为 600 (只有Root可读写) 的文件
    docker run --rm -v $TEST_VOL_NAME:/data alpine sh -c \
        "echo 'legacy_data' > /data/old_config.json && chown 0:0 /data/old_config.json && chmod 600 /data/old_config.json"

    # 验证一下真的是 Root 拥有的
    local PRE_UID=$(docker run --rm -v $TEST_VOL_NAME:/data alpine stat -c '%u' /data/old_config.json)
    if [ "$PRE_UID" != "0" ]; then
        echo "❌ 测试环境准备失败：无法创建 Root 权限文件。"
        return 1
    fi
    echo "✅ 旧数据准备完毕，文件 Owner 为 Root (UID=0)。"

    # --------------------------------------------------------------------------
    # 步骤 2: 启动“新版本”容器（使用已被占用的 GID）
    # --------------------------------------------------------------------------
    echo "👉 [Step 2] 启动新版本容器 (PUID=$TARGET_PUID, PGID=$TARGET_PGID)..."
    echo "注意：PGID=$TARGET_PGID (daemon 组) 应已被系统组占用，测试 entrypoint.sh 的处理逻辑"

    docker run -d --name $CONTAINER_NAME \
        -v $TEST_VOL_NAME:/app/data \
        -e PUID=$TARGET_PUID \
        -e PGID=$TARGET_PGID \
        $IMAGE_NAME

    # 等待 entrypoint.sh 执行
    echo "⏳ 等待容器初始化和权限修复..."
    sleep 5

    # 检查容器是否存活
    if ! docker ps | grep -q $CONTAINER_NAME; then
        echo "❌ 测试失败：容器启动后意外退出！可能是 GID 处理逻辑有问题。"
        echo "=== 容器日志 ==="
        docker logs $CONTAINER_NAME
        return 1
    fi

    # --------------------------------------------------------------------------
    # 步骤 3: 验证结果
    # --------------------------------------------------------------------------
    echo "👉 [Step 3] 验证权限修复结果..."

    # 再次使用 Alpine 挂载 Volume 查看文件属性
    local POST_UID=$(docker run --rm -v $TEST_VOL_NAME:/data alpine stat -c '%u' /data/old_config.json)

    if [ "$POST_UID" == "$TARGET_PUID" ]; then
        echo "✅ 已有 GID 处理测试通过！容器能正确处理已被占用的 GID。"
        return 0
    else
        echo "❌ 已有 GID 处理测试失败！文件 Owner 仍然是 $POST_UID (预期: $TARGET_PUID)。"
        echo "=== 🔴 容器日志 (Debug) ==="
        docker logs $CONTAINER_NAME
        echo "=========================="
        return 1
    fi
}

# ==============================================================================
# 主测试执行流程
# ==============================================================================
echo "🚀 开始执行测试套件..."

# 运行测试1：基本权限修复
if ! test_basic_permission_fix; then
    echo "❌ 基本权限修复测试失败！"
    exit 1
fi

# 运行测试2：已有 GID 处理
if ! test_existing_gid; then
    echo "❌ 已有 GID 处理测试失败！"
    exit 1
fi

echo "🎉🎉🎉 所有测试通过！"
echo "✅ 基本权限修复功能正常"
echo "✅ 已有 GID 处理功能正常"