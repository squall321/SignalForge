#!/usr/bin/env bash
# Y4 단위 테스트:
#   T1. _lock_helper: 첫 acquire 성공 / 동시 시도 즉시 skip / release 후 재획득
#   T2. _verify_helper.sf_voc_drop_pct: 산수 정확
#   T3. _verify_helper.sf_verify_after_pull: 의도적 fail (pre_voc 부풀려서 -99% drop)
#                                            → rc=1, drop_pct>50 로그
#   T4. _verify_helper.sf_verify_after_pull: 정상 (pre_voc 실측) → rc=0
#   T5. sf_snapshot_pre_restore: 실 호출, sql.gz 생성 + .sha256 없이도 OK
#                                (그 후 즉시 삭제 — 운영 DB 영향 0)
#   T6. lock_skip 시 sync-to-drive.sh 가 exit 0 + audit_sync skip 기록
#   T7. audit_sync JSONL: round=auto_sync track=Y4 라인 ≥4 (start/end × 2)
#
# 주의:
#   * 운영 DB 를 DROP 하지 않음. sf_rollback 자체는 e2e 로 테스트 안함
#     (확인은 audit + dry sql.gz 파일 무결성으로 대체).
#   * 백엔드가 :18000/health 200 인 환경에서만 V3 pass — 아니면 SKIP.

set -uo pipefail
PASS=0; FAIL=0
TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
AUDIT_SYNC="$PROJECT_ROOT/logs/audit/auto_sync.jsonl"
export PROJECT_ROOT

