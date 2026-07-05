#!/bin/bash
# 构建并推送 faber-sandbox 镜像到阿里云 ACR
# 用法: ./scripts/build-sandbox.sh [镜像标签]
# 注意：沙箱源码位于 /Users/lyn/Desktop/faber/sandbox（api 项目的平级目录）

set -e

TAG="${1:-latest}"
IMAGE_NAME="faber-sandbox"

# 请根据实际 ACR 信息修改
ACR_REGISTRY="${ACR_REGISTRY:-registry.cn-hangzhou.aliyuncs.com}"
ACR_NAMESPACE="${ACR_NAMESPACE:-faber}"
FULL_IMAGE="${ACR_REGISTRY}/${ACR_NAMESPACE}/${IMAGE_NAME}:${TAG}"

SANDBOX_SOURCE_DIR="${SANDBOX_SOURCE_DIR:-../sandbox}"

echo "Building ${FULL_IMAGE} from ${SANDBOX_SOURCE_DIR} ..."
docker build -t "${IMAGE_NAME}:${TAG}" -f "${SANDBOX_SOURCE_DIR}/Dockerfile" "${SANDBOX_SOURCE_DIR}/"
docker tag "${IMAGE_NAME}:${TAG}" "${FULL_IMAGE}"

echo "Pushing ${FULL_IMAGE} ..."
docker push "${FULL_IMAGE}"

echo "Done: ${FULL_IMAGE}"
