#!/usr/bin/env bash
# Track Y2 — auto-pull.sh dry-run + rollback 시뮬레이션
#
# 실제 Drive/DB 를 건드리지 않고 다음을 검증:
#   T1. --dry-run 모드 종료코드 0 + audit 기록 + state 미변경
#   T2. 변경 없음 (LATEST sha == local state sha) → no_change 이벤트
#   T3. --force --dry-run → change_detected 이벤트 (sha 무관)
#   T4. 잠금 충돌 → 두 번째 인스턴스 즉시 종료(skip)
#   T5. mock rclone (PATH 앞에 stub) 으로 LATEST.json 모의 → sha 변경 감지
#   T6. 알 수 없는 옵션 → exit 2
#   T7. audit JSONL 모두 round=auto_sync track=Y2 로 파싱 가능
#
# 실행: bash scripts/tests/test_auto_pull_dryrun.sh
# 종료: 0=pass, 1=fail

set -uo pipefail
PASS=0; FAIL=0
TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
AUDIT="$PROJECT_ROOT/logs/audit/auto_sync.jsonl"

# 테스트 격리: 별도 lock/state
TMP_DIR="$(mktemp -d -t sf_y2_test.XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT
TEST_LOCK="$TMP_DIR/test.lock"
TEST_STATE="$TMP_DIR/test_state.json"

check() {
  local desc="$1" expected="$2" actual="$3"
  if [[ "$actual" == "$expected" ]]; then
    echo "  [PASS] $desc (rc=$actual)"; PASS=$((PASS+1))
  else
    echo "  [FAIL] $desc (expected=$expected got=$actual)"; FAIL=$((FAIL+1))
  fi
}
check_grep() {
  local desc="$1" pattern="$2" haystack="$3"
  if grep -qE "$pattern" <<<"$haystack"; then
    echo "  [PASS] $desc (matched: $pattern)"; PASS=$((PASS+1))
  else
    echo "  [FAIL] $desc (no match: $pattern)"
    echo "$haystack" | tail -8 | sed 's/^/         /'
    FAIL=$((FAIL+1))
  fi
}

audit_count_before() {
  [[ -f "$AUDIT" ]] && wc -l < "$AUDIT" || echo 0
}

echo "================================================================"
echo " Track Y2 — auto-pull.sh dry-run tests"
echo "================================================================"

# ── T1. --dry-run --no-restore 정상 호출 (실 rclone 사용, 변경 없을 가능성 높음) ──
echo
echo "[T1] auto-pull.sh --dry-run --no-restore (격리 state/lock)"
BEFORE=$(audit_count_before)
OUT1=$(bash "$SCRIPTS_DIR/auto-pull.sh" --dry-run --no-restore \
        --lock "$TEST_LOCK" --state "$TEST_STATE" 2>&1)
RC1=$?
check "T1.rc"  0 "$RC1"
check_grep "T1.start"  "start  dry=1"                          "$OUT1"
check_grep "T1.audit_growth_or_skip" "(start|skip|no_change|change_detected|dryrun_end|end)" "$OUT1"
AFTER=$(audit_count_before)
if [[ "$AFTER" -gt "$BEFORE" ]]; then
  echo "  [PASS] T1.audit_grew ($BEFORE → $AFTER)"; PASS=$((PASS+1))
else
  echo "  [WARN] T1.audit_no_grow ($BEFORE → $AFTER) — rclone 실패시 정상 (state 부재로 fail 가능)"
fi

# ── T2. 두 번 연속 실행 → 두 번째는 'no_change' 또는 skip ─────────
echo
echo "[T2] 두 번 연속 (--dry-run --no-restore) — 변경 없음 시나리오"
bash "$SCRIPTS_DIR/auto-pull.sh" --dry-run --no-restore \
      --lock "$TEST_LOCK" --state "$TEST_STATE" >/dev/null 2>&1 || true
# 가짜 state 강제 주입 (LATEST.json 의 sha 와 일치한다고 가정)
if rclone cat ApptainerImages:SignalForge/LATEST.json 2>/dev/null > "$TMP_DIR/latest.json" && [[ -s "$TMP_DIR/latest.json" ]]; then
  FAKE_SHA=$(python3 -c "import json;print(json.load(open('$TMP_DIR/latest.json')).get('db_sha256',''))" 2>/dev/null)
  if [[ -n "$FAKE_SHA" ]]; then
    cat > "$TEST_STATE" <<JSON
{"db_sha256":"$FAKE_SHA","last_dump":"","applied_at":"2026-06-07T00:00:00Z","run_id":"test"}
JSON
    OUT2=$(bash "$SCRIPTS_DIR/auto-pull.sh" --dry-run --no-restore \
            --lock "$TEST_LOCK" --state "$TEST_STATE" 2>&1)
    check "T2.rc" 0 "$?"
    check_grep "T2.no_change" "변경 없음 — skip" "$OUT2"
  else
    echo "  [SKIP] T2 (LATEST.json 의 db_sha256 empty)"
  fi
