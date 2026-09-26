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
LOCKFILE="/tmp/sf-watchdog.lock"
mkdir -p "$ROOT/logs"

HEARTBEAT="$ROOT/logs/.watchdog-heartbeat"

_log(){ echo "$(date '+%F %T') $*" >> "$LOG"; }

# 락 fd 를 **자식에게 물려주지 않고** 실행한다.
#
# 이게 없으면 이렇게 망가진다(2026-09-18 ~ 09-25 실제로 일어났다).
#   1. 워치독이 `exec 9>lock` 으로 락을 잡고 up.sh 를 부른다
#   2. up.sh 가 `apptainer instance start` 로 데몬을 띄운다
#   3. 그 데몬이 **fd 9 를 상속한다** — 데몬은 죽지 않으니 락도 안 풀린다
#   4. 그 뒤 매분 `flock -n 9` 가 실패하고 워치독은 조용히 exit 0
#   5. 일주일간 backend·frontend·postgres·celery 가 내려간 채 아무도 몰랐다
# 복구 장치가 스스로를 벙어리로 만든 것이다. 실측: sf_postgres·sf-crawler-worker
# 가 fd 9 를 붙들고 있었다.
run_nolock(){ "$@" 9>&-; }

# 락을 붙든 프로세스 — 상속 사고를 다시 만나면 범인을 바로 남긴다
_lock_holders(){
  local ino; ino=$(stat -c '%i' "$LOCKFILE" 2>/dev/null) || return 0
  local pid l out=""
  for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
    [ -r "/proc/$pid/fd" ] || continue
    for l in /proc/$pid/fd/*; do
      case "$(readlink "$l" 2>/dev/null)" in *"$(basename "$LOCKFILE")"*)
        out="$out $pid($(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-40))" ;;
      esac
    done
  done
  echo "$out"
}

# 중복 실행 방지 — 그리고 **누수된 락은 스스로 끊는다.**
#
# 락을 붙든 것이 살아있는 워치독이 아니면(위 상속 사고), 기다려도 영원히 안 풀린다.
# 그 상태를 '경고만' 하면 서비스는 계속 내려가 있다 — 일주일이 그랬다.
# 그래서 보유자 중 워치독이 하나도 없으면 파일을 갈아 새 inode 로 락을 재발급한다.
# 옛 inode 는 unlink 되어 상속자들이 붙들고 있어도 무해하다.
ALARM="$ROOT/logs/.watchdog-lock-alarm"

_take_lock(){ exec 9>"$LOCKFILE"; flock -n 9; }

if ! _take_lock; then
  if _lock_holders | grep -q 'watchdog\.sh'; then
    # 앞선 tick 이 정말 도는 중 — 정상. 다만 오래 물고 있으면 남긴다(10분에 한 번).
    now=$(date +%s); last=$(stat -c %Y "$ALARM" 2>/dev/null || echo 0)
    if [ $((now - last)) -ge 600 ]; then
      : > "$ALARM"; _log "앞선 워치독이 아직 실행 중 — skip"
    fi
    exit 0
  fi
  # 워치독이 아닌 것이 락을 붙들었다 = fd 상속 누수. 끊고 계속한다.
  _log "!! 락 누수 감지(보유자에 워치독 없음) — 락 재발급. 보유:$(_lock_holders | cut -c1-300)"
  mv -f "$LOCKFILE" "$LOCKFILE.leaked.$(date +%s)" 2>/dev/null || rm -f "$LOCKFILE"
  find /tmp -maxdepth 1 -name 'sf-watchdog.lock.leaked.*' -mmin +1440 -delete 2>/dev/null || true
  if ! _take_lock; then
    _log "!! 락 재발급 후에도 잡히지 않음 — 다음 tick 재시도"
    exit 0
  fi
fi
: > "$HEARTBEAT"
_tcp(){ timeout 4 bash -c "exec 3<>/dev/tcp/127.0.0.1/$1" >/dev/null 2>&1; }
# postgres postmaster 를 OOM 킬러에서 보호 (권한 없으면 무해하게 실패)
_protect_pg(){ for pid in $(pgrep -f '/usr/local/bin/postgres' 2>/dev/null); do
  echo -600 > "/proc/$pid/oom_score_adj" 2>/dev/null || true; done; }

if ! _tcp "$PGPORT"; then
  _log "postgres :$PGPORT DOWN → 좀비 instance stop + up.sh 복구"
  apptainer instance stop "$INST_PG" >/dev/null 2>&1 || true
  sleep 2
  run_nolock bash "$HERE/up.sh" >> "$LOG" 2>&1 && _log "복구 완료" || _log "up.sh 실패 — 다음 tick 재시도"
  _protect_pg
  exit 0
fi

# postgres 정상 — 앱 서비스/수집 점검
need=0
for p in 18000 8013 17370; do _tcp "$p" || { need=1; _log "port $p down"; }; done
ps -eo args 2>/dev/null | grep -E '[c]elery -A celery_app worker' >/dev/null || { need=1; _log "celery worker down"; }
# **beat 도 본다.** worker 만 보던 탓에 beat 이 죽으면 전 스케줄(수집·MV 갱신·감시)이
# 멈추는데도 포트 3종과 worker 가 살아 있어 전부 초록으로 보였다. 수집이 0 인데
# '정상'으로 읽히는 조합이 바로 이것이다.
ps -eo args 2>/dev/null | grep -E '[c]elery -A celery_app beat' >/dev/null || { need=1; _log "celery beat down"; }
# beat 이 떠 있어도 스케줄을 실제로 내보내는지는 별 문제다. 스케줄 상태파일이
# 오래 갱신되지 않으면 살아는 있으나 일을 안 하는 상태다(가장 조용한 고장).
_beat_stale(){
  local f="$ROOT/crawler/celerybeat-schedule"
  [ -f "$f" ] || return 1
  [ $(( $(date +%s) - $(stat -c %Y "$f") )) -ge 1800 ]
}
if _beat_stale; then
  need=1; _log "celery beat 스케줄 파일이 30분 이상 갱신 안 됨 — 살아있지만 일하지 않는다"
fi
if [ "$need" -eq 1 ]; then
  _log "일부 서비스 다운 → up.sh"
  run_nolock bash "$HERE/up.sh" >> "$LOG" 2>&1
fi
_protect_pg
exit 0
