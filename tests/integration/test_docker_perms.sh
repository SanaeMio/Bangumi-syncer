#!/bin/bash
# ==============================================================================
# 测试用例：验证 Docker 容器的 PUID/PGID 权限自动修复功能
# 目标：确保当用户从旧版本(Root运行)升级到新版本(Appuser运行)时，挂载文件的权限能被自动修正
# 本地运行: docker build -t bangumi-syncer:test . && chmod +x ./tests/integration/test_docker_perms.sh && ./tests/integration/test_docker_perms.sh
# ==============================================================================

set -e  # 遇到错误立即退出

# 定义变量
IMAGE_NAME="${1:-bangumi-syncer:test}" # 默认镜像名，支持传参覆盖
TEST_VOL_NAME="bangumi_syncer_test_vol_$(date +%s)"
TARGET_PUID=1000
TARGET_PGID=1000

echo "🔍 开始运行 Docker 权限兼容性测试..."
echo "Target Image: $IMAGE_NAME"

# 清理函数
cleanup() {
    echo "🧹 清理测试环境..."
    docker rm -f syncer_test_container >/dev/null 2>&1 || true
    docker volume rm $TEST_VOL_NAME >/dev/null 2>&1 || true
}
# 注册清理钩子，脚本退出时自动清理
trap cleanup EXIT

# ------------------------------------------------------------------------------
# 步骤 1: 模拟“旧版本”环境
# 创建一个 Docker Volume，并用 Alpine (Root用户) 在里面创建一个 root:root 的文件
# ------------------------------------------------------------------------------
echo "👉 [Step 1] 模拟旧版本数据 (Root Owner)..."
docker volume create $TEST_VOL_NAME >/dev/null

# 强行创建一个权限为 600 (只有Root可读写) 的文件
docker run --rm -v $TEST_VOL_NAME:/data alpine sh -c \
    "echo 'legacy_data' > /data/old_config.json && chown 0:0 /data/old_config.json && chmod 600 /data/old_config.json"

# 验证一下真的是 Root 拥有的
PRE_UID=$(docker run --rm -v $TEST_VOL_NAME:/data alpine stat -c '%u' /data/old_config.json)
if [ "$PRE_UID" != "0" ]; then
    echo "❌ 测试环境准备失败：无法创建 Root 权限文件。"
    exit 1
fi
echo "✅ 旧数据准备完毕，文件 Owner 为 Root (UID=0)。"

# ------------------------------------------------------------------------------
# 步骤 2: 启动“新版本”容器
# 挂载同一个 Volume，并指定 PUID/PGID，观察 entrypoint.sh 是否工作
# ------------------------------------------------------------------------------
echo "👉 [Step 2] 启动新版本容器 (PUID=$TARGET_PUID)..."

docker run -d --name syncer_test_container \
    -v $TEST_VOL_NAME:/app/data \
    -e PUID=$TARGET_PUID \
    -e PGID=$TARGET_PGID \
    $IMAGE_NAME

# 等待 entrypoint.sh 执行 (通常几秒钟足够，根据实际情况调整)
echo "⏳ 等待容器初始化和权限修复..."
sleep 5

# 检查容器是否存活
if ! docker ps | grep -q syncer_test_container; then
    echo "❌ 测试失败：容器启动后意外退出！可能是权限错误导致的 Crash。"
    echo "=== 容器日志 ==="
    docker logs syncer_test_container
    exit 1
fi

# ------------------------------------------------------------------------------
# 步骤 3: 验证结果
# 检查文件的 Owner 是否变成了指定的 PUID
# ------------------------------------------------------------------------------
echo "👉 [Step 3] 验证权限修复结果..."

# 再次使用 Alpine 挂载 Volume 查看文件属性（不要在宿主机直接看，因为跨平台表现不一致）
POST_UID=$(docker run --rm -v $TEST_VOL_NAME:/data alpine stat -c '%u' /data/old_config.json)

if [ "$POST_UID" == "$TARGET_PUID" ]; then
    echo "🎉 测试通过！..."
    exit 0
else
    echo "❌ 测试失败！文件 Owner 仍然是 $POST_UID (预期: $TARGET_PUID)。"
    echo "=== 🔴 容器日志 (Debug) ===" 
    docker logs syncer_test_container   # <--- 查看 entrypoint 是否报错或打印了 "配置用户权限..."
    echo "=========================="
    exit 1
fi