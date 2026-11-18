#!/bin/sh
# 沙箱转发守护：把 WSL 的 docker.sock 转成 tcp 2375，供 Windows 侧后端
# SANDBOX_DOCKER_HOST=tcp://<WSL_IP>:2375 连接。由 scripts/service.ps1 调用。
# 仅在 SANDBOX_EXECUTOR=docker 时启动（校验在 service.ps1 侧完成）。
set -u

SOCK=/var/run/docker.sock
LOG=/var/log/socat-docker.log
PIDFILE=/var/log/socat-docker.pid

alive() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "${1:-}" in
  start)
    if alive; then
      echo "ALREADY $(cat "$PIDFILE")"
      exit 0
    fi
    if [ ! -S "$SOCK" ]; then
      echo "ERROR docker.sock 不存在: $SOCK" >&2
      exit 2
    fi
    if ! command -v socat >/dev/null 2>&1; then
      echo "ERROR 缺少 socat" >&2
      exit 3
    fi
    # setsid 脱离会话，避免随 wsl.exe 调用结束被回收
    setsid socat TCP-LISTEN:2375,fork,reuseaddr,bind=0.0.0.0 "UNIX-CONNECT:$SOCK" \
      >"$LOG" 2>&1 < /dev/null &
    pid=$!
    echo "$pid" > "$PIDFILE"
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
      echo "STARTED $pid"
    else
      echo "ERROR socat 启动后立即退出，日志: $LOG" >&2
      cat "$LOG" >&2 2>/dev/null
      rm -f "$PIDFILE"
      exit 4
    fi
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      pid=$(cat "$PIDFILE")
      kill "$pid" 2>/dev/null || true
      rm -f "$PIDFILE"
      echo "STOPPED $pid"
    else
      echo "NOT_RUNNING"
    fi
    # 兜底清理脚本外残留的转发进程
    pkill -f 'socat TCP-LISTEN:2375' 2>/dev/null || true
    ;;
  *)
    echo "usage: $0 {start|stop}" >&2
    exit 1
    ;;
esac
