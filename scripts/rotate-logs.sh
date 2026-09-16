#!/usr/bin/env bash
# 커진 로그를 잘라 디스크가 차는 것을 막는다.
#
# 실측(2026-09-16) — logs/celery-worker.log 가 102MB 였고 로테이션 설정이
# 어디에도 없었다. 디스크는 90% 사용 중이라 지금은 여유가 있지만, 무한히 자라는
# 로그는 언젠가 반드시 터진다.
#
# **파일을 지우지 않고 제자리에서 자른다(`: > file`).** 프로세스가 열어둔
# 파일 디스크립터를 유지해야 한다 — rm/mv 하면 celery 는 지워진 inode 에 계속
# 쓰고 디스크는 그대로 차 있다(로그만 안 보이게 된다).
#
# 자르기 전에 꼬리를 남긴다 — 직전 상황을 잃으면 사고 조사가 불가능해진다.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LOGDIR="$ROOT/logs"
LOG="$LOGDIR/rotate-logs.log"
MAX_MB="${LOG_ROTATE_MAX_MB:-50}"      # 이 크기를 넘으면 자른다
KEEP_LINES="${LOG_ROTATE_KEEP:-2000}"  # 자르기 전 남길 꼬리 줄 수
mkdir -p "$LOGDIR"

[ -d "$LOGDIR" ] || exit 0
rotated=0
for f in "$LOGDIR"/*.log; do
  [ -f "$f" ] || continue
  case "$(basename "$f")" in
    rotate-logs.log) continue ;;       # 자기 자신은 건드리지 않는다
  esac
  size_mb=$(( $(stat -c %s "$f" 2>/dev/null || echo 0) / 1024 / 1024 ))
  [ "$size_mb" -ge "$MAX_MB" ] || continue

  tail -n "$KEEP_LINES" "$f" > "$f.tail" 2>/dev/null || true
  : > "$f"                             # 제자리 절단 — fd 유지
  if [ -s "$f.tail" ]; then
    {
      echo "=== $(date '+%F %T') 로그 절단 (${size_mb}MB → 꼬리 ${KEEP_LINES}줄 보존) ==="
      cat "$f.tail"
    } >> "$f" 2>/dev/null || true
  fi
  rm -f "$f.tail"
  echo "$(date '+%F %T') $(basename "$f") ${size_mb}MB 절단" >> "$LOG"
  rotated=$(( rotated + 1 ))
done

[ "$rotated" -gt 0 ] && echo "$(date '+%F %T') 총 ${rotated}개 절단" >> "$LOG"
exit 0
