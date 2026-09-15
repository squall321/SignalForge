#!/usr/bin/env bash
# 포럼형 소스 역사 backfill — 목록 페이지를 매 실행 더 깊이 내려간다.
#
# 기존 kr-backfill.sh 는 매주 **같은 앞 50페이지**를 다시 긁어 제자리걸음이었다
# (실측 2026-09-13: 36분에 clien 0·ppomppu 3·dcinside 2건). 이 러너가 쓰는
# deep_page_backfill.py 는 site 별 페이지 커서를 logs/deep-page-state.json 에
# 남기고 매 실행 다음 구간으로 내려간다.
#
# 무겁다(한 site 당 수백 건 상세 수집). 평일 하루 한 site 씩 돌려 부하를 편다.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_common.sh" >/dev/null 2>&1 || true
load_env >/dev/null 2>&1 || true
LOG="$ROOT/logs/deep-page-backfill.log"
mkdir -p "$ROOT/logs"

# 전역 backfill 락 — 동시 실행 금지(호스트 swap 0).
exec 9>"/tmp/sf-backfill-global.lock"
flock -n 9 || exit 0

# 요일별로 한두 site 씩 나눈다. 한 site 당 수백 건 상세 수집이라 무겁고,
# 호스트 swap 이 0 이라 몰아서 돌리면 안 된다. 토·일은 global/kr backfill 몫.
case "$(date +%u)" in
  1) SITES="dcinside" ;;
  2) SITES="clien" ;;
  3) SITES="ppomppu" ;;
  4) SITES="theqoo,instiz" ;;
  5) SITES="dogdrip,ruliweb,bobaedream" ;;
  *) exit 0 ;;
esac
SITES="${DEEP_SITES:-$SITES}"

DB="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"

echo "$(date '+%F %T') 깊이 backfill 시작 — $SITES" >> "$LOG"
apptainer exec --bind "$CRAWLER_DIR:/crawler" --bind "$ROOT/logs:/logs" \
  --env DATABASE_URL="$DB" \
  --env DEEP_SITES="$SITES" \
  --env DEEP_STATE="/logs/deep-page-state.json" \
  --env DEEP_PAGES_PER_RUN="${DEEP_PAGES_PER_RUN:-12}" \
  --env DEEP_BUDGET_SEC="${DEEP_BUDGET_SEC:-900}" \
  "$APPT_DIR/sif/crawler.sif" \
  sh -c "cd /crawler && python3 scripts/deep_page_backfill.py" >> "$LOG" 2>&1
echo "$(date '+%F %T') 깊이 backfill 끝 (rc=$?)" >> "$LOG"
