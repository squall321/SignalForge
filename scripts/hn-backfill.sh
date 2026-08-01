#!/usr/bin/env bash
# Hacker News 과거 backfill — Algolia created_at_i 연도 슬라이싱으로 모든 기간 커버.
# 매 실행 1개 연도(작년→FLOOR) 처리 후 상태파일 감소 → daily cron 롤링. 정체된 celery 큐
# 우회(ephemeral sif exec, host network 공유). steady 는 celery crawl-hackernews-2h 유지.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_common.sh" >/dev/null 2>&1 || true
load_env >/dev/null 2>&1 || true
LOG="$ROOT/logs/hn-backfill.log"
STATE="$ROOT/logs/hn-backfill-year.state"
FLOOR=2011   # HN 에 Galaxy 언급이 의미있게 나오는 하한(갤럭시 S 2010~)
mkdir -p "$ROOT/logs"

exec 9>"/tmp/sf-hn-backfill.lock"
flock -n 9 || exit 0

if [ -f "$STATE" ]; then YEAR="$(cat "$STATE")"; else YEAR=$(( $(date +%Y) - 1 )); fi
if [ "$YEAR" -lt "$FLOOR" ]; then
  echo "$(date '+%F %T') backfill 완료(≤$FLOOR) — idle" >> "$LOG"; exit 0
fi
NEXT=$(( YEAR + 1 ))
# 연도 경계 → unix ts (UTC). date 는 host 것.
AFTER=$(date -u -d "${YEAR}-01-01 00:00:00" +%s)
BEFORE=$(date -u -d "${NEXT}-01-01 00:00:00" +%s)
DB="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"

echo "$(date '+%F %T') HN backfill 연도 $YEAR (ts $AFTER~$BEFORE) 시작" >> "$LOG"
apptainer exec --bind "$CRAWLER_DIR:/crawler" \
  --env DATABASE_URL="$DB" \
  --env HN_BACKFILL_AFTER="$AFTER" --env HN_BACKFILL_BEFORE="$BEFORE" \
  "$APPT_DIR/sif/crawler.sif" python3 -c "
import sys, asyncio; sys.path.insert(0, '/crawler')
from platforms.hackernews import HackerNewsCrawler
print(asyncio.run(HackerNewsCrawler().run()))
" >> "$LOG" 2>&1
echo "$(date '+%F %T') HN backfill 연도 $YEAR 끝" >> "$LOG"

echo "$(( YEAR - 1 ))" > "$STATE"
