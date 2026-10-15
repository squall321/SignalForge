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

# 전역 backfill 락 — youtube/hn/kr/global backfill 이 동시에 안 돌게(메모리 파일업 방지).
exec 9>"/tmp/sf-backfill-global.lock"
flock -n 9 || exit 0

KEY="${YOUTUBE_API_KEY:-}"
[ -z "$KEY" ] && { echo "$(date '+%F %T') YOUTUBE_API_KEY 미설정 — skip" >> "$LOG"; exit 0; }

# 처리할 연도 결정 (상태파일 없으면 작년부터 시작)
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

# 과거 backfill 용 집중 질의(전 라인 핵심 + 경쟁사). 8개 → ≈800u/연도.
BF_QUERIES="Samsung Galaxy S review,Samsung Galaxy Z Fold review,Samsung Galaxy Z Flip review,Samsung Galaxy Note review,Samsung Galaxy A review,Samsung Galaxy Watch review,삼성 갤럭시 리뷰,iPhone vs Samsung Galaxy"

echo "$(date '+%F %T') backfill 연도 $YEAR 시작" >> "$LOG"
apptainer exec --bind "$CRAWLER_DIR:/crawler" \
  --env YOUTUBE_API_KEY="$KEY" --env DATABASE_URL="$DB" \
  --env BACKFILL_MODE=1 \
  --env YOUTUBE_QUERIES="$BF_QUERIES" \
  --env YOUTUBE_PUBLISHED_AFTER="${YEAR}-01-01T00:00:00Z" \
  --env YOUTUBE_PUBLISHED_BEFORE="${NEXT}-01-01T00:00:00Z" \
  "$APPT_DIR/sif/crawler.sif" python3 -c "
import sys, asyncio; sys.path.insert(0, '/crawler')
from platforms.youtube_comments import YouTubeCommentsCrawler
print(asyncio.run(YouTubeCommentsCrawler().run()))
" >> "$LOG" 2>&1
rc=$?
echo "$(date '+%F %T') backfill 연도 $YEAR 끝 (rc=$rc)" >> "$LOG"
# **실패하면 커서를 전진시키지 않는다.** 이전에는 rc 검사 없이 무조건 다음 구간으로
# 넘겨, DB 가 내려가 있던 나흘간 빈손으로 연도를 '소비'했다(2026-10-15 감사).
# 한 번 지나간 구간은 한 바퀴 돌아야 다시 온다 — 사실상 데이터 유실이다.
if [ "$rc" -eq 0 ]; then
  echo "$(( YEAR - 1 ))" > "$STATE"
else
  echo "$(date '+%F %T') 실패(rc=$rc) — 커서 유지, 다음 실행에서 같은 구간 재시도" >> "$LOG"
fi
# 러너의 실패 경보(⚠)가 울리도록 종료코드를 전파한다. 이게 없어서 09-18~09-25
# 여덟 번의 실패가 backfill-runner.log 에 한 줄도 남지 않았다.
exit "$rc"