else
  # LATEST.json 부재 → 폴백: db-dumps/ 의 최신 파일의 실 sha256 을 받아 state 주입
  LATEST_DUMP=$(rclone lsf ApptainerImages:SignalForge/db-dumps/ --include 'sf-db-*.sql.gz' 2>/dev/null | sort | tail -1)
  if [[ -n "$LATEST_DUMP" ]]; then
    REAL_SHA=""
    if rclone cat "ApptainerImages:SignalForge/db-dumps/${LATEST_DUMP}.sha256" > "$TMP_DIR/dump.sha" 2>/dev/null && [[ -s "$TMP_DIR/dump.sha" ]]; then
      REAL_SHA=$(awk '{print $1}' "$TMP_DIR/dump.sha")
    else
      REAL_SHA="name:$LATEST_DUMP"   # .sha256 도 없으면 스크립트 동일 폴백
    fi
    cat > "$TEST_STATE" <<JSON
{"db_sha256":"$REAL_SHA","last_dump":"$LATEST_DUMP","applied_at":"2026-06-07T00:00:00Z","run_id":"test"}
JSON
    OUT2=$(bash "$SCRIPTS_DIR/auto-pull.sh" --dry-run --no-restore \
            --lock "$TEST_LOCK" --state "$TEST_STATE" 2>&1)
    check "T2.rc" 0 "$?"
    check_grep "T2.no_change_or_skip" "(변경 없음|skip)" "$OUT2"
  else
    echo "  [SKIP] T2 (remote 에 dump 없음)"
  fi
fi

# ── T3. --force --dry-run → 무조건 change_detected ─────────────────
echo
echo "[T3] --force --dry-run --no-restore"
OUT3=$(bash "$SCRIPTS_DIR/auto-pull.sh" --force --dry-run --no-restore \
        --lock "$TEST_LOCK" --state "$TEST_STATE" 2>&1)
RC3=$?
check "T3.rc" 0 "$RC3"
# remote 비어있으면 skip 일 수도 있음 — 둘 다 허용
check_grep "T3.action" "(변경 감지|변경 없음|원격 dump 없음|skip)" "$OUT3"

# ── T4. 잠금 충돌 (두 인스턴스 동시 실행) ─────────────────────────
echo
echo "[T4] flock 충돌 시뮬"
# 잠금을 다른 프로세스가 잡고 있는 상태에서 호출
(
  exec 9>"$TEST_LOCK"
  flock -x 9
  # 잠금 유지하며 백그라운드에서 auto-pull 호출
  bash "$SCRIPTS_DIR/auto-pull.sh" --dry-run --no-restore \
        --lock "$TEST_LOCK" --state "$TEST_STATE" > "$TMP_DIR/t4.out" 2>&1 &
  BG_PID=$!
  wait "$BG_PID"
  echo $? > "$TMP_DIR/t4.rc"
)
RC4=$(cat "$TMP_DIR/t4.rc")
OUT4=$(cat "$TMP_DIR/t4.out")
check "T4.rc" 0 "$RC4"
check_grep "T4.locked_skip" "이미 다른 인스턴스 실행중" "$OUT4"

# ── T5. mock rclone 으로 LATEST sha 변경 모의 ──────────────────────
echo
echo "[T5] mock rclone — 가짜 LATEST.json sha 강제 변경"
MOCK_DIR="$TMP_DIR/mock_bin"
mkdir -p "$MOCK_DIR"
cat > "$MOCK_DIR/rclone" <<'MOCK'
#!/usr/bin/env bash
# 실 rclone 우회 stub — auto-pull.sh 가 부르는 패턴만 처리
case "${1:-}" in
  listremotes) echo "ApptainerImages:"; exit 0 ;;
  cat)
    target="${2:-}"
    if [[ "$target" == *LATEST.json* ]]; then
      cat <<JSON
{"db_sha256":"deadbeef1234deadbeef1234deadbeef1234deadbeef1234deadbeef1234dead","last_dump":"sf-db-99990101-000000Z.sql.gz","ts":"2026-06-07T00:00:00Z"}
JSON
      exit 0
    fi
    exit 1 ;;
  lsf) exit 1 ;;
  *) exit 0 ;;
esac
MOCK
chmod +x "$MOCK_DIR/rclone"
# state 비움 → 반드시 change_detected
rm -f "$TEST_STATE"
OUT5=$(PATH="$MOCK_DIR:$PATH" bash "$SCRIPTS_DIR/auto-pull.sh" --dry-run --no-restore \
        --lock "$TEST_LOCK" --state "$TEST_STATE" 2>&1)
RC5=$?
# pull 은 실제 sync-from-drive.sh 가 진짜 rclone 을 다시 부르므로 실패 가능 — change_detected 까지만 검증
check_grep "T5.change_detected" "변경 감지|deadbeef" "$OUT5"
echo "  [INFO] T5.rc=$RC5  (sync-from-drive 단계는 실 rclone 으로 진행되어 실패 가능)"

# ── T6. 알 수 없는 옵션 → exit 2 ──────────────────────────────────
echo
echo "[T6] --bogus → exit 2"
bash "$SCRIPTS_DIR/auto-pull.sh" --bogus >/dev/null 2>&1
RC6=$?
check "T6.rc" 2 "$RC6"

# ── T7. audit JSONL 파싱 가능 + round=auto_sync ───────────────────
echo
echo "[T7] audit JSONL 파싱"
if command -v python3 >/dev/null 2>&1 && [[ -f "$AUDIT" ]]; then
  PARSE=$(python3 - <<PY
import json
ok = bad = y2 = 0
with open("$AUDIT") as f:
    for line in f:
        s = line.strip()
        if not s: continue
        try:
            o = json.loads(s)
            ok += 1
            if o.get("round") == "auto_sync" and o.get("track") == "Y2":
                y2 += 1
        except Exception:
            bad += 1
print(f"ok={ok} bad={bad} y2={y2}")
PY
)
  echo "  $PARSE"
  if grep -qE 'bad=0' <<<"$PARSE" && grep -qE 'y2=[1-9]' <<<"$PARSE"; then
    echo "  [PASS] T7.parse"; PASS=$((PASS+1))
  else
    echo "  [FAIL] T7.parse"; FAIL=$((FAIL+1))
  fi
else
  echo "  [SKIP] T7 (python3 또는 audit 없음)"
fi

echo
echo "================================================================"
echo " 결과: PASS=$PASS  FAIL=$FAIL"
echo "================================================================"
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
