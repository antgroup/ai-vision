#!/usr/bin/env bash
set -euo pipefail

# AI Vision 发布脚本
# 用途：构建镜像并推送到镜像仓库

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

: "${IMAGE_REGISTRY:?Set IMAGE_REGISTRY to the target container registry}"
IMAGE_NAMESPACE="${IMAGE_NAMESPACE:-aivision}"
IMAGE_NAME="${IMAGE_NAME:-aivision}"

commit=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
VERSION="${branch}-${commit}-$(date '+%Y%m%d%H%M%S')"

echo "============================================================"
echo " AI Vision Deploy"
echo " Version: ${VERSION}"
echo "============================================================"

cd "${PROJECT_ROOT}"

docker build \
    -f scripts/docker/Dockerfile \
    -t "${IMAGE_REGISTRY}/${IMAGE_NAMESPACE}/${IMAGE_NAME}:${VERSION}" \
    -t "${IMAGE_REGISTRY}/${IMAGE_NAMESPACE}/${IMAGE_NAME}:latest" \
    .

echo ""
echo "Pushing images..."
docker push "${IMAGE_REGISTRY}/${IMAGE_NAMESPACE}/${IMAGE_NAME}:${VERSION}"
docker push "${IMAGE_REGISTRY}/${IMAGE_NAMESPACE}/${IMAGE_NAME}:latest"

echo ""
echo "============================================================"
echo " Deploy complete!"
echo " Image: ${IMAGE_REGISTRY}/${IMAGE_NAMESPACE}/${IMAGE_NAME}:${VERSION}"
echo "============================================================"
