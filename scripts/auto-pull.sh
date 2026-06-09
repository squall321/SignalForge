#!/usr/bin/env bash
# SignalForge — 수신 측 자동 풀 (Track Y2, round=auto_sync)
#
# 5분 주기로 Drive 의 LATEST.json sha256 을 polling 하여 변경 감지 시
# sync-from-drive.sh 로 백업 묶음(DB+env)을 받아 restore + backend reload.
#
# 사용:
#   bash scripts/auto-pull.sh                 # 정상 모드 (LATEST 비교 → 변경시만 풀)
#   bash scripts/auto-pull.sh --dry-run       # rclone --dry-run, restore/reload 생략
#   bash scripts/auto-pull.sh --force         # LATEST 비교 무시하고 무조건 풀+적용
#   bash scripts/auto-pull.sh --no-restore    # 풀까지만 (restore + reload 생략)
#   bash scripts/auto-pull.sh --no-reload     # restore 까지, backend reload 생략
#
# 상태 파일: /var/lib/sf_last_sync.json  (마지막 적용된 LATEST sha256/필드)
# 잠금:     /var/lock/sf_sync_from.lock  (flock -n)
# audit:    logs/audit/auto_sync.jsonl   (round=auto_sync track=Y2)
#
# 디자인 결정:
#   - LATEST.json 은 Drive 의 SignalForge/LATEST.json (~200B) — push 측이 매 회 갱신
#     없으면 db-dumps/ 의 최신 sf-db-*.sql.gz 파일명으로 폴백 (sha256 도 같이 받음)
#   - LATEST.json 의 db_sha256 이 마지막 적용 값과 다를 때만 실제 pull
#   - backend reload 는 systemd 가 있으면 systemctl reload, 없으면 PID kill -HUP
#   - 실패 시 마지막 상태 파일 그대로 두고 audit 에 fail 기록 — 다음 회차 재시도

set -uo pipefail   # -e 끔: 단계별 직접 평가 (롤백 가능)

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
DRIVE_SYNC_DIR="$SCRIPTS_DIR/drive-sync"
DUMP_DIR="$PROJECT_ROOT/backups"
AUDIT_DIR="$PROJECT_ROOT/logs/audit"
AUDIT_FILE="$AUDIT_DIR/auto_sync.jsonl"

LOCK_FILE="${SF_AUTO_PULL_LOCK:-/var/lock/sf_sync_from.lock}"
STATE_FILE="${SF_AUTO_PULL_STATE:-/var/lib/sf_last_sync.json}"
# /var/lib 가 root-only 면 사용자 위치로 폴백
if [[ ! -w "$(dirname "$STATE_FILE")" ]]; then
  STATE_FILE="$PROJECT_ROOT/.sf_last_sync.json"
fi

DRY=0
FORCE=0
DO_RESTORE=1
DO_RELOAD=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)     DRY=1 ;;
    --force)       FORCE=1 ;;
    --no-restore)  DO_RESTORE=0; DO_RELOAD=0 ;;
    --no-reload)   DO_RELOAD=0 ;;
    --lock)        LOCK_FILE="${2:?--lock PATH}"; shift ;;
    --state)       STATE_FILE="${2:?--state PATH}"; shift ;;
    -h|--help)     sed -n '1,32p' "$0"; exit 0 ;;
    *) echo "[ERROR] unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$AUDIT_DIR" "$DUMP_DIR"
RUN_ID="$(date -u +%s)-$$"

