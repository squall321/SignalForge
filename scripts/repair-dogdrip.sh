#!/usr/bin/env bash
# dogdrip 댓글 발행일·작성자 소급 복구 — 낡은 셀렉터로 88%가 비어 있었다.
# 일회성이지만 대상이 800 URL 가까이라 하루에 조금씩 전진한다(천천히).
# 대상이 다 떨어지면 rc=3 을 내고, 러너가 그때부터 건너뛴다.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_common.sh" >/dev/null 2>&1 || true
load_env >/dev/null 2>&1 || true
LOG="$ROOT/logs/repair-dogdrip.log"
DONE_FLAG="$ROOT/logs/repair-dogdrip.done"
mkdir -p "$ROOT/logs"
[ -f "$DONE_FLAG" ] && exit 0          # 이미 끝났다

exec 9>"/tmp/sf-backfill-global.lock"
flock -n 9 || exit 0

DB="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"
echo "$(date '+%F %T') dogdrip 날짜 복구 시작" >> "$LOG"
apptainer exec --bind "$CRAWLER_DIR:/crawler" --bind "$ROOT/logs:/logs" \
  --env DATABASE_URL="$DB" \
  --env REPAIR_STATE="/logs/repair-dogdrip-state.json" \
  --env REPAIR_LIMIT="${REPAIR_LIMIT:-150}" \
  --env REPAIR_DELAY="${REPAIR_DELAY:-2.0}" \
  "$APPT_DIR/sif/crawler.sif" \
  sh -c "cd /crawler && python3 scripts/repair_dogdrip_dates.py" >> "$LOG" 2>&1
rc=$?
if [ "$rc" = "3" ]; then
  touch "$DONE_FLAG"
  echo "$(date '+%F %T') dogdrip 날짜 복구 완료 — 이후 실행은 건너뛴다" >> "$LOG"
else
  echo "$(date '+%F %T') dogdrip 날짜 복구 진행 중 (rc=$rc)" >> "$LOG"
fi
