#!/usr/bin/env bash
set -e

sudo mkdir -p /var/log/supervisor
sudo su admin -c 'mkdir -p /home/admin/logs/aivision'
sudo mkdir -p /home/admin/aivision/staticfiles
sudo chown admin:admin /home/admin/aivision/staticfiles

pid_file=/var/run/supervisord.pid
supervisord_running=
if [ -e "$pid_file" ]; then
  pid=$(cat $pid_file)
  if [ -n "$pid" ] && ps -p $pid >/dev/null 2>&1; then
    supervisord_running=1
  fi
fi

if [ -z "$supervisord_running" ]; then
  sudo -E supervisord --config /etc/supervisord.conf
  
  for i in $(seq 1 15); do
    if curl -s http://127.0.0.1:9001 >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

if ! sc status | grep -q "app.*RUNNING"; then
  sc start app
fi

echo "start.sh completed successfully"