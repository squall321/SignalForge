#!/usr/bin/env bash
# YouTube 과거 댓글 backfill — 연도 슬라이싱(publishedAfter/Before)으로 모든 기간 커버.
# 매 실행 1개 연도를 처리하고 상태파일의 연도를 1 감소 → 하루 1연도씩 롤링(cron daily).
# 바닥연도(FLOOR)까지 내려가면 idle. 이후엔 steady(youtube-collect.sh, 6h)가 최신 유지.
# search.list 는 maxResults 무관 100u/호출 → backfill 은 집중 8질의로 가볍게(≈800u/연도).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_common.sh" >/dev/null 2>&1 || true
load_env >/dev/null 2>&1 || true
LOG="$ROOT/logs/youtube-backfill.log"
STATE="$ROOT/logs/youtube-backfill-year.state"
FLOOR=2016
mkdir -p "$ROOT/logs"

exec 9>"/tmp/sf-youtube-backfill.lock"
flock -n 9 || exit 0

KEY="${YOUTUBE_API_KEY:-}"
[ -z "$KEY" ] && { echo "$(date '+%F %T') YOUTUBE_API_KEY 미설정 — skip" >> "$LOG"; exit 0; }

# 처리할 연도 결정 (상태파일 없으면 작년부터 시작)
if [ -f "$STATE" ]; then YEAR="$(cat "$STATE")"; else YEAR=$(( $(date +%Y) - 1 )); fi
if [ "$YEAR" -lt "$FLOOR" ]; then
  echo "$(date '+%F %T') backfill 완료(≤$FLOOR) — idle" >> "$LOG"; exit 0
fi
NEXT=$(( YEAR + 1 ))
DB="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"

# 과거 backfill 용 집중 질의(전 라인 핵심 + 경쟁사). 8개 → ≈800u/연도.
BF_QUERIES="Samsung Galaxy S review,Samsung Galaxy Z Fold review,Samsung Galaxy Z Flip review,Samsung Galaxy Note review,Samsung Galaxy A review,Samsung Galaxy Watch review,삼성 갤럭시 리뷰,iPhone vs Samsung Galaxy"

echo "$(date '+%F %T') backfill 연도 $YEAR 시작" >> "$LOG"
apptainer exec --bind "$CRAWLER_DIR:/crawler" \
  --env YOUTUBE_API_KEY="$KEY" --env DATABASE_URL="$DB" \
  --env YOUTUBE_QUERIES="$BF_QUERIES" \
  --env YOUTUBE_PUBLISHED_AFTER="${YEAR}-01-01T00:00:00Z" \
  --env YOUTUBE_PUBLISHED_BEFORE="${NEXT}-01-01T00:00:00Z" \
  "$APPT_DIR/sif/crawler.sif" python3 -c "
import sys, asyncio; sys.path.insert(0, '/crawler')
from platforms.youtube_comments import YouTubeCommentsCrawler
print(asyncio.run(YouTubeCommentsCrawler().run()))
" >> "$LOG" 2>&1
echo "$(date '+%F %T') backfill 연도 $YEAR 끝" >> "$LOG"

# 다음 실행은 이전 연도
echo "$(( YEAR - 1 ))" > "$STATE"
