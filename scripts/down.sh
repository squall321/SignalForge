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

# 레거시 pidfile 경로 — 백엔드/MCP/셀러리를 호스트 프로세스로 띄우던 시절의 잔재다. 지금은 전부
# apptainer 인스턴스라 보통 "실행 중 아님" 만 찍힌다. 옛 방식 잔여 프로세스 정리용으로 남겨 두되,
# 이 출력만 보고 "다 내렸다" 고 판단하면 안 된다 — 실제 종료는 아래 인스턴스 루프가 한다.
stop_pid "MCP(레거시 pid)"           "$LOG_DIR/mcp.pid"
stop_pid "Celery beat(레거시 pid)"   "$LOG_DIR/celery-beat.pid"
stop_pid "Celery worker(레거시 pid)" "$LOG_DIR/celery-worker.pid"
stop_pid "Backend(레거시 pid)"       "$LOG_DIR/backend.pid"

# up.sh 가 만드는 인스턴스 전부를 내린다. 예전에는 postgres 만 내리면서 "✓ 모든 서비스 종료" 라고
# 찍어 오해를 불렀다 — 실제로는 sf-frontend/sf-mcp/sf-backend/크롤러가 그대로 살아 있었고, 그
# 상태에서 sync-from-drive 가 SIF 를 덮으면 squashfs 마운트가 깨져 나중에 502 로 터졌다.
SF_INSTANCES=(sf-frontend sf-mcp sf-backend sf-crawler-beat sf-crawler-worker "$INST_POSTGRES")
stopped=0; already=0
for inst in "${SF_INSTANCES[@]}"; do
  if instance_running "$inst"; then
    echo "→ $inst 중지..."
    if apptainer instance stop "$inst" >/dev/null 2>&1; then
      echo "  ✓ $inst 종료"; stopped=$((stopped + 1))
    else
      echo "  [WARN] $inst 종료 실패 — 남아 있을 수 있음"
    fi
  else
    echo "  · $inst 실행 중 아님"; already=$((already + 1))
  fi
done

# 사실대로 보고한다 — 남은 게 있으면 "모든 서비스 종료" 라고 말하지 않는다.
left="$(apptainer instance list 2>/dev/null | awk 'NR>1 && $1 ~ /^sf[-_]/ {printf "%s ", $1}')"
if [ -n "${left// /}" ]; then
  echo "✗ 아직 남은 인스턴스: ${left% }"
  echo "  (수동 종료: apptainer instance stop ${left% })"
  exit 1
fi
echo "✓ SignalForge 인스턴스 전부 종료 (중지 $stopped, 이미 내려있던 것 $already)"
