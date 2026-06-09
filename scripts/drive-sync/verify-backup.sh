#!/usr/bin/env bash
# Drive 백업 검증 — 일일 호출용 (Celery beat 또는 cron).
#
# 검증 항목:
#   1) 가장 최근 Drive dump 존재 (sf-db-*.sql.gz)
#   2) sha256 컴패니언 파일이 함께 존재하고 형식이 64자 16진수
#   3) Drive 객체 크기 > 1MB (의미 있는 크기)
#   4) Drive ModTime 이 24h (기본) 이내 — 누락된 백업 사이클 감지
#
# 결과:
#   stdout 에 JSON 한 줄 ({ok, ...}) 출력
#   $PROJ_DUMP_DIR/last_verified.json 에 동일 JSON 영속화 (backend endpoint 가 읽음)
#   ok=true → exit 0,  ok=false → exit 1
#
# 사용:
#   bash verify-backup.sh                          # 기본 (24h 신선도, 1MB 임계)
#   MAX_AGE_HOURS=48 bash verify-backup.sh         # 신선도 임계 override
#   MIN_SIZE_BYTES=1048576 bash verify-backup.sh   # 크기 임계 override
#   DRY_RUN=1 bash verify-backup.sh                # rclone 호출 없이 PROJECT.conf 만 검증
set -euo pipefail
# shellcheck source=./_drive_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_drive_common.sh"

: "${MAX_AGE_HOURS:=24}"
: "${MIN_SIZE_BYTES:=1048576}"    # 1 MiB
: "${DRY_RUN:=0}"

STATE_FILE="$PROJ_DUMP_DIR/last_verified.json"
NOW_ISO="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# ── DRY_RUN: rclone 호출 없이 conf 파싱만 검증 ───────────────────────────────
if [[ "$DRY_RUN" = "1" ]]; then
  printf '{"ok":true,"dry_run":true,"verified_at":"%s","drive_path":"%s"}\n' \
    "$NOW_ISO" "$DRIVE_PATH"
  exit 0
fi

require_rclone

# ── 1) Drive 에서 최신 dump 확인 ─────────────────────────────────────────────
# rclone lsjson → 모든 파일 메타데이터(JSON). jq 로 sha256 컴패니언 제외 + 최신 1개.
LSJSON="$(rclone lsjson "$DRIVE_PATH" --include "${PROJ_PREFIX}-db-*.sql.gz" 2>/dev/null || echo '[]')"

# 가장 최근 ModTime 의 파일 1개.
LATEST_JSON="$(echo "$LSJSON" | jq -c 'sort_by(.ModTime) | last // empty')"
if [[ -z "$LATEST_JSON" || "$LATEST_JSON" = "null" ]]; then
  jq -nc --arg now "$NOW_ISO" --arg drive "$DRIVE_PATH" \
    '{ok:false, reason:"no_backup_file", verified_at:$now, drive_path:$drive}' \
    | tee "$STATE_FILE"
  exit 1
fi

LATEST_NAME="$(echo "$LATEST_JSON"  | jq -r '.Name')"
LATEST_SIZE="$(echo "$LATEST_JSON"  | jq -r '.Size')"
LATEST_MTIME="$(echo "$LATEST_JSON" | jq -r '.ModTime')"

# ── 2) sha256 컴패니언 파일 ─────────────────────────────────────────────────
SHA_NAME="${LATEST_NAME}.sha256"
# rclone cat 는 stdout 으로 바로 흘려보낸다 — 64자 hex + " " + 파일명 형식.
SHA_LINE="$(rclone cat "$DRIVE_PATH/$SHA_NAME" 2>/dev/null || true)"
SHA_HEX="$(echo "$SHA_LINE" | awk '{print $1}')"

# sha256 형식 검증 (64자 hex).
SHA_OK="false"
if [[ "$SHA_HEX" =~ ^[0-9a-fA-F]{64}$ ]]; then
  SHA_OK="true"
fi

# ── 3) 크기 검증 ────────────────────────────────────────────────────────────
SIZE_OK="false"
if [[ "$LATEST_SIZE" =~ ^[0-9]+$ ]] && (( LATEST_SIZE > MIN_SIZE_BYTES )); then
  SIZE_OK="true"
fi

# ── 4) 신선도 검증 ──────────────────────────────────────────────────────────
# ModTime ISO8601 → epoch.  GNU date -d 지원 가정 (linux).
MTIME_EPOCH="$(date -d "$LATEST_MTIME" -u +%s 2>/dev/null || echo 0)"
NOW_EPOCH="$(date -u +%s)"
AGE_SEC=$(( NOW_EPOCH - MTIME_EPOCH ))
AGE_HOURS=$(( AGE_SEC / 3600 ))
FRESH_OK="false"
if (( MTIME_EPOCH > 0 )) && (( AGE_HOURS <= MAX_AGE_HOURS )); then
  FRESH_OK="true"
fi

# ── 5) 최종 결과 합산 ───────────────────────────────────────────────────────
OK="false"
if [[ "$SHA_OK" = "true" && "$SIZE_OK" = "true" && "$FRESH_OK" = "true" ]]; then
  OK="true"
fi

# 실패 reason 분류 — 운영 알림에서 사람이 즉시 판독.
REASON="ok"
if [[ "$OK" != "true" ]]; then
  if   [[ "$SHA_OK"   != "true" ]]; then REASON="sha256_missing_or_invalid"
  elif [[ "$SIZE_OK"  != "true" ]]; then REASON="size_too_small"
  elif [[ "$FRESH_OK" != "true" ]]; then REASON="stale"
  fi
fi

jq -nc \
  --arg ok          "$OK" \
  --arg verified_at "$NOW_ISO" \
  --arg drive_path  "$DRIVE_PATH" \
  --arg file        "$LATEST_NAME" \
  --argjson size    "$LATEST_SIZE" \
  --arg mtime       "$LATEST_MTIME" \
  --argjson age_h   "$AGE_HOURS" \
  --argjson max_h   "$MAX_AGE_HOURS" \
  --argjson min_b   "$MIN_SIZE_BYTES" \
  --arg sha256      "$SHA_HEX" \
  --arg reason      "$REASON" \
  '{
     ok: ($ok=="true"),
     verified_at: $verified_at,
     reason: $reason,
     drive_path: $drive_path,
     file: $file,
     size_bytes: $size,
     mtime: $mtime,
     age_hours: $age_h,
     max_age_hours: $max_h,
     min_size_bytes: $min_b,
     sha256: $sha256
  }' \
  | tee "$STATE_FILE"

[[ "$OK" = "true" ]] || exit 1