audit_event() {
  local event="$1"; shift
  local extra="${1:-}"
  local line
  line=$(printf '{"ts":"%s","round":"auto_sync","track":"Y2","run_id":"%s","script":"auto-pull","dry_run":%d,"force":%d,"event":"%s"' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$RUN_ID" "$DRY" "$FORCE" "$event")
  [[ -n "$extra" ]] && line="$line,$extra"
  line="$line}"
  echo "$line" >> "$AUDIT_FILE"
}

log() { echo "[auto-pull $(date -u +%H:%M:%SZ) $RUN_ID] $*"; }

# ── flock — 동시 실행 방지 ──────────────────────────────────────────
exec 9>"$LOCK_FILE" 2>/dev/null || {
  log "lock fd open 실패: $LOCK_FILE"
  audit_event "fail" "\"reason\":\"lock_open\""
  exit 1
}
if ! flock -n 9; then
  log "이미 다른 인스턴스 실행중 — skip ($LOCK_FILE)"
  audit_event "skip" "\"reason\":\"locked\""
  exit 0
fi

log "start  dry=$DRY  force=$FORCE  restore=$DO_RESTORE  reload=$DO_RELOAD"
log "state=$STATE_FILE  lock=$LOCK_FILE"
audit_event "start" "\"state\":\"$STATE_FILE\",\"lock\":\"$LOCK_FILE\",\"restore\":$DO_RESTORE,\"reload\":$DO_RELOAD"

# ── rclone & remote ─────────────────────────────────────────────────
command -v rclone >/dev/null || { log "rclone 미설치"; audit_event "fail" "\"reason\":\"no_rclone\""; exit 1; }

REMOTE="${SF_DRIVE_REMOTE:-ApptainerImages}"
PROJ="${SF_DRIVE_PROJECT:-SignalForge}"
REMOTE_ROOT="${REMOTE}:${PROJ}"

if ! rclone listremotes 2>/dev/null | grep -qx "${REMOTE}:"; then
  log "rclone remote 미설정: $REMOTE"
  audit_event "fail" "\"reason\":\"no_remote\",\"remote\":\"$REMOTE\""
  exit 1
fi

# ── 1) LATEST 비교 ──────────────────────────────────────────────────
TMP_LATEST="$(mktemp -t sf_latest.XXXXXX.json)"
trap 'rm -f "$TMP_LATEST"' EXIT

REMOTE_DB_SHA=""
REMOTE_DUMP_NAME=""
HAS_LATEST_JSON=0

# 1a) LATEST.json 우선 (push 측이 기록한 manifest)
if rclone cat "$REMOTE_ROOT/LATEST.json" > "$TMP_LATEST" 2>/dev/null && [[ -s "$TMP_LATEST" ]]; then
  HAS_LATEST_JSON=1
  REMOTE_DB_SHA=$(python3 -c "import json,sys;print(json.load(open('$TMP_LATEST')).get('db_sha256',''))" 2>/dev/null || echo "")
  REMOTE_DUMP_NAME=$(python3 -c "import json,sys;print(json.load(open('$TMP_LATEST')).get('last_dump',''))" 2>/dev/null || echo "")
  log "LATEST.json: db_sha256=${REMOTE_DB_SHA:0:12}…  last_dump=$REMOTE_DUMP_NAME"
else
  # 1b) 폴백: db-dumps/ 의 최신 .sha256 파일을 받아 sha 추출
  log "LATEST.json 부재 → db-dumps/ 직접 polling"
  LATEST_DUMP=$(rclone lsf "$REMOTE_ROOT/db-dumps/" --include 'sf-db-*.sql.gz' 2>/dev/null | sort | tail -1)
  if [[ -z "$LATEST_DUMP" ]]; then
    log "원격 dump 없음 — skip"
    audit_event "skip" "\"reason\":\"no_remote_dump\""
    exit 0
  fi
  REMOTE_DUMP_NAME="$LATEST_DUMP"
  TMP_SHA="$(mktemp -t sf_dump_sha.XXXXXX)"
  trap 'rm -f "$TMP_LATEST" "$TMP_SHA"' EXIT
  if rclone cat "$REMOTE_ROOT/db-dumps/${LATEST_DUMP}.sha256" > "$TMP_SHA" 2>/dev/null && [[ -s "$TMP_SHA" ]]; then
    REMOTE_DB_SHA=$(awk '{print $1}' "$TMP_SHA")
  else
    # sha 파일도 없으면 파일명 자체를 변경 키로 사용
    REMOTE_DB_SHA="name:$LATEST_DUMP"
    log "remote .sha256 부재 — 파일명을 변경 키로 사용"
  fi
  log "fallback: name=$LATEST_DUMP  sha=${REMOTE_DB_SHA:0:12}…"
fi

# ── 2) 로컬 상태 비교 ───────────────────────────────────────────────
LOCAL_DB_SHA=""
LOCAL_APPLIED_AT=""
if [[ -f "$STATE_FILE" ]]; then
  LOCAL_DB_SHA=$(python3 -c "import json;print(json.load(open('$STATE_FILE')).get('db_sha256',''))" 2>/dev/null || echo "")
  LOCAL_APPLIED_AT=$(python3 -c "import json;print(json.load(open('$STATE_FILE')).get('applied_at',''))" 2>/dev/null || echo "")
fi
log "local: sha=${LOCAL_DB_SHA:0:12}…  applied_at=$LOCAL_APPLIED_AT"

if [[ $FORCE -eq 0 && -n "$LOCAL_DB_SHA" && "$LOCAL_DB_SHA" == "$REMOTE_DB_SHA" ]]; then
  log "변경 없음 — skip"
  audit_event "no_change" "\"sha\":\"${REMOTE_DB_SHA:0:16}\""
  exit 0
fi

log "변경 감지 → pull 진행 (force=$FORCE)"
audit_event "change_detected" "\"local_sha\":\"${LOCAL_DB_SHA:0:16}\",\"remote_sha\":\"${REMOTE_DB_SHA:0:16}\",\"dump\":\"$REMOTE_DUMP_NAME\""

# ── 3) sync-from-drive.sh 호출 (DB+env, SIF 제외 — 핫경로 가벼움) ───
PULL_RC=0
if [[ $DRY -eq 1 ]]; then
  log "(dry-run) sync-from-drive.sh --dry-run --no-sif 호출"
  bash "$SCRIPTS_DIR/sync-from-drive.sh" --dry-run --no-sif >/dev/null 2>&1
  PULL_RC=$?
else
  bash "$SCRIPTS_DIR/sync-from-drive.sh" --no-sif >/dev/null 2>&1
  PULL_RC=$?
fi

if [[ $PULL_RC -ne 0 ]]; then
  log "sync-from-drive 실패 (rc=$PULL_RC) — 상태 미갱신"
  audit_event "fail" "\"reason\":\"pull_failed\",\"rc\":$PULL_RC"
  exit 1
fi
log "pull OK"
audit_event "pull_ok"

# ── 4) restore (옵션) ───────────────────────────────────────────────
if [[ $DO_RESTORE -eq 1 && $DRY -eq 0 ]]; then
  DUMP_PATH="$DUMP_DIR/sf-db-latest.sql.gz"
  if [[ ! -f "$DUMP_PATH" && -L "$DUMP_PATH" ]]; then DUMP_PATH=$(readlink -f "$DUMP_PATH"); fi
  if [[ ! -f "$DUMP_PATH" ]]; then
    # symlink 가 없으면 최신 sf-db-*.sql.gz 직접 선택
    DUMP_PATH=$(ls -t "$DUMP_DIR"/sf-db-*.sql.gz 2>/dev/null | head -1)
  fi
  if [[ -z "$DUMP_PATH" || ! -f "$DUMP_PATH" ]]; then
    log "restore 대상 dump 부재 — 단계 스킵"
    audit_event "restore_skip" "\"reason\":\"no_local_dump\""
  else
    log "restore: $DUMP_PATH"
    if bash "$DRIVE_SYNC_DIR/restore-db.sh" "$DUMP_PATH" --yes >/dev/null 2>&1; then
      log "restore OK"
      audit_event "restore_ok" "\"dump\":\"$(basename "$DUMP_PATH")\""
    else
      log "restore 실패 — 상태 미갱신 (안전백업은 drive-sync/restore-db.sh 가 생성)"
      audit_event "fail" "\"reason\":\"restore_failed\""
      exit 1
    fi
  fi
fi

# ── 5) backend reload (옵션) ────────────────────────────────────────
if [[ $DO_RELOAD -eq 1 && $DRY -eq 0 ]]; then
  RELOADED=0
  # 5a) systemd
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet sf-backend 2>/dev/null; then
    if systemctl reload sf-backend 2>/dev/null || systemctl restart sf-backend 2>/dev/null; then
      RELOADED=1; log "systemd reload sf-backend OK"
    fi
  fi
  # 5b) uvicorn PID kill -HUP
  if [[ $RELOADED -eq 0 ]]; then
    BPID=$(ss -ltnp 2>/dev/null | awk '/:18000 / {print $0}' | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
    if [[ -n "$BPID" ]]; then
      if kill -HUP "$BPID" 2>/dev/null; then
        RELOADED=1; log "kill -HUP $BPID (uvicorn :18000) OK"
      fi
    fi
  fi
  if [[ $RELOADED -eq 0 ]]; then
    log "backend reload 대상 없음 (uvicorn :18000 미가동?) — 검증으로 진행"
    audit_event "reload_skip" "\"reason\":\"no_target\""
  else
    audit_event "reload_ok"
    sleep 3   # backend 부팅 시간
  fi
fi

# ── 6) 검증 (voc_count + alembic head) ──────────────────────────────
VERIFY_OK=1
VOC=""
ALEMBIC_HEAD=""
if [[ $DRY -eq 0 ]]; then
  VOC=$(curl -sS --max-time 5 "http://127.0.0.1:18000/api/v1/_internal/key-status" 2>/dev/null \
        | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('voc_count',d.get('voc','?')))" 2>/dev/null || echo "?")
  if [[ -x "$DRIVE_SYNC_DIR/_drive_common.sh" || -f "$DRIVE_SYNC_DIR/_drive_common.sh" ]]; then
    ALEMBIC_HEAD=$(
      cd "$DRIVE_SYNC_DIR"
      source ./_drive_common.sh 2>/dev/null
      PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -h "${POSTGRES_HOST:-127.0.0.1}" -p "${POSTGRES_PORT:-5434}" \
        -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-signalforge}" -t -A \
        -c "SELECT version_num FROM alembic_version LIMIT 1;" 2>/dev/null
    )
  fi
  log "verify: voc=$VOC  alembic=$ALEMBIC_HEAD"
fi

# ── 7) 성공 시 상태 갱신 ────────────────────────────────────────────
if [[ $DRY -eq 0 && $VERIFY_OK -eq 1 ]]; then
  STATE_DIR="$(dirname "$STATE_FILE")"
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  cat > "$STATE_FILE" <<JSON
{
  "db_sha256": "$REMOTE_DB_SHA",
  "last_dump": "$REMOTE_DUMP_NAME",
  "applied_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "run_id": "$RUN_ID",
  "voc_count": "$VOC",
  "alembic_head": "$ALEMBIC_HEAD",
  "has_latest_json": $HAS_LATEST_JSON
}
JSON
  log "state 갱신: $STATE_FILE"
  audit_event "applied" "\"sha\":\"${REMOTE_DB_SHA:0:16}\",\"dump\":\"$REMOTE_DUMP_NAME\",\"voc\":\"$VOC\",\"alembic\":\"$ALEMBIC_HEAD\""
elif [[ $DRY -eq 1 ]]; then
  log "(dry-run) state 갱신 생략"
  audit_event "dryrun_end" "\"sha\":\"${REMOTE_DB_SHA:0:16}\""
fi

audit_event "end"
log "done"
exit 0
