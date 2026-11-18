#!/bin/bash

set -e

cd /home/admin/agentlens

# 加载 start.sh 写入的环境变量
if [ -f .supervisor.env ]; then
  # shellcheck disable=SC1091
  source .supervisor.env
fi

# 确保日志目录存在且可写
sudo mkdir -p /home/admin/logs/agentlens
sudo chown admin:admin /home/admin/logs/agentlens

exec python3 -m uvicorn --factory agentlens.main:build_app --host 0.0.0.0 --port 8080
