#!/bin/bash
# Weaver Live Check 批量验证脚本
# 用法: ./validate_batch.sh [input.json]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEAVER_DIR="$(dirname "$SCRIPT_DIR")"
TESTDATA_DIR="$WEAVER_DIR/testdata"
SEMCONV_DIR="$WEAVER_DIR/semconv"
ADVICE_POLICIES_DIR="$WEAVER_DIR/policies"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Weaver Live Check 批量验证 ===${NC}"
echo ""

# 检查 weaver 是否安装
if ! command -v weaver &> /dev/null; then
    echo -e "${RED}错误: weaver 未安装${NC}"
    echo "请运行: cargo install weaver"
    exit 1
fi

# 静态验证语义约定
echo -e "${YELLOW}1. 验证语义约定定义...${NC}"
if weaver registry check -r "$SEMCONV_DIR" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ 语义约定定义有效${NC}"
else
    echo -e "${RED}✗ 语义约定定义有误${NC}"
    weaver registry check -r "$SEMCONV_DIR"
    exit 1
fi
echo ""

# 验证函数
validate_file() {
    local file=$1
    local filename=$(basename "$file")
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}验证: $filename${NC}"
    echo ""

    if weaver registry live-check \
        -r "$SEMCONV_DIR" \
        --advice-policies "$ADVICE_POLICIES_DIR" \
        --input-source "$file" \
        --input-format json; then
        return 0
    else
        return 1
    fi
}

# 主逻辑
if [ -n "$1" ]; then
    # 验证指定文件
    if [ -f "$1" ]; then
        validate_file "$1"
    else
        echo -e "${RED}文件不存在: $1${NC}"
        exit 1
    fi
else
    # 验证所有 livecheck_*.json 文件
    echo -e "${YELLOW}2. 验证测试数据...${NC}"
    echo ""

    json_files=$(find "$TESTDATA_DIR" -name "livecheck_*.json" | sort)
    json_count=$(echo "$json_files" | grep -c . || echo "0")

    if [ "$json_count" -eq 0 ]; then
        echo -e "${YELLOW}未找到测试数据文件${NC}"
        echo "请将 Weaver 格式的 JSON 文件放入: $TESTDATA_DIR/"
        echo "文件名格式: livecheck_*.json"
        exit 0
    fi

    total_passed=0
    total_failed=0

    for file in $json_files; do
        if validate_file "$file"; then
            ((total_passed++))
        else
            ((total_failed++))
        fi
        echo ""
    done

    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}=== 批量验证完成 ===${NC}"
    echo "  通过: $total_passed"
    echo "  失败: $total_failed"

    if [ "$total_failed" -gt 0 ]; then
        exit 1
    fi
fi