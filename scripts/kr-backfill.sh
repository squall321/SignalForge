#!/usr/bin/env bash
# 한국 커뮤니티 과거 backfill — clien/ppomppu/dcinside 를 깊은 페이지(BACKFILL_PAGES)로 소급 수집.
# 기존 crawler/scripts/historical_kr_backfill.py(dormant, 과거 일회성)를 cron 으로 활성화.
# 정체된 celery 큐 우회(ephemeral sif exec). steady(celery)는 최근 페이지만 보므로 이건 옛 글 담당.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_common.sh" >/dev/null 2>&1 || true
load_env >/dev/null 2>&1 || true
LOG="$ROOT/logs/kr-backfill.log"
mkdir -p "$ROOT/logs"

# 전역 backfill 락 — youtube/hn/kr/global backfill 이 동시에 안 돌게(메모리 파일업 방지).
exec 9>"/tmp/sf-backfill-global.lock"
flock -n 9 || exit 0

DB="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"
PAGES="${KR_BACKFILL_PAGES:-50}"   # 깊이(페이지). 기본 50 = 옛 글까지 소급.

echo "$(date '+%F %T') KR backfill 시작 (BACKFILL_PAGES=$PAGES)" >> "$LOG"
apptainer exec --bind "$CRAWLER_DIR:/crawler" \
  --env DATABASE_URL="$DB" --env BACKFILL_PAGES="$PAGES" \
  "$APPT_DIR/sif/crawler.sif" sh -c "cd /crawler && python3 scripts/historical_kr_backfill.py" \
  >> "$LOG" 2>&1
echo "$(date '+%F %T') KR backfill 끝" >> "$LOG"
