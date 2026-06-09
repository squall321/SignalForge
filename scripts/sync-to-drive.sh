#!/usr/bin/env bash
# SignalForge — 단방향 배포 동기화 (이 호스트 → Google Drive)
#
# Stage 4 portal_deploy 진입점. MX White Paper images-to-drive.sh + AIDataHub drive-sync
# 두 패턴을 합친 합성 진입점. 본격 작업은 위임:
#   - DB dump  → scripts/drive-sync/backup-to-drive.sh  (이미 정상 가동, 7 dump 보존중)
#   - SIF      → 자체 sha256sum SHA256SUMS + rclone copy + latest/ sync (MXWP 패턴)
#   - .env.example → rclone copy SignalForge/env/
#
# 사용:
#   bash scripts/sync-to-drive.sh                     # 전체 (DB + SIF + env)
#   bash scripts/sync-to-drive.sh --dry-run           # 실 업로드 없이 시뮬레이션
#   bash scripts/sync-to-drive.sh --no-sif            # DB + env 만
#   bash scripts/sync-to-drive.sh --no-db             # SIF + env 만 (Drive 용량 절약)
#   bash scripts/sync-to-drive.sh --no-env
#   bash scripts/sync-to-drive.sh --sif-retain 3      # SIF 세트 보존 개수 (default 3)
#
# audit: logs/audit/portal_deploy.jsonl  (round=portal_deploy track=S4)

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
SIF_DIR="$PROJECT_ROOT/apptainer/sif"
DRIVE_SYNC_DIR="$SCRIPTS_DIR/drive-sync"
AUDIT_DIR="$PROJECT_ROOT/logs/audit"
AUDIT_FILE="$AUDIT_DIR/portal_deploy.jsonl"
AUDIT_SYNC_FILE="$AUDIT_DIR/auto_sync.jsonl"

# Y4 — flock 가드 (5분 timeout). 동시 실행시 즉시 skip.
# shellcheck source=./_lock_helper.sh
source "$SCRIPTS_DIR/_lock_helper.sh"

# Z1 — LATEST.json 메타 헬퍼 (Y3 contract). build_latest_meta() 제공.
# shellcheck source=./lib/latest-meta.sh
source "$SCRIPTS_DIR/lib/latest-meta.sh"

# ── 기본 옵션 ────────────────────────────────────────────────────────
DRY=0
WITH_DB=1
WITH_SIF=1
WITH_ENV=1
SIF_RETAIN=3
# Z1 — sif_changed 누적 (svc 이름만, 콤마 구분). SIF 스테이지가 실제로 업로드한 svc.
SIF_CHANGED_CSV=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)     DRY=1 ;;
    --no-db)       WITH_DB=0 ;;
    --no-sif)      WITH_SIF=0 ;;
    --no-env)      WITH_ENV=0 ;;
    --sif-retain)  SIF_RETAIN="${2:?--sif-retain N}"; shift ;;
    -h|--help)
      sed -n '1,30p' "$0"; exit 0 ;;
    *) echo "[ERROR] unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$AUDIT_DIR"
TS_RUN="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RUN_ID="$(date -u +%s)-$$"

