#!/usr/bin/env bash
# SignalForge — Frontend (prod build + static serve)
#
# HWAX 포털의 `/signalforge/` 프록시는 trailing-slash 라서 prefix 를 STRIP 한다:
#   브라우저 /signalforge/assets/x.js → 백엔드 :17370/assets/x.js
# 따라서 17370 에는 vite *dev* 가 아니라, base=/signalforge/ 로 빌드된 dist 를
# 루트(/)에서 SPA-fallback 으로 정적 서빙해야 한다 (mx-white-paper 와 동일 패턴).
#   - VITE_BASE_PATH=/signalforge/ npm run build  → dist asset URL = /signalforge/assets/...
#   - serve -s dist -l 17370                        → :17370/assets/x.js = dist/assets/x.js
#   - serve -s (SPA fallback)                       → 없는 경로 → index.html (deep-link refresh OK)
#
# vite preview 는 쓰지 않는다: preview 는 base 를 prefix 로 serve 해서 nginx strip 과 안 맞는다.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env 2>/dev/null || true

FRONTEND_DIR="$PROJECT_ROOT/frontend"
SERVE_BIN="$FRONTEND_DIR/node_modules/.bin/serve"
WEB_PORT="${SF_WEB_PORT:-17370}"
BASE_PATH="${VITE_BASE_PATH:-/signalforge/}"
PIDFILE="$LOG_DIR/frontend.pid"
SERVE_LOG="$LOG_DIR/frontend_serve.log"

mkdir -p "$LOG_DIR"

# ── 1. prod 빌드 (포털 prefix 베이크) ────────────────────────────────
echo "→ frontend prod build (VITE_BASE_PATH=$BASE_PATH)"
cd "$FRONTEND_DIR"
if [[ ! -x "$SERVE_BIN" ]]; then
  echo "→ serve 미설치 — npm install"
  npm install > "$LOG_DIR/frontend-npm.log" 2>&1
fi
VITE_BASE_PATH="$BASE_PATH" npm run build > "$LOG_DIR/frontend-build.log" 2>&1
echo "  ✓ dist 빌드 완료 ($(grep -oE 'assets/index-[^\"]*\.js' dist/index.html | head -1))"

# ── 2. 기존 serve / vite dev 종료 ────────────────────────────────────
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "  (기존 frontend serve 종료: pid=$(cat "$PIDFILE"))"
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
  sleep 1
fi
# 포트를 점유한 잔존 vite dev / serve 강제 정리 (pidfile 없이 수동 기동된 경우 대비)
if pid="$(ss -ltnp 2>/dev/null | grep ":${WEB_PORT} " | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)"; then
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "  (포트 ${WEB_PORT} 점유 프로세스 종료: pid=$pid)"
    kill "$pid" 2>/dev/null || true
    sleep 1
  fi
fi
rm -f "$PIDFILE"

# ── 3. dist 정적 서빙 (SPA fallback, detached) ───────────────────────
echo "→ static serve 시작 (port=${WEB_PORT}, SPA fallback)"
setsid nohup "$SERVE_BIN" -s "$FRONTEND_DIR/dist" -l "tcp://0.0.0.0:${WEB_PORT}" \
  > "$SERVE_LOG" 2>&1 < /dev/null &
echo $! > "$PIDFILE"

for i in $(seq 1 15); do
  if curl -fsS -m 2 "http://127.0.0.1:${WEB_PORT}/" >/dev/null 2>&1; then
    echo "✓ frontend serving on ${WEB_PORT} (pid=$(cat "$PIDFILE"))"
    break
  fi
  sleep 1
done

if ! curl -fsS -m 2 "http://127.0.0.1:${WEB_PORT}/" >/dev/null 2>&1; then
  echo "[ERROR] frontend NOT serving on ${WEB_PORT} — 로그: $SERVE_LOG" >&2
  exit 1
fi

echo ""
echo "  포털:     http://127.0.0.1:8088/signalforge/"
echo "  로컬:     http://127.0.0.1:${WEB_PORT}/  (asset base=${BASE_PATH})"
echo "  로그:     tail -f $SERVE_LOG"
