#!/usr/bin/env bash
set -e

# 优雅停机脚本

cd /home/admin/aivision

pid_file=/var/run/gunicorn.pid

if [ -f "$pid_file" ]; then
    pid=$(cat $pid_file)
    if [ -n "$pid" ]; then
        kill -TERM $pid 2>/dev/null || true
        sleep 5
    fi
fi

sc stop app 2>/dev/null || true