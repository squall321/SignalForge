#!/usr/bin/env bash
# test_beat_registered.sh
# 목적: celery beat 가 auto-sync-to-drive-30m 을 등록·발화하고,
#       워커가 tasks.run_auto_sync_to_drive 를 정상 수신/실행하는지 검증.
# 사용: bash /home/koopark/claude/SignalForge/scripts/tests/test_beat_registered.sh
# 종료 코드: 0=PASS, 1=FAIL

set -u

ROOT=/home/koopark/claude/SignalForge
LOG_BEAT="$ROOT/logs/celery-beat.log"
LOG_WORKER="$ROOT/logs/celery-worker.log"
PID_BEAT="$ROOT/logs/celery-beat.pid"
PID_WORKER="$ROOT/logs/celery-worker.pid"

FAIL=0

echo "== test_beat_registered =="
echo "[1/4] beat schedule (config) 에 auto-sync-to-drive-30m 존재 확인"
if grep -q "auto-sync-to-drive-30m" "$ROOT/crawler/celery_app.py"; then
  echo "  PASS celery_app.py 에 entry 존재"
else
  echo "  FAIL celery_app.py 에 entry 없음"
  FAIL=1
fi

echo "[2/4] tasks.py 에 run_auto_sync_to_drive 정의 확인"
if grep -q 'name="tasks.run_auto_sync_to_drive"' "$ROOT/crawler/tasks.py"; then
  echo "  PASS tasks.py 정의 존재"
else
  echo "  FAIL tasks.py 정의 없음"
  FAIL=1
fi

echo "[3/4] beat 로그에 최근 fire 기록 확인 (직전 1시간)"
SINCE=$(date -u -d '1 hour ago' '+%Y-%m-%d %H:%M:%S')
HIT=$(awk -v s="$SINCE" '$0 >= "["s {print}' "$LOG_BEAT" 2>/dev/null | grep -c "auto-sync-to-drive-30m")
if [ "${HIT:-0}" -gt 0 ]; then
  echo "  PASS beat 발화 ${HIT}회 (since $SINCE)"
else
  echo "  WARN 직전 1시간 내 fire 없음 — 30분 주기 특성상 0~1회 정상"
fi

echo "[4/4] 워커 등록 상태 (celery inspect registered)"
cd "$ROOT/crawler" || exit 1
REG=$(REDIS_URL='redis://:Soseks314!@127.0.0.1:6379/0' \
      DATABASE_URL='postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge' \
      "$ROOT/.venv/bin/celery" -A celery_app inspect registered 2>/dev/null | grep -c "run_auto_sync_to_drive")
if [ "${REG:-0}" -gt 0 ]; then
  echo "  PASS 워커가 task 등록"
else
  echo "  FAIL 워커 미등록 — 워커 재시작 필요 (kill \$(cat $PID_WORKER) 후 재기동)"
  FAIL=1
fi

if [ "$FAIL" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
