#!/usr/bin/env bash
# 로컬 백업 + Drive 업로드 + 보존정책 (최신 N개만 유지).
#
# 사용:
#   bash backup-to-drive.sh
#   PROJ_DRIVE_RETAIN=10 bash backup-to-drive.sh   # 보존 개수 override
set -euo pipefail
# shellcheck source=./_drive_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_drive_common.sh"

require_rclone

DUMP_FILE="$PROJ_DUMP_DIR/$(dump_name)"
SUM_FILE="${DUMP_FILE}.sha256"

# 1) 로컬 dump
echo "→ pg_dump → $DUMP_FILE"
dump_verified "$DUMP_FILE"
SHA=$(file_sha256 "$DUMP_FILE")
echo "$SHA  $(basename "$DUMP_FILE")" > "$SUM_FILE"
SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo "[OK] $(basename "$DUMP_FILE")  $SIZE  sha256=$SHA"

# 2) 복원 가이드 자동 생성 (MXWP 패턴)
TS_TAG="$(basename "$DUMP_FILE" .sql.gz | sed "s|${PROJ_PREFIX}-db-||")"
GUIDE_FILE="$PROJ_DUMP_DIR/RESTORE-GUIDE-${TS_TAG}.md"
cat > "$GUIDE_FILE" <<EOF
# Restore Guide — $PROJ_NAME @ $TS_TAG

## 빠른 복원 (타깃 서버에서)

