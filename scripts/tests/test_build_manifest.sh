#!/usr/bin/env bash
# Stage 1 단위 테스트:
#   1. MANIFEST.json 존재 + 유효 JSON
#   2. 각 entry 의 sha256 = 실 파일 sha256
#   3. backend/crawler/mcp.sif 존재 + 50MB+ (실서비스 이미지 최소치)
#   4. 가동 중 postgres.sif baseline 보존 여부 (audit 로그에서 확인)
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SIF_DIR="$PROJECT_ROOT/apptainer/sif"
MANIFEST="$SIF_DIR/MANIFEST.json"
AUDIT_LOG="$PROJECT_ROOT/logs/audit/portal_deploy_S1.jsonl"

FAIL=0
pass() { echo "  ✓ $*"; }
fail() { echo "  ✗ $*" >&2; FAIL=1; }

echo "[1] MANIFEST.json 존재 + JSON 유효"
[[ -f "$MANIFEST" ]] || { fail "MANIFEST 없음: $MANIFEST"; exit 1; }
python3 -c "import json,sys; json.load(open('$MANIFEST'))" \
  && pass "JSON 유효" \
  || { fail "JSON 파싱 실패"; exit 1; }

echo "[2] sha256 일치 검증"
python3 <<PY
import hashlib, json, sys, os
m = json.load(open("$MANIFEST"))
fail = 0
for name, meta in m.items():
    if name.startswith("_"): continue
    path = meta["path"]
    if not os.path.exists(path):
        print(f"  ✗ {name}: 파일 없음 {path}"); fail = 1; continue
    h = hashlib.sha256(open(path,"rb").read()).hexdigest()
    if h != meta["sha256"]:
        print(f"  ✗ {name}: sha256 불일치 expected={meta['sha256'][:12]}… actual={h[:12]}…")
        fail = 1
    else:
        print(f"  ✓ {name}: sha256 {h[:12]}… size_mb={meta['size_mb']}")
sys.exit(fail)
PY
[[ $? -eq 0 ]] || FAIL=1

echo "[3] 필수 서비스 이미지 존재 + 사이즈 임계"
for svc in backend crawler mcp; do
  sif="$SIF_DIR/${svc}.sif"
  if [[ ! -f "$sif" ]]; then
    fail "${svc}.sif 없음"; continue
  fi
  size_mb=$(stat -c %s "$sif" | awk '{printf "%.1f", $1/1024/1024}')
  # crawler 는 playwright chromium 포함 → 350MB+ (squashfs 압축 기준),
  # backend/mcp 는 100MB+
  case "$svc" in
    crawler) min_mb=350 ;;
    *)       min_mb=100 ;;
  esac
  awk -v s="$size_mb" -v m="$min_mb" 'BEGIN{exit !(s>=m)}' \
    && pass "${svc}.sif ${size_mb}MB (>= ${min_mb}MB)" \
    || fail "${svc}.sif ${size_mb}MB 너무 작음 (< ${min_mb}MB)"
done

echo "[4] postgres.sif 무변경 검증 (audit log)"
if [[ -f "$AUDIT_LOG" ]]; then
  if grep -q '"event":"pg_invariant".*"ok":"1"' "$AUDIT_LOG"; then
    pass "audit log: pg_invariant ok"
  elif grep -q '"event":"pg_drift"' "$AUDIT_LOG"; then
    fail "audit log: pg_drift 발생 — 가동 중 postgres.sif 변경됨"
  else
    # baseline 자체가 없었거나 (cold start), pg 미가동
    pass "audit log: pg baseline 없음 (postgres.sif 가동 외)"
  fi
else
  fail "audit log 없음: $AUDIT_LOG"
fi

echo
if [[ "$FAIL" -eq 0 ]]; then
  echo "✓ ALL PASS"
  exit 0
else
  echo "✗ FAIL" >&2
  exit 1
fi
