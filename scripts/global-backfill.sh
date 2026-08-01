#!/usr/bin/env bash
# 글로벌 IT 사이트 과거 backfill — sammobile/engadget/xataka/gigazine/anandtech/xda 등을
# 깊은 페이지(H1_FACTOR 배)로 소급 수집. 기존 crawler/scripts/global_it_backfill.py(dormant) 활성화.
# 정체된 celery 큐 우회(ephemeral sif exec). audit jsonl 은 /audit 로 bind.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_common.sh" >/dev/null 2>&1 || true
load_env >/dev/null 2>&1 || true
LOG="$ROOT/logs/global-backfill.log"
mkdir -p "$ROOT/logs" "$ROOT/audit"

exec 9>"/tmp/sf-global-backfill.lock"
flock -n 9 || exit 0

DB="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"
FACTOR="${GLOBAL_BACKFILL_FACTOR:-3.0}"   # LIST_PAGES 배수(깊이). 기본 3배.

echo "$(date '+%F %T') 글로벌 IT backfill 시작 (H1_FACTOR=$FACTOR)" >> "$LOG"
# audit_path = 스크립트 3-up/audit → 컨테이너 /audit. host $ROOT/audit 를 bind.
apptainer exec --bind "$CRAWLER_DIR:/crawler" --bind "$ROOT/audit:/audit" \
  --env DATABASE_URL="$DB" --env H1_FACTOR="$FACTOR" \
  "$APPT_DIR/sif/crawler.sif" sh -c "cd /crawler && python3 scripts/global_it_backfill.py" \
  >> "$LOG" 2>&1
echo "$(date '+%F %T') 글로벌 IT backfill 끝" >> "$LOG"
