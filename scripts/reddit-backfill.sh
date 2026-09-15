#!/usr/bin/env bash
# Reddit 과거 backfill — Arctic Shift 기간 검색으로 연도 슬라이싱.
#
# 다른 backfill 과 다른 점: **연도를 매 실행 내리지 않는다.** r/samsung 은 한 해에
# 수만 건이라 한 번에 다 못 긁는다. 자식 스크립트가 sub 별 시각 커서를
# logs/reddit-backfill-state.json 에 남기고 이어받으며, 그 연도의 모든 sub 가
# 소진돼야(rc=3) 연도를 내린다. 그래야 "천천히, 하지만 확실하게" 가 성립한다.
#   rc=3 → 이 연도 완료, 연도 감소
#   rc=0 → 아직 남음, 같은 연도 유지 (다음 실행이 이어받음)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_common.sh" >/dev/null 2>&1 || true
load_env >/dev/null 2>&1 || true
LOG="$ROOT/logs/reddit-backfill.log"
STATE="$ROOT/logs/reddit-backfill-year.state"
FLOOR="${REDDIT_BACKFILL_FLOOR:-2010}"   # 갤럭시 S 1세대(2010) 이전은 의미 없음
mkdir -p "$ROOT/logs"

# 전역 backfill 락 — 다른 backfill 과 동시에 돌지 않게(호스트 swap 0).
exec 9>"/tmp/sf-backfill-global.lock"
flock -n 9 || exit 0

if [ -f "$STATE" ]; then YEAR="$(cat "$STATE")"; else YEAR="$(date +%Y)"; fi
if ! [[ "$YEAR" =~ ^[0-9]{4}$ ]]; then YEAR="$(date +%Y)"; fi
if [ "$YEAR" -lt "$FLOOR" ]; then
  # 바닥에 닿으면 금년부터 재순회한다. 과거 구간도 시간이 지나며 내용이 늘고,
  # 중복은 external_id + content_hash 가 막으므로 손해가 없다.
  YEAR="$(date +%Y)"
  echo "$(date '+%F %T') FLOOR($FLOOR) 도달 — $YEAR 부터 재순회" >> "$LOG"
fi

DB="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"

echo "$(date '+%F %T') Reddit backfill 연도 $YEAR 시작" >> "$LOG"
apptainer exec --bind "$CRAWLER_DIR:/crawler" --bind "$ROOT/logs:/logs" \
  --env DATABASE_URL="$DB" \
  --env REDDIT_BACKFILL_YEAR="$YEAR" \
  --env REDDIT_ARCTIC_STATE="/logs/reddit-backfill-state.json" \
  --env REDDIT_ARCTIC_DELAY="${REDDIT_ARCTIC_DELAY:-3.0}" \
  --env REDDIT_ARCTIC_MAX_PAGES="${REDDIT_ARCTIC_MAX_PAGES:-40}" \
  "$APPT_DIR/sif/crawler.sif" \
  sh -c "cd /crawler && python3 scripts/reddit_arctic_backfill.py" >> "$LOG" 2>&1
rc=$?

if [ "$rc" = "3" ]; then
  echo "$(( YEAR - 1 ))" > "$STATE"
  echo "$(date '+%F %T') Reddit backfill 연도 $YEAR 완료 → 다음 실행은 $(( YEAR - 1 ))" >> "$LOG"
else
  echo "$(date '+%F %T') Reddit backfill 연도 $YEAR 계속 (rc=$rc) — 같은 연도 이어서" >> "$LOG"
fi
