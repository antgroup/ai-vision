#!/usr/bin/env bash
set -e
######################################################################
# start.sh
# 应用业务进程启动脚本
# 需要在此脚本中拉起业务需要的所有进程
#
# 注意：脚本返回非0时，容器启动会失败。
######################################################################

PID_FILE="/var/run/supervisord.pid"
CONFIG_FILE="/etc/supervisor/supervisord.conf"
SERVICE_NAME="app"
APP_LOG_DIR="/home/admin/logs/agentlens"

# supervisord validates stdout_logfile before it starts app.sh. Runtime mounts
# may hide the directory created in the image, so provision it in the hook.
sudo mkdir -p /var/log/supervisor "$APP_LOG_DIR"
sudo chown admin:admin "$APP_LOG_DIR"

# poststart hook 以 `su - admin`（login shell）调用本脚本，
# login shell 会清空环境变量。supervisord 子进程也不继承环境变量。
# 从 /proc/1/environ（容器入口进程）读取关键变量，写入 env 文件供 app.sh 加载。
if [ -z "$AIVISION_ENV" ]; then
  AIVISION_ENV=$(sudo cat /proc/1/environ 2>/dev/null | tr '\0' '\n' | grep '^AIVISION_ENV=' | head -1 | cut -d= -f2- || true)
fi
ENV_FILE="/home/admin/agentlens/.supervisor.env"
echo "AIVISION_ENV=${AIVISION_ENV:-}" > "$ENV_FILE"

supervisord_running=
if [ -e "$PID_FILE" ]; then
  pid=$(cat $PID_FILE)
  if [ -n "$pid" ]; then
    if ps -p "$pid" >/dev/null 2>&1; then
      supervisord_running=1
    fi
  fi
fi

if [ -z "$supervisord_running" ]; then
  sudo -E supervisord --config $CONFIG_FILE
  sleep 1
fi

if ! sc status | grep -e RUNNING -e STARTING; then
  sc start $SERVICE_NAME
fi
