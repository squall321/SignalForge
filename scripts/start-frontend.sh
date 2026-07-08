#!/usr/bin/env bash
# frontend(serve_prod) 워치독 — 127.0.0.1:17370 미가동 시 prod 빌드 정적 서버 기동.
# up.sh 에 frontend 가 빠져 있어 별도 관리. cron 주기 실행으로 기동+자동복구.
# 스크립트 실제 위치 기준(하드코딩 금지) — 서버마다 체크아웃 경로가 달라도 동작.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../SignalForge/scripts
ROOT="$(cd "$HERE/.." && pwd)"                          # .../SignalForge (프로젝트 루트)
export FRONTEND_HOST=127.0.0.1
export FRONTEND_PORT=17370
LOG="$ROOT/logs/frontend.log"

# 이미 리스닝 중이면 아무것도 안 함 (멱등)
if ss -tln 2>/dev/null | grep -qE '127\.0\.0\.1:17370\b'; then
  exit 0
fi

mkdir -p "$ROOT/logs"
cd "$ROOT/frontend" || exit 1
nohup python3 "$ROOT/frontend/serve_prod.py" >> "$LOG" 2>&1 &
