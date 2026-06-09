#!/usr/bin/env bash
# Track Y3 (round=auto_sync) — scripts/lib/latest-meta.sh 단위 테스트.
#
# 검증:
#   T1. build  → 유효 JSON, 필수 키 존재
#   T2. build  → jq 로 schema 형상 검증 (db_dump/sif_sha256sums/alembic_head 등)
#   T3. diff   → 동일 메타 비교 시 rc=0, reason=same
#   T4. diff   → db_dump.sha256 변화 시 rc=1, reason=delta, .db=true
#   T5. diff   → sif_sha256sums.backend 변화 시 sif_changed=["backend"]
#   T6. diff   → 로컬 메타 부재 시 (첫 sync) rc=1, sif_changed 4종 전부
#
# 사용: bash scripts/tests/test_latest_meta.sh
# 종료: 0=all pass, 1=fail

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
LIB="$SCRIPTS_DIR/lib/latest-meta.sh"

[[ -x "$LIB" ]] || { echo "[FAIL] $LIB 미설치/미실행"; exit 1; }
command -v jq >/dev/null || { echo "[FAIL] jq 미설치"; exit 1; }

PASS=0; FAIL=0
ok()   { echo "  [PASS] $*"; PASS=$((PASS+1)); }
ng()   { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
WORK="$(mktemp -d -t sf-latest-test.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

echo "================================================================"
echo " auto_sync / Y3 — latest-meta.sh tests"
echo "   workdir: $WORK"
echo "================================================================"

# ── T1. build 산출 ───────────────────────────────────────────────────
echo
echo "[T1] build → 유효 JSON"
META="$WORK/L.json"
bash "$LIB" build "$META" >/dev/null 2>&1
RC=$?
[[ $RC -eq 0 ]] && ok "T1.rc=0" || ng "T1.rc=$RC"
[[ -s "$META" ]] && ok "T1.file_nonempty" || ng "T1.file_empty"
jq -e . "$META" >/dev/null 2>&1 && ok "T1.json_valid" || ng "T1.json_invalid"

# ── T2. schema 형상 ──────────────────────────────────────────────────
echo
echo "[T2] schema 형상 검증"
EXPECT_KEYS='["alembic_head","db_dump","sif_changed","sif_sha256sums","source_host","sync_run_id","timestamp","voc_count"]'
ACTUAL_KEYS="$(jq -c 'keys' "$META")"
if [[ "$ACTUAL_KEYS" == "$EXPECT_KEYS" ]]; then
  ok "T2.top_keys ($ACTUAL_KEYS)"
else
  ng "T2.top_keys expected=$EXPECT_KEYS actual=$ACTUAL_KEYS"
fi

jq -e '.db_dump | has("sha256") and has("size_mb") and has("filename")' "$META" >/dev/null \
  && ok "T2.db_dump_shape" || ng "T2.db_dump_shape"
jq -e '.sif_sha256sums | type=="object"' "$META" >/dev/null \
  && ok "T2.sif_sha256sums_is_obj" || ng "T2.sif_sha256sums_is_obj"
jq -e '.sif_changed | type=="array"' "$META" >/dev/null \
  && ok "T2.sif_changed_is_array" || ng "T2.sif_changed_is_array"
jq -e '.voc_count | type=="number"' "$META" >/dev/null \
  && ok "T2.voc_count_is_number" || ng "T2.voc_count_is_number"
jq -e '.timestamp | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")' "$META" >/dev/null \
  && ok "T2.timestamp_iso_utc" || ng "T2.timestamp_iso_utc"

# ── T3. diff 동일 ────────────────────────────────────────────────────
echo
echo "[T3] diff 동일 메타 → rc=0"
cp "$META" "$WORK/A.json"
cp "$META" "$WORK/B.json"
OUT="$(bash "$LIB" diff "$WORK/A.json" "$WORK/B.json" 2>&1)"
RC=$?
if [[ $RC -eq 0 ]]; then ok "T3.rc=0"; else ng "T3.rc=$RC"; fi
echo "$OUT" | jq -e '.reason=="same"' >/dev/null 2>&1 \
  && ok "T3.reason=same" || ng "T3.reason!=same (out=$OUT)"

# ── T4. db_dump.sha256 변경 ──────────────────────────────────────────
echo
echo "[T4] diff DB sha 변경 → rc=1, .db=true"
jq '.db_dump.sha256 = "DIFFERENT_SHA_FOR_TEST"' "$WORK/A.json" > "$WORK/A2.json"
OUT="$(bash "$LIB" diff "$WORK/A2.json" "$WORK/B.json" 2>&1)"
RC=$?
[[ $RC -eq 1 ]] && ok "T4.rc=1" || ng "T4.rc=$RC"
echo "$OUT" | jq -e '.db==true and .reason=="delta"' >/dev/null 2>&1 \
  && ok "T4.db_true_delta" || ng "T4.db_or_delta_wrong (out=$OUT)"
echo "$OUT" | jq -e '(.sif_changed|length)==0' >/dev/null 2>&1 \
  && ok "T4.sif_unchanged" || ng "T4.sif_unchanged (out=$OUT)"

# ── T5. sif_sha256sums.backend 변경 ──────────────────────────────────
echo
echo "[T5] diff backend sif 변경 → sif_changed=[backend]"
jq '.sif_sha256sums.backend = "BACKEND_NEW_SHA"' "$WORK/A.json" > "$WORK/A3.json"
OUT="$(bash "$LIB" diff "$WORK/A3.json" "$WORK/B.json" 2>&1)"
RC=$?
[[ $RC -eq 1 ]] && ok "T5.rc=1" || ng "T5.rc=$RC"
echo "$OUT" | jq -e '.sif_changed==["backend"]' >/dev/null 2>&1 \
  && ok "T5.sif_changed=[backend]" || ng "T5.sif_changed_wrong (out=$OUT)"
echo "$OUT" | jq -e '.db==false' >/dev/null 2>&1 \
  && ok "T5.db_false" || ng "T5.db_should_be_false (out=$OUT)"

# ── T6. 로컬 메타 부재 (첫 sync) ─────────────────────────────────────
echo
echo "[T6] diff 로컬 부재 → 전 svc sif_changed"
OUT="$(bash "$LIB" diff "$WORK/A.json" "$WORK/NONEXISTENT.json" 2>&1)"
RC=$?
[[ $RC -eq 1 ]] && ok "T6.rc=1" || ng "T6.rc=$RC"
echo "$OUT" | jq -e '.reason=="no_local"' >/dev/null 2>&1 \
  && ok "T6.reason=no_local" || ng "T6.reason!=no_local (out=$OUT)"
echo "$OUT" | jq -e '(.sif_changed|length)==4' >/dev/null 2>&1 \
  && ok "T6.sif_changed_4" || ng "T6.sif_changed_count_wrong (out=$OUT)"

# ── T7. 잘못된 입력 → rc=2 ────────────────────────────────────────────
echo
echo "[T7] diff 원격 메타 부재 → rc=2"
bash "$LIB" diff "$WORK/NO_REMOTE.json" "$WORK/B.json" >/dev/null 2>&1
RC=$?
[[ $RC -eq 2 ]] && ok "T7.rc=2" || ng "T7.rc=$RC"

echo
echo "================================================================"
echo " 결과: PASS=$PASS  FAIL=$FAIL"
echo "================================================================"
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
