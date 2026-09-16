#!/usr/bin/env bash
# 모든 backfill 을 '하나씩' 순차 실행 — 동시 실행 OOM 방지(호스트 메모리 빠듯, swap 0).
# 단일 cron 이 이 러너 하나만 호출한다. 각 단계는 앞 단계가 끝난 뒤에 시작(직렬).
#   - youtube / hn : 매 실행(연도 롤링, 1연도씩) — 가벼움
#   - reddit       : 매 실행. 연도를 매번 내리지 않고 sub별 커서로 이어받는다
#                    (한 해가 수만 건이라 한 번에 못 긁는다). 연도 완료 시에만 감소.
#   - deep-page    : 월/화/수 각 1 site. **커서를 전진**시켜 매번 더 깊이 내려간다.
#                    kr-backfill 은 앞 50p 를 다시 보는 신선도용이라 역할이 다르다.
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
# 로그 절단부터 — 무한히 자라는 로그가 디스크를 채우면 나머지가 다 멈춘다
# (실측 2026-09-16: celery-worker.log 102MB, 로테이션 설정 없음).
run_step rotate-logs.sh
run_step youtube-backfill.sh
run_step hn-backfill.sh
run_step wpnews-backfill.sh                         # WP뉴스 옛기사(연도 롤링) 매일
run_step wayback-backfill.sh                        # Wayback 아카이브 옛뉴스(연도 롤링, 느림) 매일
run_step reddit-backfill.sh                         # Reddit 역사(Arctic Shift, sub별 커서 이어받기) 매일
run_step deep-page-backfill.sh                       # 포럼 깊이(커서 전진) 월~금 요일별
run_step repair-dogdrip.sh                          # dogdrip 옛 댓글 날짜 복구(끝나면 자동 skip)
run_step wp-source-backfill.sh                      # WP REST 개별 소스 역사(자기 코드로) 화/목/토
[ "$dow" = "7" ] && run_step kr-backfill.sh        # 일요일: KR 깊이(앞 50p 재확인 — 신선도용)
[ "$dow" = "6" ] && run_step global-backfill.sh    # 토요일: 글로벌 깊이
echo "$(date '+%F %T') ===== backfill 러너 끝 =====" >> "$LOG"