audit_event() {
  # $1 event, $2 (선택) JSON 조각 — 예: '"sif_count":4,"sha":"abc"'
  local event="$1"; shift
  local extra="${1:-}"
  local line
  line=$(printf '{"ts":"%s","round":"portal_deploy","track":"S4","run_id":"%s","script":"sync-to-drive","dry_run":%d,"event":"%s"' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$RUN_ID" "$DRY" "$event")
  [[ -n "$extra" ]] && line="$line,$extra"
  line="$line}"
  echo "$line" >> "$AUDIT_FILE"
}

audit_sync() {
  # Y4 auto_sync 채널 — round=auto_sync track=Y4
  local event="$1"; shift
  local extra="${1:-}"
  local line
  line=$(printf '{"ts":"%s","round":"auto_sync","track":"Y4","run_id":"%s","script":"sync-to-drive","dry_run":%d,"event":"%s"' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$RUN_ID" "$DRY" "$event")
  [[ -n "$extra" ]] && line="$line,$extra"
  line="$line}"
  echo "$line" >> "$AUDIT_SYNC_FILE"
}

echo "================================================================"
echo " SignalForge sync-to-drive  [run=$RUN_ID  dry=$DRY]"
echo "   db=$WITH_DB  sif=$WITH_SIF  env=$WITH_ENV  sif_retain=$SIF_RETAIN"
echo "================================================================"

# Y4 — flock 가드. 동시 실행 시 즉시 종료 (exit 0, audit skip 기록).
if ! sf_lock_acquire push 300; then
  audit_sync "lock_skip" "\"kind\":\"push\",\"file\":\"$(sf_lock_path push)\""
  echo "[SKIP] 이미 다른 push 가 실행 중 — 종료"
  exit 0
fi
audit_sync "start" "\"db\":$WITH_DB,\"sif\":$WITH_SIF,\"env\":$WITH_ENV,\"lock\":\"$SF_LOCK_FILE\",\"timeout\":$SF_LOCK_TIMEOUT"

audit_event "start" "\"db\":$WITH_DB,\"sif\":$WITH_SIF,\"env\":$WITH_ENV,\"sif_retain\":$SIF_RETAIN"

# ── rclone & remote 확인 ─────────────────────────────────────────────
command -v rclone >/dev/null || { echo "[ERROR] rclone 미설치"; audit_event "fail" "\"reason\":\"no_rclone\""; exit 1; }

# remote 이름은 drive-sync/PROJECT.conf 의 PROJ_DRIVE_REMOTE_DEFAULT 와 일치시킴
REMOTE="${SF_DRIVE_REMOTE:-ApptainerImages}"
PROJ="${SF_DRIVE_PROJECT:-SignalForge}"
REMOTE_ROOT="${REMOTE}:${PROJ}"

if ! rclone listremotes 2>/dev/null | grep -qx "${REMOTE}:"; then
  echo "[ERROR] rclone remote 미설정: $REMOTE  →  rclone config 또는 scripts/drive-sync/setup-drive-sync.sh"
  audit_event "fail" "\"reason\":\"no_remote\",\"remote\":\"$REMOTE\""
  exit 1
fi
echo "[OK] remote=$REMOTE  root=$REMOTE_ROOT"

# ── 1) DB dump (drive-sync/backup-to-drive.sh 위임) ──────────────────
if [[ $WITH_DB -eq 1 ]]; then
  echo
  echo "▶ [1/4] DB dump → $REMOTE_ROOT/db-dumps/"
  if [[ $DRY -eq 1 ]]; then
    echo "  (dry-run) skip — 실 실행 시 scripts/drive-sync/backup-to-drive.sh 호출"
    audit_event "db_dryrun"
  else
    if [[ ! -x "$DRIVE_SYNC_DIR/backup-to-drive.sh" ]]; then
      echo "[WARN] $DRIVE_SYNC_DIR/backup-to-drive.sh 없음 — DB 단계 스킵"
      audit_event "db_skip" "\"reason\":\"no_helper\""
    else
      ( cd "$DRIVE_SYNC_DIR" && bash backup-to-drive.sh )
      audit_event "db_ok"
    fi
  fi
else
  echo "▶ [1/4] DB dump  ── skip (--no-db)"
fi

# ── 2) SIF 묶음 (MXWP 패턴 — staging + SHA256SUMS + latest/) ─────────
if [[ $WITH_SIF -eq 1 ]]; then
  echo
  echo "▶ [2/4] SIF → $REMOTE_ROOT/sif/  (+ latest/)"
  if [[ ! -d "$SIF_DIR" ]]; then
    echo "[WARN] $SIF_DIR 디렉터리 없음 — SIF 단계 스킵"
    audit_event "sif_skip" "\"reason\":\"no_sif_dir\""
  else
    mapfile -t SIFS < <(find "$SIF_DIR" -maxdepth 1 -type f -name "*.sif" | sort)
    if [[ ${#SIFS[@]} -eq 0 ]]; then
      echo "[WARN] $SIF_DIR 에 *.sif 없음 — SIF 단계 스킵 (Stage 1/2 빌드 미완)"
      audit_event "sif_skip" "\"reason\":\"empty\""
    else
      TS_TAG="$(date -u +%Y%m%d-%H%M%SZ)"
      STAGE="$(mktemp -d -t sf-sif-stage.XXXXXX)"
      trap 'rm -rf "$STAGE"' EXIT
      for f in "${SIFS[@]}"; do
        cp "$f" "$STAGE/"
        # Z1 — svc 이름 (backend.sif → backend) 만 누적. postgres* 는 baseline 이므로 제외.
        svc_name="$(basename "$f" .sif)"
        case "$svc_name" in
          postgres|postgres-*) ;;  # baseline — skip
          *)
            if [[ -z "$SIF_CHANGED_CSV" ]]; then
              SIF_CHANGED_CSV="$svc_name"
            else
              SIF_CHANGED_CSV="$SIF_CHANGED_CSV,$svc_name"
            fi
            ;;
        esac
      done
      ( cd "$STAGE" && sha256sum ./*.sif > SHA256SUMS )
      SIF_COUNT=${#SIFS[@]}
      TOTAL_SIZE=$(du -sh "$STAGE" | cut -f1)
      echo "  staged: $SIF_COUNT sif ($TOTAL_SIZE)  →  sif-$TS_TAG/ + latest/"
      cat "$STAGE/SHA256SUMS" | sed 's/^/    /'

      if [[ $DRY -eq 1 ]]; then
        echo "  (dry-run) rclone copy/sync 생략"
        audit_event "sif_dryrun" "\"count\":$SIF_COUNT,\"size\":\"$TOTAL_SIZE\""
      else
        rclone copy --progress "$STAGE/" "$REMOTE_ROOT/sif-$TS_TAG/"
        rclone sync --progress "$STAGE/" "$REMOTE_ROOT/sif/latest/"
        # 보존: sif-* 폴더 중 최신 SIF_RETAIN 개만 유지
        if [[ "$SIF_RETAIN" -gt 0 ]]; then
          rclone lsf --dirs-only "$REMOTE_ROOT/" 2>/dev/null \
            | sed 's#/$##' | grep -E '^sif-' | sort \
            | head -n -"$SIF_RETAIN" \
            | while read -r old; do
                [[ -z "$old" ]] && continue
                echo "  retention: purge $old/"
                rclone purge "$REMOTE_ROOT/$old" 2>/dev/null || true
              done
        fi
        audit_event "sif_ok" "\"count\":$SIF_COUNT,\"size\":\"$TOTAL_SIZE\",\"tag\":\"$TS_TAG\",\"retain\":$SIF_RETAIN"
      fi
    fi
  fi
else
  echo "▶ [2/4] SIF  ── skip (--no-sif)"
fi

# ── 3) .env.example ──────────────────────────────────────────────────
if [[ $WITH_ENV -eq 1 ]]; then
  echo
  echo "▶ [3/4] .env.example → $REMOTE_ROOT/env/"
  ENV_EX="$PROJECT_ROOT/.env.example"
  if [[ ! -f "$ENV_EX" ]]; then
    echo "[WARN] $ENV_EX 없음 — env 단계 스킵"
    audit_event "env_skip" "\"reason\":\"no_env_example\""
  else
    if [[ $DRY -eq 1 ]]; then
      echo "  (dry-run) rclone copy 생략 — $ENV_EX ($(stat -c%s "$ENV_EX") bytes)"
      audit_event "env_dryrun"
    else
      rclone copy "$ENV_EX" "$REMOTE_ROOT/env/"
      audit_event "env_ok"
    fi
  fi
else
  echo "▶ [3/4] .env.example  ── skip (--no-env)"
fi

# ── 4) LATEST.json (Z1 — Y3 contract, Drive 루트 업로드) ─────────────
echo
echo "▶ [4/4] LATEST.json → $REMOTE_ROOT/LATEST.json"
LATEST_LOCAL="$PROJECT_ROOT/logs/sync/LATEST.json"
mkdir -p "$(dirname "$LATEST_LOCAL")"
# build_latest_meta 가 SF_SIF_CHANGED 환경변수를 읽어 sif_changed 배열을 채운다.
LATEST_BUILT=0
if SF_SIF_CHANGED="$SIF_CHANGED_CSV" build_latest_meta "$LATEST_LOCAL" 2>/tmp/sf_latest_err.$$; then
  LATEST_BUILT=1
  L_SHA="$(jq -r '.db_dump.sha256 // ""' "$LATEST_LOCAL" 2>/dev/null || echo "")"
  L_VOC="$(jq -r '.voc_count // 0'         "$LATEST_LOCAL" 2>/dev/null || echo "0")"
  L_TS="$(jq  -r '.timestamp // ""'        "$LATEST_LOCAL" 2>/dev/null || echo "")"
  echo "  built: voc=$L_VOC  ts=$L_TS  dump_sha=${L_SHA:0:12}..  sif_changed=[$SIF_CHANGED_CSV]"
else
  echo "[WARN] LATEST.json 빌드 실패 — $(cat /tmp/sf_latest_err.$$ 2>/dev/null | tail -3)"
  audit_event "latest_build_fail"
fi
rm -f /tmp/sf_latest_err.$$

if [[ $LATEST_BUILT -eq 1 ]]; then
  if [[ $DRY -eq 1 ]]; then
    echo "  (dry-run) rclone copy 생략 — $LATEST_LOCAL ($(stat -c%s "$LATEST_LOCAL") bytes)"
    audit_event "latest_dryrun" "\"path\":\"$LATEST_LOCAL\",\"dump_sha\":\"${L_SHA}\",\"voc\":$L_VOC"
  else
    if rclone copy "$LATEST_LOCAL" "$REMOTE_ROOT/"; then
      audit_event "latest_ok" "\"path\":\"$LATEST_LOCAL\",\"dump_sha\":\"${L_SHA}\",\"voc\":$L_VOC,\"sif_changed\":\"$SIF_CHANGED_CSV\""
      audit_sync  "latest_ok" "\"path\":\"$REMOTE_ROOT/LATEST.json\",\"dump_sha\":\"${L_SHA}\",\"voc\":$L_VOC"
    else
      echo "[WARN] LATEST.json rclone copy 실패"
      audit_event "latest_upload_fail"
    fi
  fi
fi

echo
echo "================================================================"
echo "✓ sync-to-drive 완료  (dry-run=$DRY)"
echo "  remote: $REMOTE_ROOT"
echo "  audit:  $AUDIT_FILE"
echo "================================================================"
audit_event "end"
audit_sync "end" "\"elapsed_s\":$(sf_lock_elapsed)"
