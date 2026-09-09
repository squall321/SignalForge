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

if [ -f "$STATE" ]; then YEAR="$(cat "$STATE")"; else YEAR="$(date +%Y)"; fi
if [ "$YEAR" -lt "$FLOOR" ]; then
  # FLOOR 도달 시 영구 idle 이었다. 그 결과 4종 전부 멈춰 pre-2020 유입이
  # 하루 1,189건 → 66건(-94%)으로 붕괴했고, 시작이 '작년'이라 금년 상반기는
  # 한 번도 수집된 적이 없었다(GS26 출시창 118건 vs GZF8 24,677건의 직접 원인).
  # → 금년부터 재순회한다. 과거 소스는 시간이 지나며 내용이 늘고, 중복은
  #   content_hash dedup 이 막는다.
  YEAR="$(date +%Y)"
  echo "$(date '+%F %T') FLOOR($FLOOR) 도달 — $YEAR 부터 재순회" >> "$LOG"
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
