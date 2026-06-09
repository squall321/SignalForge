#!/usr/bin/env bash
# Track Z1 (round=sync_polish) — scripts/sync-to-drive.sh 의 LATEST.json 통합 검증.
#
# 검증:
#   T1. --dry-run 모드에서 [4/4] LATEST.json 스테이지 출력 확인
#   T2. --dry-run 모드에서 LATEST.json 로컬 산출 (logs/sync/LATEST.json)
#   T3. 산출된 LATEST.json 이 Y3 contract 키 (sync_run_id, timestamp,
#       db_dump{sha256,size_mb,filename}, sif_sha256sums, sif_changed,
#       voc_count, alembic_head, source_host) 를 모두 가짐
#   T4. audit JSONL 에 latest_dryrun (또는 latest_ok) 이벤트 기록
#   T5. mock rclone 으로 실 모드 시뮬 — latest_ok event 기록 + mock 호출
#       대상이 ApptainerImages:SignalForge/ 와 LATEST.json 포함
#
# 사용: bash scripts/tests/test_sync_to_drive_latest.sh
# 종료: 0=all pass, 1=fail

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
SYNC="$SCRIPTS_DIR/sync-to-drive.sh"
LATEST_LOCAL="$PROJECT_ROOT/logs/sync/LATEST.json"
AUDIT="$PROJECT_ROOT/logs/audit/portal_deploy.jsonl"

[[ -x "$SYNC" ]] || { echo "[FAIL] $SYNC 미설치/미실행"; exit 1; }
command -v jq >/dev/null || { echo "[FAIL] jq 미설치"; exit 1; }

PASS=0; FAIL=0
ok() { echo "  [PASS] $*"; PASS=$((PASS+1)); }
ng() { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }

echo "================================================================"
echo " sync_polish / Z1 — sync-to-drive.sh LATEST.json 통합 tests"
echo "================================================================"

# ── T1. --dry-run 모드에서 [4/4] 스테이지 출력 ───────────────────────
echo
echo "[T1] --dry-run [4/4] LATEST.json 스테이지 출력"
OUT="$(bash "$SYNC" --dry-run --no-db --no-sif --no-env 2>&1)"
RC=$?
[[ $RC -eq 0 ]] && ok "T1.rc=0" || ng "T1.rc=$RC"
echo "$OUT" | grep -qE '\[4/4\] LATEST.json' && ok "T1.has_stage_4" \
  || { ng "T1.no_stage_4"; echo "$OUT" | tail -10 | sed 's/^/        /'; }
echo "$OUT" | grep -qE '\(dry-run\) rclone copy 생략' \
  && ok "T1.dryrun_skip_message" || ng "T1.no_dryrun_skip_message"

# ── T2. LATEST.json 로컬 산출 ────────────────────────────────────────
echo
echo "[T2] LATEST.json 로컬 산출"
[[ -s "$LATEST_LOCAL" ]] && ok "T2.file_nonempty ($LATEST_LOCAL)" \
  || ng "T2.file_empty_or_missing ($LATEST_LOCAL)"
jq -e . "$LATEST_LOCAL" >/dev/null 2>&1 && ok "T2.json_valid" || ng "T2.json_invalid"

# ── T3. Y3 contract 키 검증 ──────────────────────────────────────────
echo
echo "[T3] Y3 contract 키 검증"
EXPECT='["alembic_head","db_dump","sif_changed","sif_sha256sums","source_host","sync_run_id","timestamp","voc_count"]'
ACTUAL="$(jq -c 'keys' "$LATEST_LOCAL")"
if [[ "$ACTUAL" == "$EXPECT" ]]; then
  ok "T3.top_keys ($ACTUAL)"
else
  ng "T3.top_keys mismatch  expected=$EXPECT  actual=$ACTUAL"
fi
jq -e '.db_dump | has("sha256") and has("size_mb") and has("filename")' "$LATEST_LOCAL" >/dev/null \
  && ok "T3.db_dump_shape" || ng "T3.db_dump_shape"
jq -e '.timestamp | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")' "$LATEST_LOCAL" >/dev/null \
  && ok "T3.timestamp_iso_utc" || ng "T3.timestamp_iso_utc"
jq -e '.sync_run_id | test("^[0-9]+-[0-9]+$")' "$LATEST_LOCAL" >/dev/null \
  && ok "T3.sync_run_id_shape" || ng "T3.sync_run_id_shape"

# ── T4. audit JSONL 에 latest_dryrun 이벤트 ──────────────────────────
echo
echo "[T4] audit JSONL latest_dryrun 기록"
if [[ -f "$AUDIT" ]]; then
  CNT=$(tail -30 "$AUDIT" | grep -c '"event":"latest_dryrun"' || true)
  if [[ "$CNT" -ge 1 ]]; then
    ok "T4.audit_latest_dryrun ($CNT 건)"
  else
    ng "T4.no_latest_dryrun_event (최근 30줄)"
  fi
else
  ng "T4.audit_missing ($AUDIT)"
fi

# ── T5. mock rclone 으로 실 모드 시뮬 ────────────────────────────────
echo
echo "[T5] mock rclone 실 모드 시뮬레이션"
MOCK_DIR="$(mktemp -d -t sf-mock-rclone.XXXXXX)"
trap 'rm -rf "$MOCK_DIR"' EXIT
MOCK_LOG="$MOCK_DIR/rclone_calls.log"

cat > "$MOCK_DIR/rclone" <<'MOCKEOF'
#!/usr/bin/env bash
# mock rclone — listremotes 는 진짜 응답 흉내, copy/sync/lsf/purge 는 NOP 후 로그 기록.
LOG="${MOCK_LOG:?MOCK_LOG required}"
echo "rclone $*" >> "$LOG"
case "$1" in
  listremotes) echo "ApptainerImages:" ;;
  lsf)         echo "" ;;     # 보존 폴더 없음
  *)           : ;;           # copy/sync/purge → 성공 처리
esac
exit 0
MOCKEOF
chmod +x "$MOCK_DIR/rclone"
export MOCK_LOG

# DB/SIF/ENV 단계는 끔 — LATEST 단계 단독 검증.
OUT5="$(PATH="$MOCK_DIR:$PATH" MOCK_LOG="$MOCK_LOG" \
  bash "$SYNC" --no-db --no-sif --no-env 2>&1)"
RC5=$?
[[ $RC5 -eq 0 ]] && ok "T5.rc=0" || { ng "T5.rc=$RC5"; echo "$OUT5" | tail -10 | sed 's/^/        /'; }

if [[ -s "$MOCK_LOG" ]]; then
  if grep -qE 'rclone copy .+/logs/sync/LATEST.json ApptainerImages:SignalForge/' "$MOCK_LOG"; then
    ok "T5.mock_rclone_copy_latest_invoked"
  else
    ng "T5.mock_rclone_copy_latest_missing"
    sed 's/^/        /' "$MOCK_LOG"
  fi
else
  ng "T5.mock_log_empty"
fi

# audit latest_ok 이벤트 (실 모드 → latest_ok)
if tail -30 "$AUDIT" | grep -q '"event":"latest_ok"'; then
  ok "T5.audit_latest_ok"
else
  ng "T5.audit_no_latest_ok"
fi

echo
echo "================================================================"
echo " 결과: PASS=$PASS  FAIL=$FAIL"
echo "================================================================"
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
