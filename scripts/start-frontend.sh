#!/usr/bin/env bash
# frontend(serve_prod) 워치독 — 127.0.0.1:17370 미가동 시 prod 빌드 정적 서버 기동.
# up.sh 에 frontend 가 빠져 있어 별도 관리. cron 주기 실행으로 기동+자동복구.
export FRONTEND_HOST=127.0.0.1
export FRONTEND_PORT=17370
LOG=/home/koopark/claude/SignalForge/logs/frontend.log

# 이미 리스닝 중이면 아무것도 안 함 (멱등)
if ss -tln 2>/dev/null | grep -qE '127\.0\.0\.1:17370\b'; then
  exit 0
fi

cd /home/koopark/claude/SignalForge/frontend || exit 1
nohup python3 /home/koopark/claude/SignalForge/frontend/serve_prod.py >> "$LOG" 2>&1 &
