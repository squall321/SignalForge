#!/usr/bin/env bash
# SignalForge — 전체 서비스 중지
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

LOG_DIR="$PROJECT_ROOT/logs"

stop_pid() {
  local name="$1" pidfile="$2"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    kill "$(cat "$pidfile")" && echo "✓ $name 종료" || echo "[WARN] $name 종료 실패"
    rm -f "$pidfile"
  else
    echo "  $name 실행 중 아님"
  fi
}

stop_pid "MCP"           "$LOG_DIR/mcp.pid"
stop_pid "Celery beat"   "$LOG_DIR/celery-beat.pid"
stop_pid "Celery worker" "$LOG_DIR/celery-worker.pid"
stop_pid "Backend"       "$LOG_DIR/backend.pid"

if instance_running "$INST_POSTGRES"; then
  echo "→ $INST_POSTGRES 중지..."
  apptainer instance stop "$INST_POSTGRES" && echo "✓ postgres 종료"
else
  echo "  postgres 실행 중 아님"
fi

echo "✓ 모든 서비스 종료"