\`\`\`bash
cd <project_root>/scripts/drive-sync   # 또는 본 키트가 있는 곳
git pull
bash sync-from-drive.sh
\`\`\`

## 수동 복원 (이 파일을 직접 갖고 있는 경우)

\`\`\`bash
# 1. 무결성 검증
sha256sum -c $(basename "$SUM_FILE")
# 예상: $(basename "$DUMP_FILE"): OK

# 2. restore
bash restore-db.sh $(basename "$DUMP_FILE") --yes
\`\`\`

## 메타데이터

- 원본 서버: $(hostname -f 2>/dev/null || hostname)
- 생성 시각 (UTC): $TS_TAG
- DB: $POSTGRES_DB @ $POSTGRES_HOST:$POSTGRES_PORT
- 크기: $SIZE
- sha256: $SHA
EOF

# 3) Drive 업로드
echo "→ Drive 업로드: $DRIVE_PATH"
rclone copy "$DUMP_FILE" "$DRIVE_PATH/" --progress
rclone copy "$SUM_FILE"  "$DRIVE_PATH/"
rclone copy "$GUIDE_FILE" "$DRIVE_PATH/"

# 4) Drive 보존정책 — '최신 N개' 는 이 cron 주기에 맞지 않는다.
#    crontab 은 */30 sync-to-drive(--no-sif) 로 하루 48회 이걸 부른다. RETAIN=5 는 하루 1회
#    백업을 전제한 상수라, 실제로는 2.5시간 넘은 것을 전부 지워 오프사이트 복구창이
#    1.5시간까지 줄어 있었다(실측: Drive 의 5개가 전부 같은 날 03:00~04:30).
#    논리적 사고(잘못된 삭제·크롤러 오염)는 보통 몇 시간~며칠 뒤 발견되는데 그때 되돌릴
#    스냅샷이 없다는 뜻이다. 문서·설정은 '최신 5개'라고만 해서 5일치로 읽힌다.
#
#    시간 기준으로 바꾼다 — 최근 24시간은 전부 남기고(세밀 롤백), 그보다 오래된 것은
#    하루 한 개(그날의 최신)만 DRIVE_RETAIN_DAYS 일까지 남긴다.
#    5일 전량 보존은 43GB 지만 이 정책은 약 9GB 다(182MB × (48+4)).
#    파일명이 <prefix>-db-YYYYMMDD-HHMMSSZ.sql.gz 라 이름만으로 판정한다.
DRIVE_RETAIN_DAYS="${DRIVE_RETAIN_DAYS:-5}"
echo "→ Drive 보존정책: 최근 24시간 전량 + 이후 일별 1개 (${DRIVE_RETAIN_DAYS}일)"
mapfile -t ALL < <(rclone lsf "$DRIVE_PATH" --include "${PROJ_PREFIX}-db-*.sql.gz" 2>/dev/null | sort -r)
mapfile -t DELS < <(printf '%s\n' "${ALL[@]}" | thin_dumps "$DRIVE_RETAIN_DAYS")
if [[ ${#DELS[@]} -gt 0 ]]; then
  del_fail=0
  for old in "${DELS[@]}"; do
    [[ -n "$old" ]] || continue
    echo "    - delete $old"
    # 실패를 `|| true` 로 삼키면 안 된다 — 지워졌다고 믿는데 Drive 에 남아 같은 이름의
    # 객체가 둘 생기는 일이 실제로 있었다. 세어서 마지막에 알린다.
    rclone deletefile "$DRIVE_PATH/$old" 2>/dev/null || del_fail=$((del_fail+1))
    rclone deletefile "$DRIVE_PATH/${old}.sha256" 2>/dev/null || true
    TS="$(echo "$old" | sed "s|${PROJ_PREFIX}-db-||; s|.sql.gz||")"
    rclone deletefile "$DRIVE_PATH/RESTORE-GUIDE-${TS}.md" 2>/dev/null || true
  done
  [[ "$del_fail" -gt 0 ]] && echo "  ⚠ Drive 삭제 실패 ${del_fail}건 — 같은 이름의 객체가 중복될 수 있다(rclone dedupe 확인)"
fi

# 4b) 로컬 보존정책 — Drive 쪽만 정리하고 로컬은 무한 누적이었다. 30분마다 덤프가 쌓여
#     2.5개월 만에 3,200파일 165GB 가 됐고 /home 이 92% 까지 찼다(실측). 로컬 덤프는
#     Drive 업로드용 스테이징이자 단기 롤백용이므로 오래된 것을 들고 있을 이유가 없다.
#     기본 7일(다른 백업 스크립트의 RETAIN_DAYS 관례와 동일). LOCAL_RETAIN_DAYS 로 조정.
LOCAL_RETAIN_DAYS="${LOCAL_RETAIN_DAYS:-7}"
if [[ -d "$PROJ_DUMP_DIR" ]]; then
  # Drive 와 **같은 규칙**으로 얇게 만든다 — 최근 24시간 전량 + 이후 하루 1개.
  # 이전엔 로컬만 '7일 전량'이어서 30분×314MB×7일 = 105GB 가 쌓여 /home 이 95% 까지
  # 찼다(2026-09-25 실측, 여유 114GB). 디스크가 차면 postgres 가 죽고 그게 이번
  # 사고의 재발 경로다. 같은 규칙이면 48개+6개 ≈ 17GB 로 떨어지고,
  # 실제 롤백 창(최근 24시간 30분 단위)은 그대로다.
  mapfile -t LDELS < <(
    find "$PROJ_DUMP_DIR" -maxdepth 1 -name "${PROJ_PREFIX}-db-*.sql.gz" -printf '%f\n' 2>/dev/null \
      | sort -r | thin_dumps "$LOCAL_RETAIN_DAYS")
  if [[ ${#LDELS[@]} -gt 0 ]]; then
    freed=0
    for old in "${LDELS[@]}"; do
      [[ -n "$old" ]] || continue
      f="$PROJ_DUMP_DIR/$old"
      [[ -f "$f" ]] && freed=$((freed + $(stat -c %s "$f")))
      rm -f "$f" "$f.sha256"
    done
    echo "→ 로컬 보존정책: ${#LDELS[@]}개 삭제 ($((freed / 1048576))MB 확보)"
  else
    echo "→ 로컬 보존정책: 삭제 대상 없음"
  fi
  # 과거에 생긴 빈 덤프 정리 — dump_verified 이전에 만들어진 것들이 남아 있다.
  # **바이트로 판정한다.** `-size -1M` 은 블록 반올림 규약이 얽혀 헷갈리기 쉽고,
  # 실측에서 같은 조건이 187개를 한 번은 놓치고 한 번은 잡았다. 크기는 바이트로 본다.
  purged=0
  while read -r sz f; do
    [ "${sz:-0}" -lt 1024 ] || continue
    rm -f "$f" "$f.sha256"; purged=$((purged + 1))
  done < <(find "$PROJ_DUMP_DIR" -maxdepth 1 -name "${PROJ_PREFIX}-db-*.sql.gz" -printf '%s %p\n' 2>/dev/null)
  [ "$purged" -gt 0 ] && echo "→ 빈 덤프 ${purged}개 정리 (복원 후보에서 제외)"

  # 짝 잃은 사이드카·중단된 .part 정리
  find "$PROJ_DUMP_DIR" -maxdepth 1 -name "*.sql.gz.part" -mmin +120 -delete 2>/dev/null || true
  for sc in "$PROJ_DUMP_DIR"/${PROJ_PREFIX}-db-*.sql.gz.sha256; do
    [[ -e "$sc" ]] || continue
    [[ -f "${sc%.sha256}" ]] || rm -f "$sc"
  done
  df -h "$PROJ_DUMP_DIR" | awk 'NR==2{printf "  디스크: %s 사용 (여유 %s)\n", $5, $4}'
fi

# 5) (옵션) 공유 링크
LINK=$(rclone link "$DRIVE_PATH/$(basename "$DUMP_FILE")" 2>/dev/null || true)

echo
echo "================================================================"
echo "✓ Drive 업로드 완료"
echo "  $DRIVE_PATH/$(basename "$DUMP_FILE")  ($SIZE)"
echo "  sha256: $SHA"
[[ -n "$LINK" ]] && {
  echo
  echo "공유 링크:"
  echo "    $LINK"
}
echo "================================================================"
