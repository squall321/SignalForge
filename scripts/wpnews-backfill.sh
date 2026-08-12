#!/usr/bin/env bash
# WP 뉴스 옛기사 backfill — WordPress REST 연도 슬라이싱(9to5google/phandroid/sammobile).
# 매 실행 1개 연도(작년→FLOOR) 처리 후 상태파일 감소 → 롤링. 정체 큐 우회(ephemeral sif exec).
# youtube/hn backfill 과 동형. 전역 락 공유(동시 실행 OOM 방지).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_common.sh" >/dev/null 2>&1 || true
load_env >/dev/null 2>&1 || true
LOG="$ROOT/logs/wpnews-backfill.log"
STATE="$ROOT/logs/wpnews-backfill-year.state"
FLOOR=2016
mkdir -p "$ROOT/logs"

# 전역 backfill 락 — youtube/hn/kr/global/wpnews 동시 실행 방지.
exec 9>"/tmp/sf-backfill-global.lock"
flock -n 9 || exit 0

if [ -f "$STATE" ]; then YEAR="$(cat "$STATE")"; else YEAR=$(( $(date +%Y) - 1 )); fi
if [ "$YEAR" -lt "$FLOOR" ]; then
  echo "$(date '+%F %T') wpnews backfill 완료(≤$FLOOR) — idle" >> "$LOG"; exit 0
fi
NEXT=$(( YEAR + 1 ))
DB="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"

echo "$(date '+%F %T') wpnews backfill 연도 $YEAR 시작" >> "$LOG"
apptainer exec --bind "$CRAWLER_DIR:/crawler" \
  --env DATABASE_URL="$DB" \
  --env WPNEWS_AFTER="${YEAR}-01-01T00:00:00" \
  --env WPNEWS_BEFORE="${NEXT}-01-01T00:00:00" \
  "$APPT_DIR/sif/crawler.sif" python3 -c "
import sys, asyncio; sys.path.insert(0, '/crawler')
from platforms.wpnews import WPNewsCrawler
print(asyncio.run(WPNewsCrawler().run()))
" >> "$LOG" 2>&1
echo "$(date '+%F %T') wpnews backfill 연도 $YEAR 끝" >> "$LOG"

echo "$(( YEAR - 1 ))" > "$STATE"
