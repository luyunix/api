#!/bin/bash
# 构建并推送 faber-api 镜像到阿里云 ACR
# 用法: ./scripts/build-api.sh [镜像标签]

set -e

TAG="${1:-latest}"
IMAGE_NAME="faber-api"

# 请根据实际 ACR 信息修改
ACR_REGISTRY="${ACR_REGISTRY:-registry.cn-hangzhou.aliyuncs.com}"
ACR_NAMESPACE="${ACR_NAMESPACE:-faber}"
FULL_IMAGE="${ACR_REGISTRY}/${ACR_NAMESPACE}/${IMAGE_NAME}:${TAG}"

echo "Building ${FULL_IMAGE} ..."
docker build -t "${IMAGE_NAME}:${TAG}" .
docker tag "${IMAGE_NAME}:${TAG}" "${FULL_IMAGE}"

echo "Pushing ${FULL_IMAGE} ..."
docker push "${FULL_IMAGE}"

echo "Done: ${FULL_IMAGE}"
