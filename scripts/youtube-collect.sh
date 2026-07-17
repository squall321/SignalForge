#!/usr/bin/env bash
# YouTube 댓글 수집 — 정체된 celery 큐를 우회해 host cron 으로 직접 실행(4h).
# 배경: crawl 태스크 82개가 concurrency=4 worker 를 포화시켜 큐 대기 ~3h.
#       youtube 는 API 기반(빠름)이라 느린 HTML 크롤러 뒤에 줄서면 굶는다.
#       Drive sync 를 beat→cron 으로 뺀 것과 같은 패턴으로 큐 밖에서 직접 돌린다.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_common.sh" >/dev/null 2>&1 || true   # XDG_RUNTIME_DIR/DBUS (apptainer exec 필수)
load_env >/dev/null 2>&1 || true                    # .env → YOUTUBE_API_KEY, POSTGRES_*
LOG="$ROOT/logs/youtube-collect.log"
mkdir -p "$ROOT/logs"

# 중복 실행 방지 (이전 run 이 아직 돌면 skip)
exec 9>"/tmp/sf-youtube-collect.lock"
flock -n 9 || exit 0

KEY="${YOUTUBE_API_KEY:-}"
if [ -z "$KEY" ]; then
  echo "$(date '+%F %T') YOUTUBE_API_KEY 미설정 — skip" >> "$LOG"; exit 0
fi
DB="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"

echo "$(date '+%F %T') youtube 수집 시작" >> "$LOG"
# instance:// 대신 ephemeral sif exec — cron/detached 컨텍스트에서 instance 조작은
# cgroup manager(systemd/DBUS 세션) 오류를 내지만, sif 직접 exec 는 정상 동작한다.
# 코드는 host 에서 bind, 네트워크는 host 공유(127.0.0.1:5434 → sf_postgres).
apptainer exec --bind "$CRAWLER_DIR:/crawler" \
  --env YOUTUBE_API_KEY="$KEY" --env DATABASE_URL="$DB" \
  "$APPT_DIR/sif/crawler.sif" python3 -c "
import sys, asyncio; sys.path.insert(0, '/crawler')
from platforms.youtube_comments import YouTubeCommentsCrawler
print(asyncio.run(YouTubeCommentsCrawler().run()))
" >> "$LOG" 2>&1
echo "$(date '+%F %T') youtube 수집 끝" >> "$LOG"
