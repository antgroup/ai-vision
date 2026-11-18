#!/usr/bin/env bash

# 环境初始化脚本
# 该脚本在容器启动时被 source

export PATH="/opt/venv/bin:$PATH"
export DJANGO_SETTINGS_MODULE=aivision_backend.settings
export PYTHONUNBUFFERED=1

# 如果有 .env 文件，加载环境变量
if [ -f /home/admin/aivision/.env ]; then
    set -a
    source /home/admin/aivision/.env
    set +a
fi