#!/usr/bin/env bash
# SignalForge — 단방향 배포 동기화 (Google Drive → 이 호스트)
#
# Stage 4 portal_deploy 진입점. 새 서버에 SignalForge 를 띄울 때 사용:
#   1. git clone <repo> && cd SignalForge
#   2. (rclone 미설정이면) bash scripts/drive-sync/setup-drive-sync.sh
#   3. bash scripts/sync-from-drive.sh          # SIF + DB dump + .env.example 모두 받음
#   4. cp .env.example .env  &&  $EDITOR .env   # secret 만 수정
#   5. bash scripts/up.sh                       # 가동 (SIF 가 이미 있으면 build 스킵)
#   6. bash scripts/drive-sync/restore-db.sh ./backups/sf-db-latest.sql.gz --yes
#
# 사용:
#   bash scripts/sync-from-drive.sh                   # 전체 (SIF + DB + env), restore 는 별도
#   bash scripts/sync-from-drive.sh --dry-run         # 무엇이 받아질지만 확인
#   bash scripts/sync-from-drive.sh --no-sif          # DB + env 만
#   bash scripts/sync-from-drive.sh --no-db
#   bash scripts/sync-from-drive.sh --no-env
#   bash scripts/sync-from-drive.sh --restore         # DB 받자마자 자동 restore (drive-sync/sync-from-drive.sh 위임)
#
# audit: logs/audit/portal_deploy.jsonl  (round=portal_deploy track=S4)

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
SIF_DIR="$PROJECT_ROOT/apptainer/sif"
DUMP_DIR="$PROJECT_ROOT/backups"
DRIVE_SYNC_DIR="$SCRIPTS_DIR/drive-sync"
AUDIT_DIR="$PROJECT_ROOT/logs/audit"
AUDIT_FILE="$AUDIT_DIR/portal_deploy.jsonl"
AUDIT_SYNC_FILE="$AUDIT_DIR/auto_sync.jsonl"

# Y4 — flock 가드 (10분 timeout) + 검증/롤백 헬퍼
# shellcheck source=./_lock_helper.sh
source "$SCRIPTS_DIR/_lock_helper.sh"
# shellcheck source=./_verify_helper.sh
source "$SCRIPTS_DIR/_verify_helper.sh"

# ── 기본 옵션 ────────────────────────────────────────────────────────
DRY=0
WITH_DB=1
WITH_SIF=1
WITH_ENV=1
DO_RESTORE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)  DRY=1 ;;
    --no-db)    WITH_DB=0 ;;
    --no-sif)   WITH_SIF=0 ;;
    --no-env)   WITH_ENV=0 ;;
    --restore)  DO_RESTORE=1 ;;
    -h|--help)
      sed -n '1,30p' "$0"; exit 0 ;;
    *) echo "[ERROR] unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$AUDIT_DIR" "$SIF_DIR" "$DUMP_DIR"
RUN_ID="$(date -u +%s)-$$"

