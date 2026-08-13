#!/usr/bin/env bash
# 모든 backfill 을 '하나씩' 순차 실행 — 동시 실행 OOM 방지(호스트 메모리 빠듯, swap 0).
# 단일 cron 이 이 러너 하나만 호출한다. 각 단계는 앞 단계가 끝난 뒤에 시작(직렬).
#   - youtube / hn : 매 실행(연도 롤링, 1연도씩) — 가벼움
#   - kr           : 일요일만 (깊이 수집, 무거움)
#   - global       : 토요일만 (깊이 수집, 무거움)
# 러너 자신은 별도 락(runner.lock)으로 중복 기동 방지. 각 자식 스크립트는 순차라 충돌 없음.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LOG="$ROOT/logs/backfill-runner.log"
mkdir -p "$ROOT/logs"

# 러너 중복 기동 방지(자식들이 쓰는 global 락과 별개).
exec 8>"/tmp/sf-backfill-runner.lock"
flock -n 8 || { echo "$(date '+%F %T') 러너 이미 실행중 — skip" >> "$LOG"; exit 0; }

dow="$(date +%u)"   # 1=월 .. 7=일

run_step() {  # $1 = 자식 스크립트명
  local s="$1"
  echo "$(date '+%F %T') ▶ $s 시작" >> "$LOG"
  bash "$HERE/$s" >> "$LOG" 2>&1 || echo "$(date '+%F %T') ⚠ $s 실패(rc=$?)" >> "$LOG"
  echo "$(date '+%F %T') ■ $s 끝" >> "$LOG"
}

echo "$(date '+%F %T') ===== backfill 러너 시작 (dow=$dow) =====" >> "$LOG"
run_step youtube-backfill.sh
run_step hn-backfill.sh
run_step wpnews-backfill.sh                         # WP뉴스 옛기사(연도 롤링) 매일
run_step wayback-backfill.sh                        # Wayback 아카이브 옛뉴스(연도 롤링, 느림) 매일
[ "$dow" = "7" ] && run_step kr-backfill.sh        # 일요일: KR 깊이
[ "$dow" = "6" ] && run_step global-backfill.sh    # 토요일: 글로벌 깊이
echo "$(date '+%F %T') ===== backfill 러너 끝 =====" >> "$LOG"
