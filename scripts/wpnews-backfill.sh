#!/usr/bin/env bash
# WP 뉴스 옛기사 backfill — WordPress REST **월 단위** 슬라이싱.
#
# 연 단위로 돌던 것을 월 단위로 좁혔다. 크롤러 상한이 사이트·쿼리당 3페이지
# (300건)라 **연 창에서는 한 해를 다 못 긁는다** — 실측(2026-09-15) 연도별 보유가
# 1,100~1,400건에서 평평했는데, 2022년 상반기만 따로 돌리자 1,212건이 더 들어왔다
# (그 반년이 184건에서 1,212건으로). 창을 좁히면 같은 상한으로 훨씬 촘촘해진다.
#
# 상태는 'YYYY-MM' 하나. 매 실행 한 달씩 과거로 내려가고 FLOOR 에 닿으면
# 이번 달부터 재순회한다(중복은 external_id + content_hash 가 막는다).
# 전역 락 공유(동시 실행 OOM 방지).
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

if [ -f "$STATE" ]; then CUR="$(cat "$STATE")"; else CUR="$(date -u +%Y-%m)"; fi
# 옛 상태(연도 4자리)에서 올라오는 경우 그 해 12월부터 시작한다.
if [[ "$CUR" =~ ^[0-9]{4}$ ]]; then CUR="${CUR}-12"; fi
if ! [[ "$CUR" =~ ^[0-9]{4}-[0-9]{2}$ ]]; then CUR="$(date -u +%Y-%m)"; fi

if [[ "${CUR%%-*}" -lt "$FLOOR" ]]; then
  # 바닥에 닿으면 이번 달부터 재순회. 과거 구간도 시간이 지나며 내용이 늘고
  # 중복은 external_id + content_hash 가 막으므로 손해가 없다.
  CUR="$(date -u +%Y-%m)"
  echo "$(date '+%F %T') FLOOR($FLOOR) 도달 — $CUR 부터 재순회" >> "$LOG"
fi

DB="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"

AFTER="${CUR}-01T00:00:00"
BEFORE="$(date -u -d "${CUR}-01 +1 month" +%Y-%m-01)T00:00:00"
PREV="$(date -u -d "${CUR}-01 -1 month" +%Y-%m)"

echo "$(date '+%F %T') wpnews backfill $CUR ($AFTER ~ $BEFORE) 시작" >> "$LOG"
apptainer exec --bind "$CRAWLER_DIR:/crawler" \
  --env DATABASE_URL="$DB" \
  --env BACKFILL_MODE=1 \
  --env WPNEWS_AFTER="$AFTER" \
  --env WPNEWS_BEFORE="$BEFORE" \
  "$APPT_DIR/sif/crawler.sif" python3 -c "
import sys, asyncio; sys.path.insert(0, '/crawler')
from platforms.wpnews import WPNewsCrawler
print(asyncio.run(WPNewsCrawler().run()))
" >> "$LOG" 2>&1
rc=$?
echo "$(date '+%F %T') wpnews backfill $CUR 끝 (rc=$rc)" >> "$LOG"
# **실패하면 커서를 전진시키지 않는다.** 이전에는 rc 검사 없이 무조건 다음 구간으로
# 넘겨, DB 가 내려가 있던 나흘간 빈손으로 연도를 '소비'했다(2026-10-15 감사).
# 한 번 지나간 구간은 한 바퀴 돌아야 다시 온다 — 사실상 데이터 유실이다.
if [ "$rc" -eq 0 ]; then
  echo "$PREV" > "$STATE"
else
  echo "$(date '+%F %T') 실패(rc=$rc) — 커서 유지, 다음 실행에서 같은 구간 재시도" >> "$LOG"
fi
# 러너의 실패 경보(⚠)가 울리도록 종료코드를 전파한다. 이게 없어서 09-18~09-25
# 여덟 번의 실패가 backfill-runner.log 에 한 줄도 남지 않았다.
exit "$rc"

