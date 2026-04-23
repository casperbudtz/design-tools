#!/bin/bash
PID=$(ss -tlnp | grep ':8081' | grep -oP 'pid=\K[0-9]+')
if [ -n "$PID" ]; then
    echo "Stopping pid $PID..."
    kill "$PID"
    sleep 1
fi
nohup python3 "$(dirname "$0")/server.py" > /tmp/design-tools.log 2>&1 &
echo "Started pid $! — http://localhost:8081"
