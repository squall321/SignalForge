#!/usr/bin/env bash
# Stage 4 portal_deploy — sync-{to,from}-drive.sh 의 --dry-run 동작 검증.
# 실 업로드/다운로드 없이 구조/exit 코드만 확인.
#
# 사용: bash scripts/tests/test_sync_dryrun.sh
# 종료코드: 0=pass, 1=fail

set -uo pipefail  # -e 끔 — 개별 케이스에서 직접 평가
PASS=0; FAIL=0
TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
AUDIT="$PROJECT_ROOT/logs/audit/portal_deploy.jsonl"

check() {
  local desc="$1" expected_rc="$2" actual_rc="$3" extra="${4:-}"
  if [[ "$actual_rc" == "$expected_rc" ]]; then
    echo "  [PASS] $desc (rc=$actual_rc) $extra"
    PASS=$((PASS+1))
  else
    echo "  [FAIL] $desc (expected rc=$expected_rc, got rc=$actual_rc) $extra"
    FAIL=$((FAIL+1))
  fi
}

check_grep() {
  local desc="$1" pattern="$2" output="$3"
  if echo "$output" | grep -qE "$pattern"; then
    echo "  [PASS] $desc (matched: $pattern)"
    PASS=$((PASS+1))
  else
    echo "  [FAIL] $desc (no match: $pattern)"
    echo "         --- output (last 5) ---"
    echo "$output" | tail -5 | sed 's/^/         /'
    FAIL=$((FAIL+1))
  fi
}

echo "================================================================"
echo " Stage 4 portal_deploy — sync dry-run tests"
echo "================================================================"

# ── 1) sync-to-drive --dry-run ───────────────────────────────────────
echo
echo "[T1] sync-to-drive.sh --dry-run"
OUT1=$(bash "$SCRIPTS_DIR/sync-to-drive.sh" --dry-run 2>&1)
RC1=$?
check "T1.rc"  0 "$RC1"
check_grep "T1.has_start"   "sync-to-drive  \[run="                  "$OUT1"
check_grep "T1.has_dryrun"  "dry-run"                                "$OUT1"
check_grep "T1.has_end"     "sync-to-drive 완료"                     "$OUT1"

# ── 2) sync-to-drive --dry-run --no-db (DB 단계 건너뜀 검증) ─────────
echo
echo "[T2] sync-to-drive.sh --dry-run --no-db"
OUT2=$(bash "$SCRIPTS_DIR/sync-to-drive.sh" --dry-run --no-db 2>&1)
RC2=$?
check "T2.rc" 0 "$RC2"
check_grep "T2.db_skipped" "skip \(--no-db\)" "$OUT2"

# ── 3) sync-from-drive --dry-run ─────────────────────────────────────
echo
echo "[T3] sync-from-drive.sh --dry-run"
OUT3=$(bash "$SCRIPTS_DIR/sync-from-drive.sh" --dry-run 2>&1)
RC3=$?
check "T3.rc"  0 "$RC3"
check_grep "T3.has_start"  "sync-from-drive  \[run=" "$OUT3"
check_grep "T3.has_end"    "sync-from-drive 완료"   "$OUT3"

# ── 4) 알 수 없는 옵션 → exit 2 ──────────────────────────────────────
echo
echo "[T4] sync-to-drive.sh --bogus"
bash "$SCRIPTS_DIR/sync-to-drive.sh" --bogus >/dev/null 2>&1
RC4=$?
check "T4.rc" 2 "$RC4"

# ── 5) audit JSONL 가 추가 기록되었는지 ──────────────────────────────
echo
echo "[T5] audit JSONL 기록"
if [[ -f "$AUDIT" ]]; then
  RECENT=$(tail -20 "$AUDIT" | grep -c '"round":"portal_deploy".*"track":"S4"' || true)
  if [[ "$RECENT" -ge 4 ]]; then
    echo "  [PASS] T5.audit (최근 20줄에 portal_deploy/S4 이벤트 $RECENT 건)"
    PASS=$((PASS+1))
  else
    echo "  [FAIL] T5.audit (기대 ≥4, 실제 $RECENT) — $AUDIT"
    FAIL=$((FAIL+1))
  fi
else
  echo "  [FAIL] T5.audit ($AUDIT 없음)"
  FAIL=$((FAIL+1))
fi

# ── 6) JSON 라인이 파싱 가능한지 (python 으로) ────────────────────────
echo
echo "[T6] audit JSONL 파싱 가능성"
if command -v python3 >/dev/null 2>&1 && [[ -f "$AUDIT" ]]; then
  python3 -c "
import json, sys
ok = 0; bad = 0
with open('$AUDIT') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try:
            o = json.loads(line)
            if o.get('round') == 'portal_deploy' and o.get('track') == 'S4':
                ok += 1
        except Exception as e:
            bad += 1
print(f'parsed_ok={ok} parsed_bad={bad}')
sys.exit(0 if bad == 0 and ok > 0 else 1)
"
  RC6=$?
  check "T6.parse" 0 "$RC6"
else
  echo "  [SKIP] T6.parse (python3 또는 audit 부재)"
fi

echo
echo "================================================================"
echo " 결과: PASS=$PASS  FAIL=$FAIL"
echo "================================================================"
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
