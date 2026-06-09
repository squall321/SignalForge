#!/usr/bin/env bash
# SignalForge — 서비스 상태 확인
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env 2>/dev/null || true

LOG_DIR="$PROJECT_ROOT/logs"

echo "================================================================"
echo " SignalForge 서비스 상태"
echo "================================================================"

pid_status() {
  local name="$1" pidfile="$2"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "  ✓ $name  (pid=$(cat "$pidfile"))"
  else
    echo "  ✗ $name  (중지됨)"
  fi
}

# Apptainer instances
echo "[Apptainer]"
if instance_running "$INST_POSTGRES"; then
  echo "  ✓ $INST_POSTGRES"
else
  echo "  ✗ $INST_POSTGRES"
fi

# Native 프로세스
echo "[Native]"
pid_status "Backend (uvicorn)" "$LOG_DIR/backend.pid"
pid_status "Celery worker"     "$LOG_DIR/celery-worker.pid"
pid_status "Celery beat"       "$LOG_DIR/celery-beat.pid"
pid_status "MCP server"        "$LOG_DIR/mcp.pid"

# HTTP 헬스체크
echo "[Health]"
curl -sf "http://127.0.0.1:${API_PORT:-8000}/health" >/dev/null 2>&1 \
  && echo "  ✓ API /health OK" \
  || echo "  ✗ API /health 응답 없음"

# Redis
redis-cli -h "${REDIS_HOST:-127.0.0.1}" -p "${REDIS_PORT:-6379}" \
  ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD"} --no-auth-warning ping 2>/dev/null \
  | grep -q PONG && echo "  ✓ Redis OK" || echo "  ✗ Redis 응답 없음"

echo "================================================================"
echo "  로그: tail -f $LOG_DIR/backend.log"
