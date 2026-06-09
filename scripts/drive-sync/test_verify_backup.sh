#!/usr/bin/env bash
# verify-backup.sh 의 단위 검증 (1 케이스, 인터넷·rclone 무관).
#
# 검증:
#   - DRY_RUN=1 호출이 0 으로 종료
#   - stdout 이 valid JSON
#   - dry_run=true 와 drive_path 필드 포함
#   - drive_path 가 "ApptainerImages:SignalForge/db-dumps" 형식 (PROJECT.conf 파싱 정상)
#
# 실행: bash test_verify_backup.sh
set -euo pipefail

DS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$DS_DIR/verify-backup.sh"

if [[ ! -x "$SCRIPT" ]]; then
  echo "[FAIL] $SCRIPT 가 실행 가능하지 않음" >&2
  exit 1
fi

OUT="$(DRY_RUN=1 bash "$SCRIPT" 2>&1)"
RC=$?
if (( RC != 0 )); then
  echo "[FAIL] DRY_RUN 호출이 exit=$RC (0 기대)" >&2
  echo "$OUT" >&2
  exit 1
fi

# JSON 파싱 가능?
if ! echo "$OUT" | jq -e . >/dev/null 2>&1; then
  echo "[FAIL] stdout 이 valid JSON 아님:" >&2
  echo "$OUT" >&2
  exit 1
fi

DRY="$(echo "$OUT" | jq -r '.dry_run // false')"
if [[ "$DRY" != "true" ]]; then
  echo "[FAIL] dry_run 플래그 누락: $OUT" >&2
  exit 1
fi

DRIVE="$(echo "$OUT" | jq -r '.drive_path // ""')"
if [[ ! "$DRIVE" =~ : ]]; then
  echo "[FAIL] drive_path 형식 비정상 ('<remote>:<folder>' 기대): $DRIVE" >&2
  exit 1
fi

echo "[OK] verify-backup.sh DRY_RUN smoke 통과 — drive_path=$DRIVE"