audit_event() {
  local event="$1"; shift
  local extra="${1:-}"
  local line
  line=$(printf '{"ts":"%s","round":"portal_deploy","track":"S4","run_id":"%s","script":"sync-from-drive","dry_run":%d,"event":"%s"' \
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
  line=$(printf '{"ts":"%s","round":"auto_sync","track":"Y4","run_id":"%s","script":"sync-from-drive","dry_run":%d,"event":"%s"' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$RUN_ID" "$DRY" "$event")
  [[ -n "$extra" ]] && line="$line,$extra"
  line="$line}"
  echo "$line" >> "$AUDIT_SYNC_FILE"
}

echo "================================================================"
echo " SignalForge sync-from-drive  [run=$RUN_ID  dry=$DRY]"
echo "   db=$WITH_DB  sif=$WITH_SIF  env=$WITH_ENV  restore=$DO_RESTORE"
echo "================================================================"

# Y4 — flock 가드. 동시 실행 즉시 종료 (exit 0, audit skip).
if ! sf_lock_acquire pull 600; then
  audit_sync "lock_skip" "\"kind\":\"pull\",\"file\":\"$(sf_lock_path pull)\""
  echo "[SKIP] 이미 다른 pull 이 실행 중 — 종료"
  exit 0
fi
audit_sync "start" "\"db\":$WITH_DB,\"sif\":$WITH_SIF,\"env\":$WITH_ENV,\"restore\":$DO_RESTORE,\"lock\":\"$SF_LOCK_FILE\",\"timeout\":$SF_LOCK_TIMEOUT"

audit_event "start" "\"db\":$WITH_DB,\"sif\":$WITH_SIF,\"env\":$WITH_ENV,\"restore\":$DO_RESTORE"

# ── rclone & remote ──────────────────────────────────────────────────
command -v rclone >/dev/null || { echo "[ERROR] rclone 미설치"; audit_event "fail" "\"reason\":\"no_rclone\""; exit 1; }

REMOTE="${SF_DRIVE_REMOTE:-ApptainerImages}"
PROJ="${SF_DRIVE_PROJECT:-SignalForge}"
REMOTE_ROOT="${REMOTE}:${PROJ}"

if ! rclone listremotes 2>/dev/null | grep -qx "${REMOTE}:"; then
  echo "[ERROR] rclone remote 미설정: $REMOTE  →  bash scripts/drive-sync/setup-drive-sync.sh"
  audit_event "fail" "\"reason\":\"no_remote\",\"remote\":\"$REMOTE\""
  exit 1
fi
echo "[OK] remote=$REMOTE  root=$REMOTE_ROOT"

# ── 1) SIF 묶음 ──────────────────────────────────────────────────────
if [[ $WITH_SIF -eq 1 ]]; then
  echo
  echo "▶ [1/3] SIF ← $REMOTE_ROOT/sif/latest/ (없으면 sif-* 최신)"
  SRC="$REMOTE_ROOT/sif/latest"
  if ! rclone lsf "$SRC/" 2>/dev/null | grep -qE '\.sif$'; then
    NEWEST="$(rclone lsf --dirs-only "$REMOTE_ROOT/" 2>/dev/null \
              | sed 's#/$##' | grep -E '^sif-' | sort | tail -n1 || true)"
    if [[ -z "$NEWEST" ]]; then
      echo "[WARN] $REMOTE_ROOT 에 SIF 없음 — Stage 1/2 빌드 후 sync-to-drive 가 먼저 필요"
      audit_event "sif_skip" "\"reason\":\"no_remote_sif\""
      SRC=""
    else
      SRC="$REMOTE_ROOT/$NEWEST"
    fi
  fi

  if [[ -n "$SRC" ]]; then
    echo "  source: $SRC"
    if [[ $DRY -eq 1 ]]; then
      echo "  (dry-run) 받을 파일 목록:"
      rclone lsf "$SRC/" 2>/dev/null | sed 's/^/    /'
      audit_event "sif_dryrun" "\"src\":\"$SRC\""
    else
      STAGE="$(mktemp -d -t sf-sif-pull.XXXXXX)"
      trap 'rm -rf "$STAGE"' EXIT
      rclone copy --progress "$SRC/" "$STAGE/"
      if [[ -f "$STAGE/SHA256SUMS" ]]; then
        ( cd "$STAGE" && sha256sum -c SHA256SUMS ) \
          || { echo "[ERROR] sha256 mismatch — abort"; audit_event "fail" "\"reason\":\"sha_mismatch\""; exit 1; }
        echo "  [OK] sha256 검증 통과"
      else
        echo "  [WARN] SHA256SUMS 없음 — 검증 스킵"
      fi
      cp "$STAGE"/*.sif "$SIF_DIR/" 2>/dev/null || true
      SIF_COUNT=$(ls "$STAGE"/*.sif 2>/dev/null | wc -l)
      echo "  → staged $SIF_COUNT sif → $SIF_DIR/"
      audit_event "sif_ok" "\"count\":$SIF_COUNT,\"src\":\"$SRC\""
    fi
  fi
else
  echo "▶ [1/3] SIF  ── skip (--no-sif)"
fi

# ── 2) DB dump (최신 sf-db-*.sql.gz) ─────────────────────────────────
if [[ $WITH_DB -eq 1 ]]; then
  echo
  echo "▶ [2/3] DB dump ← $REMOTE_ROOT/db-dumps/"
  if [[ $DO_RESTORE -eq 1 && $DRY -eq 0 ]]; then
    # restore 까지 한방에 — drive-sync/sync-from-drive.sh 위임
    if [[ ! -x "$DRIVE_SYNC_DIR/sync-from-drive.sh" ]]; then
      echo "[ERROR] $DRIVE_SYNC_DIR/sync-from-drive.sh 없음"
      audit_event "fail" "\"reason\":\"no_helper\""
      exit 1
    fi

    # ── Y4 안전망 ──────────────────────────────────────────────────
    # .env 로드 (verify_helper 가 POSTGRES_* 를 요구)
    if [[ -f "$PROJECT_ROOT/.env" ]]; then
      set -a; source "$PROJECT_ROOT/.env"; set +a
    fi
    # 1) 사전 측정
    PRE_VOC="$(sf_voc_count 2>/dev/null || echo 0)"
    echo "  [Y4] pre voc_records=$PRE_VOC"
    audit_sync "pre_measure" "\"pre_voc\":$PRE_VOC"
    # 2) 안전백업
    SAFETY=""
    if [[ "$PRE_VOC" -gt 0 ]]; then
      SAFETY="$(sf_snapshot_pre_restore "$DUMP_DIR" || true)"
      if [[ -n "$SAFETY" && -f "$SAFETY" ]]; then
        SAFE_SIZE="$(stat -c %s "$SAFETY" 2>/dev/null || echo 0)"
        echo "  [Y4] safety snapshot: $SAFETY ($SAFE_SIZE bytes)"
        audit_sync "safety_snapshot" "\"path\":\"$SAFETY\",\"bytes\":$SAFE_SIZE"
      else
        echo "  [Y4] WARN safety snapshot 실패 — 롤백 불가, 그래도 진행"
        audit_sync "safety_fail"
      fi
    else
      echo "  [Y4] pre_voc=0 — fresh DB, safety 스킵"
      audit_sync "safety_skip" "\"reason\":\"fresh_db\""
    fi

    # 3) 실 restore 위임
    ( cd "$DRIVE_SYNC_DIR" && bash sync-from-drive.sh )
    RESTORE_RC=$?
    audit_event "db_restored"

    # 4) 검증
    if [[ $RESTORE_RC -eq 0 ]]; then
      export SF_PRE_VOC="$PRE_VOC"
      export SF_VOC_DROP_LIMIT="${SF_VOC_DROP_LIMIT:-50}"
      echo "  [Y4] verify_after_pull ..."
      if sf_verify_after_pull; then
        audit_sync "verify_ok" "\"pre_voc\":$PRE_VOC,\"post_voc\":${SF_VERIFY_POST_VOC:-0},\"drop_pct\":${SF_VERIFY_DROP_PCT:-0},\"db_head\":\"${SF_VERIFY_DB_HEAD:-}\""
      else
        echo "  [Y4] FAIL — 자동 롤백 시도"
        audit_sync "verify_fail" "\"pre_voc\":$PRE_VOC,\"post_voc\":${SF_VERIFY_POST_VOC:-0},\"drop_pct\":${SF_VERIFY_DROP_PCT:-0},\"db_head\":\"${SF_VERIFY_DB_HEAD:-}\""
        if [[ -n "$SAFETY" && -f "$SAFETY" ]]; then
          if sf_rollback "$SAFETY"; then
            audit_sync "rollback_ok" "\"safety\":\"$SAFETY\""
            echo "  [Y4] 롤백 성공 — 원본 DB 복원됨"
            exit 1
          else
            audit_sync "rollback_fail" "\"safety\":\"$SAFETY\""
            echo "  [Y4] 롤백 실패 — 수동 개입 필요" >&2
            exit 2
          fi
        else
          audit_sync "rollback_skip" "\"reason\":\"no_safety\""
          echo "  [Y4] safety 없음 — 롤백 불가" >&2
          exit 1
        fi
      fi
    else
      audit_sync "restore_fail" "\"rc\":$RESTORE_RC"
      echo "  [Y4] restore 실패 (rc=$RESTORE_RC)" >&2
      exit "$RESTORE_RC"
    fi
  else
    # 다운로드만
    LATEST=$(rclone lsf "$REMOTE_ROOT/db-dumps/" --include 'sf-db-*.sql.gz' 2>/dev/null | sort | tail -1)
    if [[ -z "$LATEST" ]]; then
      echo "[WARN] $REMOTE_ROOT/db-dumps/ 에 sf-db-*.sql.gz 없음"
      audit_event "db_skip" "\"reason\":\"no_remote_dump\""
    else
      echo "  최신 dump: $LATEST"
      if [[ $DRY -eq 1 ]]; then
        echo "  (dry-run) 다운로드 생략"
        audit_event "db_dryrun" "\"latest\":\"$LATEST\""
      else
        rclone copy "$REMOTE_ROOT/db-dumps/$LATEST"           "$DUMP_DIR/" --progress
        rclone copy "$REMOTE_ROOT/db-dumps/${LATEST}.sha256"  "$DUMP_DIR/" 2>/dev/null || true
        if [[ -f "$DUMP_DIR/${LATEST}.sha256" ]]; then
          ( cd "$DUMP_DIR" && sha256sum -c "${LATEST}.sha256" ) \
            || { echo "[ERROR] sha256 mismatch — abort"; audit_event "fail" "\"reason\":\"db_sha_mismatch\""; exit 1; }
          echo "  [OK] sha256 검증 통과"
        fi
        # 편의: latest 심볼릭
        ( cd "$DUMP_DIR" && ln -sf "$LATEST" sf-db-latest.sql.gz )
        echo "  symlink: $DUMP_DIR/sf-db-latest.sql.gz → $LATEST"
        audit_event "db_ok" "\"latest\":\"$LATEST\""
        echo
        echo "  ▶ 다음 단계 (수동 restore):"
        echo "      bash $DRIVE_SYNC_DIR/restore-db.sh $DUMP_DIR/sf-db-latest.sql.gz --yes"
        echo "    또는 한번에:  bash scripts/sync-from-drive.sh --restore"
      fi
    fi
  fi
else
  echo "▶ [2/3] DB dump  ── skip (--no-db)"
fi

# ── 3) .env.example ──────────────────────────────────────────────────
if [[ $WITH_ENV -eq 1 ]]; then
  echo
  echo "▶ [3/3] .env.example ← $REMOTE_ROOT/env/"
  if [[ $DRY -eq 1 ]]; then
    echo "  (dry-run) 받을 파일:"
    rclone lsf "$REMOTE_ROOT/env/" 2>/dev/null | sed 's/^/    /' || echo "    (remote env/ 비어있음)"
    audit_event "env_dryrun"
  else
    rclone copy "$REMOTE_ROOT/env/.env.example" "$PROJECT_ROOT/" 2>/dev/null || {
      echo "  [WARN] remote 에 .env.example 없음 — 로컬 것 사용"
      audit_event "env_skip" "\"reason\":\"no_remote_env\""
    }
    if [[ -f "$PROJECT_ROOT/.env.example" && ! -f "$PROJECT_ROOT/.env" ]]; then
      echo "  → 다음: cp $PROJECT_ROOT/.env.example $PROJECT_ROOT/.env  &&  \$EDITOR .env"
    fi
    audit_event "env_ok"
  fi
else
  echo "▶ [3/3] .env.example  ── skip (--no-env)"
fi

echo
echo "================================================================"
echo "✓ sync-from-drive 완료  (dry-run=$DRY)"
echo "  sif:    $SIF_DIR"
echo "  dumps:  $DUMP_DIR"
echo "  audit:  $AUDIT_FILE"
echo
if [[ $DRY -eq 0 && $DO_RESTORE -eq 0 ]]; then
  echo "  ▶ 다음:"
  echo "      1. cp .env.example .env  &&  \$EDITOR .env"
  echo "      2. bash scripts/up.sh"
  echo "      3. bash scripts/drive-sync/restore-db.sh $DUMP_DIR/sf-db-latest.sql.gz --yes"
fi
echo "================================================================"
audit_event "end"
audit_sync "end" "\"elapsed_s\":$(sf_lock_elapsed)"
