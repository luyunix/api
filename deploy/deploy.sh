#!/bin/bash
# ECS 服务器上的部署脚本
# 用法: ./deploy.sh <镜像标签>
# 云效流水线会通过 SSH 在 ECS 上执行此脚本

set -e

APP_NAME="faber-api"
IMAGE_TAG="${1:-latest}"
ACR_REGISTRY="${ACR_REGISTRY:-registry.cn-hangzhou.aliyuncs.com}"
ACR_NAMESPACE="${ACR_NAMESPACE:-faber}"
FULL_IMAGE="${ACR_REGISTRY}/${ACR_NAMESPACE}/${APP_NAME}:${IMAGE_TAG}"
COMPOSE_FILE="/opt/faber/docker-compose.yml"
ENV_FILE="/opt/faber/.env"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始部署 ${FULL_IMAGE}"

# 1. 确保目录存在
mkdir -p /opt/faber

# 2. 登录阿里云容器镜像服务
if [ -n "$ACR_USERNAME" ] && [ -n "$ACR_PASSWORD" ]; then
    echo "$ACR_PASSWORD" | docker login --username="$ACR_USERNAME" --password-stdin "$ACR_REGISTRY"
fi

# 3. 拉取最新 API 镜像
docker pull "$FULL_IMAGE"

# 4. 预拉取沙箱镜像，避免首次请求时超时
SANDBOX_IMAGE="${SANDBOX_IMAGE:-${ACR_REGISTRY}/${ACR_NAMESPACE}/faber-sandbox:latest}"
if [ -n "$SANDBOX_IMAGE" ]; then
    echo "预拉取沙箱镜像 ${SANDBOX_IMAGE} ..."
    docker pull "$SANDBOX_IMAGE" || echo "沙箱镜像拉取失败，将在首次请求时重试"
fi

# 5. 如果存在旧容器则停止
if [ -f "$COMPOSE_FILE" ]; then
    cd /opt/faber
    docker compose down || true
fi

# 6. 拷贝最新编排文件（云效流水线会先把仓库文件同步到 /opt/faber）
# 这里假设 docker-compose.yml 和 config.yaml 已经通过流水线 SCP 到 /opt/faber

# 7. 创建 Docker 网络（供沙箱容器使用）
docker network inspect faber-network >/dev/null 2>&1 || docker network create faber-network

# 8. 启动服务
cd /opt/faber
export IMAGE_TAG
export FULL_IMAGE
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

# 9. 健康检查
MAX_RETRIES=30
RETRY_INTERVAL=5
echo "等待服务健康检查通过..."
for i in $(seq 1 $MAX_RETRIES); do
    if curl -sf http://localhost:8000/api/status >/dev/null 2>&1; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 服务健康检查通过 ✓"
        exit 0
    fi
    echo "第 ${i}/${MAX_RETRIES} 次健康检查失败，${RETRY_INTERVAL}s 后重试..."
    sleep $RETRY_INTERVAL
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 服务健康检查失败 ✗"
docker compose -f "$COMPOSE_FILE" logs --tail=100 api
exit 1
