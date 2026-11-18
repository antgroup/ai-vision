#!/bin/bash
# 启动 Weaver Live Check 作为 OTLP gRPC 代理
# 用法: ./start_live_check.sh [backend_endpoint]
#
# 架构:
#┌─────────────────┐     ┌─────────────────────┐     ┌─────────────────┐
# │  cchooks/       │     │  Weaver live-check  │     │  Langfuse       │
# │  cdxhooks       │────▶│  (验证 + 转发)      │────▶│  (原始数据)     │
# └─────────────────┘     └─────────────────────┘     └─────────────────┘
#┌─────────────────────┐
#                               │
#                               ▼ 违规报告
#                        ┌─────────────────┐
#                        │  OTLP Logs      │
#                        │  (告警/日志)    │
#                        └─────────────────┘

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEAVER_DIR="$(dirname "$SCRIPT_DIR")"
SEMCONV_DIR="$WEAVER_DIR/semconv"
ADVICE_POLICIES_DIR="$WEAVER_DIR/policies"

# 默认配置
OTLP_GRPC_PORT=${OTLP_GRPC_PORT:-4317}
OTLP_LOGS_ENDPOINT=${1:-"${OTLP_LOGS_ENDPOINT:-http://localhost:4318}"}
ADMIN_PORT=${ADMIN_PORT:-4320}
INACTIVITY_TIMEOUT=${INACTIVITY_TIMEOUT:-10}

echo "=== 启动 Weaver Live Check ==="
echo ""
echo "配置:"
echo "  OTLP gRPC 端口: $OTLP_GRPC_PORT"
echo "  后端端点: $OTLP_LOGS_ENDPOINT"
echo "  Admin 端口: $ADMIN_PORT"
echo "  超时: ${INACTIVITY_TIMEOUT}s"
echo ""

# 检查 weaver 是否安装
if ! command -v weaver &> /dev/null; then
    echo "错误: weaver 未安装"
    echo "请运行: cargo install weaver"
    exit 1
fi

# 检查端口是否被占用
if lsof -Pi :$OTLP_GRPC_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "警告: 端口 $OTLP_GRPC_PORT 已被占用"
    echo "请先停止占用该端口的进程，或设置 OTLP_GRPC_PORT 环境变量"
    lsof -Pi :$OTLP_GRPC_PORT -sTCP:LISTEN
    exit 1
fi

echo "启动 live-check..."
echo "按Ctrl+C 停止"
echo ""
echo "停止服务: curl http://localhost:$ADMIN_PORT/stop"
echo ""

# 启动live-check
exec weaver registry live-check \
    -r "$SEMCONV_DIR" \
    --advice-policies "$ADVICE_POLICIES_DIR" \
    --otlp-grpc-port $OTLP_GRPC_PORT \
    --admin-port $ADMIN_PORT \
    --inactivity-timeout $INACTIVITY_TIMEOUT \
    --emit-otlp-logs \
    --otlp-logs-endpoint "$OTLP_LOGS_ENDPOINT"