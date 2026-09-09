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
