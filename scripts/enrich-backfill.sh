#!/usr/bin/env bash
# 수집된 데이터의 **귀속 보강** — 제품 링크·국가 추론.
#
# 수집(collect)과 성격이 다르다. 새 글을 가져오는 게 아니라 이미 가진 글에
# "어느 제품인가 · 어느 나라인가"를 채운다. 둘 다 도구는 진작 있었는데
# 어디에도 등록돼 있지 않아 돌지 않고 있었다(2026-09-16 발견).
#
#   backfill_product_links   다중 제품 링크(비교글의 compared/mentioned)를 만든다.
#                            voc_records.product_id 는 1행 1제품이라 "S26 vs Fold8"
#                            같은 글은 한쪽만 남는다 — 그 손실을 보완한다.
#   backfill_country_from_lang  country_code 가 비어 있는 행을 언어로 추론한다.
#                            국가 미상이 최대 항목이었다(157,654건).
#
# 둘 다 멱등이다 — 같은 행을 다시 봐도 손해가 없다(UPSERT / IS NULL 조건).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_common.sh" >/dev/null 2>&1 || true
load_env >/dev/null 2>&1 || true
LOG="$ROOT/logs/enrich-backfill.log"
mkdir -p "$ROOT/logs"

exec 9>"/tmp/sf-backfill-global.lock"
flock -n 9 || exit 0

DB="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"

run_py() {  # $1=스크립트 $2...=추가 env
  local script="$1"; shift
  echo "$(date '+%F %T') ▷ $script 시작" >> "$LOG"
  apptainer exec --bind "$CRAWLER_DIR:/crawler" \
    --env DATABASE_URL="$DB" "$@" \
    "$APPT_DIR/sif/crawler.sif" \
    sh -c "cd /crawler && python3 scripts/$script" >> "$LOG" 2>&1
  local rc=$?
  echo "$(date '+%F %T') ◁ $script 끝 (rc=$rc)" >> "$LOG"
}

echo "$(date '+%F %T') ===== 귀속 보강 시작 =====" >> "$LOG"
# 국가 추론이 먼저 — 가볍고(30초) 빨리 끝난다. 링크는 전량 스캔이라 무겁다.
run_py backfill_country_from_lang.py
run_py backfill_product_links.py --env LINK_LIMIT="${LINK_LIMIT:-60000}"
echo "$(date '+%F %T') ===== 귀속 보강 끝 =====" >> "$LOG"
