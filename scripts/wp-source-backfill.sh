#!/usr/bin/env bash
# WP REST 를 쓰는 개별 소스의 역사 backfill — **자기 플랫폼 코드로** 쌓는다.
#
# wpnews 는 여러 매체를 'wpnews' 한 코드로 모은다(meta 로 매체 구분). 그건 그거대로
# 쓸모가 있지만, 플랫폼별 분석에서는 hipertextual 의 역사가 hipertextual 에 있어야
# 한다. 그래서 자기 크롤러에 기간 창을 얹어 따로 돌린다.
#
# 월 단위로 한 소스씩. 상태는 소스별 'YYYY-MM'.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_common.sh" >/dev/null 2>&1 || true
load_env >/dev/null 2>&1 || true
LOG="$ROOT/logs/wp-source-backfill.log"
FLOOR="${WP_SOURCE_FLOOR:-2016}"
mkdir -p "$ROOT/logs"

exec 9>"/tmp/sf-backfill-global.lock"
flock -n 9 || exit 0

# 요일별 1소스 — 무겁지 않지만 다른 백필과 겹치지 않게 나눈다.
case "$(date +%u)" in
  2) SRC="hipertextual"; CLS="HipertextualCrawler"; PFX="HIPERTEXTUAL" ;;
  4) SRC="mobile_review"; CLS="MobileReviewCrawler"; PFX="MOBILE_REVIEW" ;;
  6) SRC="jagatreview";  CLS="JagatReviewCrawler";  PFX="JAGATREVIEW" ;;
  *) exit 0 ;;
esac
SRC="${WP_SOURCE_ONLY:-$SRC}"

STATE="$ROOT/logs/wp-source-${SRC}.state"
if [ -f "$STATE" ]; then CUR="$(cat "$STATE")"; else CUR="$(date -u +%Y-%m)"; fi
if ! [[ "$CUR" =~ ^[0-9]{4}-[0-9]{2}$ ]]; then CUR="$(date -u +%Y-%m)"; fi
if [[ "${CUR%%-*}" -lt "$FLOOR" ]]; then
  CUR="$(date -u +%Y-%m)"
  echo "$(date '+%F %T') $SRC FLOOR($FLOOR) 도달 — $CUR 부터 재순회" >> "$LOG"
fi

DB="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"
AFTER="${CUR}-01T00:00:00"
BEFORE="$(date -u -d "${CUR}-01 +1 month" +%Y-%m-01)T00:00:00"
PREV="$(date -u -d "${CUR}-01 -1 month" +%Y-%m)"

echo "$(date '+%F %T') $SRC backfill $CUR ($AFTER ~ $BEFORE) 시작" >> "$LOG"
apptainer exec --bind "$CRAWLER_DIR:/crawler" \
  --env DATABASE_URL="$DB" \
  --env "${PFX}_AFTER=$AFTER" --env "${PFX}_BEFORE=$BEFORE" \
  "$APPT_DIR/sif/crawler.sif" python3 -c "
import sys, asyncio, time; sys.path.insert(0, '/crawler')
from platforms.${SRC} import ${CLS}
from nlp.pipeline import process_voc_list
async def m():
    c = ${CLS}(); c.CRAWL_BUDGET_SEC = 600; c.RUN_BUDGET_SEC = 600
    raw = await c.crawl()
    n = 0
    for i in range(0, len(raw), c.NLP_CHUNK):
        part = raw[i:i+c.NLP_CHUNK]
        n += await c.save(await process_voc_list(
            [c.normalize(r) for r in part], translate_deadline=time.monotonic()-1))
    print({'fetched': len(raw), 'saved': n})
asyncio.run(m())
" >> "$LOG" 2>&1
echo "$(date '+%F %T') $SRC backfill $CUR 끝" >> "$LOG"

echo "$PREV" > "$STATE"
