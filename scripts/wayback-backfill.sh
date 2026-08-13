#!/usr/bin/env bash
# Wayback 뉴스 옛기사 backfill — archive.org 아카이브 RSS 스냅샷 연도 슬라이싱.
# RSS-only(WP REST 막힌) 매체(The Verge/Engadget/PhoneArena 등)를 연도별 소급.
# archive.org 가 느리고 간헐 503 이라 '천천히' 채운다. 매 실행 1연도(작년→FLOOR) 롤링.
# 정체 큐 우회(ephemeral sif exec). 전역 backfill 락 공유(동시 실행 OOM 방지).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_common.sh" >/dev/null 2>&1 || true
load_env >/dev/null 2>&1 || true
LOG="$ROOT/logs/wayback-backfill.log"
STATE="$ROOT/logs/wayback-backfill-year.state"
FLOOR=2012   # Galaxy S(2010~) 이후 삼성 보도가 의미있게 아카이브된 하한
mkdir -p "$ROOT/logs"

exec 9>"/tmp/sf-backfill-global.lock"
flock -n 9 || exit 0

if [ -f "$STATE" ]; then YEAR="$(cat "$STATE")"; else YEAR=$(( $(date +%Y) - 1 )); fi
if [ "$YEAR" -lt "$FLOOR" ]; then
  echo "$(date '+%F %T') wayback backfill 완료(≤$FLOOR) — idle" >> "$LOG"; exit 0
fi
DB="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"

echo "$(date '+%F %T') wayback backfill 연도 $YEAR 시작" >> "$LOG"
apptainer exec --bind "$CRAWLER_DIR:/crawler" \
  --env DATABASE_URL="$DB" --env WAYBACK_YEAR="$YEAR" \
  "$APPT_DIR/sif/crawler.sif" python3 -c "
import sys, asyncio; sys.path.insert(0, '/crawler')
from platforms.wayback_news import WaybackNewsCrawler
print(asyncio.run(WaybackNewsCrawler().run()))
" >> "$LOG" 2>&1
echo "$(date '+%F %T') wayback backfill 연도 $YEAR 끝" >> "$LOG"

echo "$(( YEAR - 1 ))" > "$STATE"
