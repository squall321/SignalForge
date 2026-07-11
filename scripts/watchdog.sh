#!/usr/bin/env bash
# SignalForge 서비스 워치독 — cron 매분 실행. postgres 좀비/다운·서비스 다운 자동 복구.
# 핵심: OOM 등으로 sf_postgres 가 좀비(instance 는 살아있으나 postgres 프로세스 죽음)가 되면
#       up.sh 는 instance_running=true 라 skip 하므로, 여기서 instance stop 후 up.sh 로 fresh 복구.
# 부수: postgres postmaster 의 oom_score_adj 를 낮춰(권한 있으면) OOM 우선순위에서 보호.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# XDG_RUNTIME_DIR/DBUS (apptainer instance 조작 필수) — _common.sh 가 설정
# shellcheck source=/dev/null
source "$HERE/_common.sh" >/dev/null 2>&1 || true
INST_PG="${INST_POSTGRES:-sf_postgres}"
PGPORT="${POSTGRES_PORT:-5434}"
LOG="$ROOT/logs/watchdog.log"
mkdir -p "$ROOT/logs"

# 중복 실행 방지
exec 9>"/tmp/sf-watchdog.lock"
flock -n 9 || exit 0

_log(){ echo "$(date '+%F %T') $*" >> "$LOG"; }
_tcp(){ timeout 4 bash -c "exec 3<>/dev/tcp/127.0.0.1/$1" >/dev/null 2>&1; }
# postgres postmaster 를 OOM 킬러에서 보호 (권한 없으면 무해하게 실패)
_protect_pg(){ for pid in $(pgrep -f '/usr/local/bin/postgres' 2>/dev/null); do
  echo -600 > "/proc/$pid/oom_score_adj" 2>/dev/null || true; done; }

if ! _tcp "$PGPORT"; then
  _log "postgres :$PGPORT DOWN → 좀비 instance stop + up.sh 복구"
  apptainer instance stop "$INST_PG" >/dev/null 2>&1 || true
  sleep 2
  bash "$HERE/up.sh" >> "$LOG" 2>&1 && _log "복구 완료" || _log "up.sh 실패 — 다음 tick 재시도"
  _protect_pg
  exit 0
fi

# postgres 정상 — 앱 서비스/수집 점검
need=0
for p in 18000 8013 17370; do _tcp "$p" || { need=1; _log "port $p down"; }; done
ps -eo args 2>/dev/null | grep -E '[c]elery -A celery_app worker' >/dev/null || { need=1; _log "celery worker down"; }
if [ "$need" -eq 1 ]; then
  _log "일부 서비스 다운 → up.sh"
  bash "$HERE/up.sh" >> "$LOG" 2>&1
fi
_protect_pg
exit 0