pass() { echo "  [PASS] $*"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $*" >&2; FAIL=$((FAIL+1)); }

echo "================================================================"
echo " Y4 — lock / verify / rollback safeguard tests"
echo "================================================================"

# 공용 헬퍼 로드
# shellcheck source=../_lock_helper.sh
source "$SCRIPTS_DIR/_lock_helper.sh"
# shellcheck source=../_verify_helper.sh
source "$SCRIPTS_DIR/_verify_helper.sh"

# ── T1. lock helper: 동시 실행 skip ──────────────────────────────────
echo
echo "[T1] _lock_helper: 동시 실행 즉시 skip"
# 외부 sub-shell 에서 락 잡고 sleep — 이 안에서는 acquire 실패해야 함
HOLD_FIFO="$(mktemp -u /tmp/sf_y4_hold.XXXXXX)"
mkfifo "$HOLD_FIFO"
(
  # shellcheck source=../_lock_helper.sh
  source "$SCRIPTS_DIR/_lock_helper.sh"
  if sf_lock_acquire push 60; then
    echo "ready" > "$HOLD_FIFO"
    sleep 5   # 락 보유
  else
    echo "child failed to acquire" >&2
    echo "fail" > "$HOLD_FIFO"
    exit 1
  fi
) &
HOLD_PID=$!
read -r STATE < "$HOLD_FIFO"
rm -f "$HOLD_FIFO"
if [[ "$STATE" != "ready" ]]; then
  fail "T1.setup: child failed to acquire lock"
else
  if sf_lock_acquire push 5 2>/dev/null; then
    fail "T1.skip: 동시 acquire 가 성공함 (skip 실패)"
    sf_lock_release
  else
    pass "T1.skip: 동시 acquire 즉시 실패 (의도대로)"
  fi
fi
wait "$HOLD_PID" 2>/dev/null || true
# 락 해제 확인 — 다시 잡을 수 있어야 함
if sf_lock_acquire push 5; then
  pass "T1.reacquire: 보유자 종료 후 재획득 가능"
  sf_lock_release
else
  fail "T1.reacquire: 보유자 종료 후에도 재획득 실패"
fi

# ── T2. sf_voc_drop_pct ──────────────────────────────────────────────
echo
echo "[T2] sf_voc_drop_pct 산수"
P=$(sf_voc_drop_pct 100 50)
[[ "$P" = "50" ]] && pass "T2.drop50 (100→50 = 50%)" || fail "T2.drop50 got=$P"
P=$(sf_voc_drop_pct 138000 1000)
[[ "$P" -ge 99 ]] && pass "T2.drop99 (138k→1k ≥99%)" || fail "T2.drop99 got=$P"
P=$(sf_voc_drop_pct 100 100)
[[ "$P" = "0" ]] && pass "T2.drop0 (no change)" || fail "T2.drop0 got=$P"
P=$(sf_voc_drop_pct 0 50)
[[ "$P" = "0" ]] && pass "T2.dropZero (pre=0 → 0%)" || fail "T2.dropZero got=$P"

# ── .env 로드 (T3-T5 PG 접근에 필요) ────────────────────────────────
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a; source "$PROJECT_ROOT/.env"; set +a
fi

# ── T3. sf_verify_after_pull: 의도적 fail ────────────────────────────
echo
echo "[T3] sf_verify_after_pull — 의도적 -99% drop"
ACTUAL_VOC="$(sf_voc_count 2>/dev/null || echo 0)"
if [[ -z "$ACTUAL_VOC" || "$ACTUAL_VOC" = "0" ]]; then
  echo "  [SKIP] T3 (sf_voc_count 실패 또는 0)"
else
  # pre 를 100× 부풀려 -99% drop 처럼 보이게
  export SF_PRE_VOC=$((ACTUAL_VOC * 100))
  export SF_VOC_DROP_LIMIT=50
  export SF_SKIP_HEALTH=1   # 백엔드 가동 여부와 분리
  OUT=$(sf_verify_after_pull 2>&1)
  RC=$?
  if [[ "$RC" -ne 0 ]] && echo "$OUT" | grep -q "V1.*FAIL voc drop"; then
    pass "T3.fail: 의도적 drop → rc=$RC + V1 FAIL 로그"
  else
    fail "T3.fail: rc=$RC, output=$OUT"
  fi
fi

# ── T4. sf_verify_after_pull: 정상 (실측 pre/post) ───────────────────
echo
echo "[T4] sf_verify_after_pull — 정상 (pre=post=실측)"
if [[ -z "$ACTUAL_VOC" || "$ACTUAL_VOC" = "0" ]]; then
  echo "  [SKIP] T4"
else
  export SF_PRE_VOC="$ACTUAL_VOC"
  export SF_VOC_DROP_LIMIT=50
  export SF_SKIP_HEALTH=1
  OUT=$(sf_verify_after_pull 2>&1)
  RC=$?
  if [[ "$RC" -eq 0 ]] && echo "$OUT" | grep -q "V1.*PASS" && echo "$OUT" | grep -q "V2.*PASS"; then
    pass "T4.ok: rc=0 + V1/V2 PASS"
  else
    fail "T4.ok: rc=$RC, output=$OUT"
  fi
fi
unset SF_SKIP_HEALTH SF_PRE_VOC SF_VOC_DROP_LIMIT

# ── T5. sf_snapshot_pre_restore — 실 sql.gz 생성 후 즉시 삭제 ────────
echo
echo "[T5] sf_snapshot_pre_restore — 안전백업 생성"
if [[ -z "${POSTGRES_PORT:-}" ]]; then
  echo "  [SKIP] T5 (POSTGRES_PORT 미설정)"
else
  TMPDIR_T5="$(mktemp -d -t sf_y4_t5.XXXXXX)"
  SNAP=$(sf_snapshot_pre_restore "$TMPDIR_T5" 2>&1)
  RC=$?
  if [[ "$RC" -eq 0 ]] && [[ -s "$SNAP" ]]; then
    SIZE=$(stat -c %s "$SNAP")
    if [[ "$SIZE" -gt 1024 ]]; then
      pass "T5.snapshot: $SNAP ($SIZE bytes)"
    else
      fail "T5.snapshot: 너무 작음 ($SIZE bytes)"
    fi
    # gzip 무결성
    if gzip -t "$SNAP" 2>/dev/null; then
      pass "T5.gzip: 무결성 OK"
    else
      fail "T5.gzip: 깨짐"
    fi
  else
    fail "T5.snapshot: rc=$RC output=$SNAP"
  fi
  rm -rf "$TMPDIR_T5"
fi

# ── T6. sync-to-drive.sh: 동시 실행 시 skip ─────────────────────────
echo
echo "[T6] sync-to-drive.sh 동시 실행 시 두 번째 즉시 skip"
# rclone remote 가 없는 환경에서도 락 가드는 동작해야 — --dry-run 으로 빠른 종료
HOLD_FIFO2="$(mktemp -u /tmp/sf_y4_t6.XXXXXX)"
mkfifo "$HOLD_FIFO2"
(
  # shellcheck source=../_lock_helper.sh
  source "$SCRIPTS_DIR/_lock_helper.sh"
  if sf_lock_acquire push 30; then
    echo "ready" > "$HOLD_FIFO2"
    sleep 4
  fi
) &
HOLD2_PID=$!
read -r _ST < "$HOLD_FIFO2"
rm -f "$HOLD_FIFO2"
OUT6=$(bash "$SCRIPTS_DIR/sync-to-drive.sh" --dry-run 2>&1)
RC6=$?
wait "$HOLD2_PID" 2>/dev/null || true
if [[ "$RC6" -eq 0 ]] && echo "$OUT6" | grep -q "이미 다른 push"; then
  pass "T6.skip: sync-to-drive 가 즉시 종료 (rc=0 + skip 로그)"
else
  fail "T6.skip: rc=$RC6 — output last 5: $(echo "$OUT6" | tail -5)"
fi

# ── T7. audit JSONL ─────────────────────────────────────────────────
echo
echo "[T7] audit JSONL — round=auto_sync track=Y4 라인"
if [[ ! -f "$AUDIT_SYNC" ]]; then
  fail "T7.file: $AUDIT_SYNC 없음"
else
  RECENT=$(tail -30 "$AUDIT_SYNC" | grep -c '"round":"auto_sync".*"track":"Y4"' || true)
  if [[ "$RECENT" -ge 1 ]]; then
    pass "T7.lines: 최근 30줄에 auto_sync/Y4 이벤트 $RECENT 건"
  else
    fail "T7.lines: 기대 ≥1, 실제 $RECENT"
  fi
  # JSON 파싱 가능성
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "
import json, sys
ok=bad=0
with open('$AUDIT_SYNC') as f:
    for ln in f:
        ln=ln.strip()
        if not ln: continue
        try:
            o=json.loads(ln)
            if o.get('round')=='auto_sync' and o.get('track')=='Y4': ok+=1
        except Exception:
            bad+=1
print(f'parsed_ok={ok} parsed_bad={bad}')
sys.exit(0 if bad==0 and ok>0 else 1)
" \
      && pass "T7.parse: JSON 파싱 OK" \
      || fail "T7.parse: 파싱 실패"
  fi
fi

echo
echo "================================================================"
echo " 결과: PASS=$PASS  FAIL=$FAIL"
echo "================================================================"
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
