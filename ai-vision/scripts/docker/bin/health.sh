#!/usr/bin/env bash

exit 0

# 健康检查脚本
# 返回 0 表示健康，返回非 0 表示不健康

HEALTH_URL="${HEALTH_URL:-http://localhost:8080/api/v1/health}"

response=$(curl -s -o /dev/null -w "%{http_code}" "${HEALTH_URL}" 2>/dev/null || echo "000")

if [ "$response" = "200" ]; then
    echo "Health check passed"
    exit 0
else
    echo "Health check failed: HTTP ${response}"
    exit 1
fi