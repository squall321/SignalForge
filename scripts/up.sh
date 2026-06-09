#!/usr/bin/env bash
# SignalForge — 전체 서비스 기동 (AIDataHub 패턴)
# PostgreSQL → Apptainer instance
# Backend / Crawler(Celery) / MCP → native venv (호스트)
# Redis → 시스템 서비스 (이미 실행 중으로 가정)
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy
require_apptainer
require_python_venv
ensure_dirs

SIF_DIR="$APPT_DIR/sif"

# REDIS_URL 자동 생성 (명시 override 없으면)
if [[ -z "${REDIS_URL:-}" ]]; then
  if [[ -n "${REDIS_PASSWORD:-}" ]]; then
    REDIS_URL="redis://:${REDIS_PASSWORD}@${REDIS_HOST:-127.0.0.1}:${REDIS_PORT:-6379}/0"
  else
    REDIS_URL="redis://${REDIS_HOST:-127.0.0.1}:${REDIS_PORT:-6379}/0"
  fi
fi
export REDIS_URL

DB_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"
export DATABASE_URL="$DB_URL"

# ─────────────────────────────────────────────────────────────────────────────
# 1. PostgreSQL (Apptainer instance)
# ─────────────────────────────────────────────────────────────────────────────
if [[ ! -f "$SIF_DIR/postgres.sif" ]]; then
  echo "[ERROR] postgres.sif 없음. 먼저: ./scripts/build.sh postgres" >&2; exit 1
fi

if instance_running "$INST_POSTGRES"; then
  echo "✓ $INST_POSTGRES 이미 실행 중"
else
  # stale lock 정리
  for f in "$DATA_DIR/postgres-run/.s.PGSQL.${POSTGRES_PORT}.lock" \
           "$DATA_DIR/postgres/pgdata/postmaster.pid"; do
    [[ -e "$f" ]] || continue
    pid="$(head -n1 "$f" 2>/dev/null | tr -dc '0-9')"
    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
      echo "  → stale lock 정리: $(basename "$f")"
      rm -f "$f"
    fi
  done

  require_port_free "$POSTGRES_PORT" "POSTGRES"
  echo "→ start $INST_POSTGRES"
  apptainer instance start \
    --bind "$DATA_DIR/postgres:/var/lib/postgresql/data" \
    --bind "$DATA_DIR/postgres-run:/var/run/postgresql" \
    --env "POSTGRES_USER=${POSTGRES_USER}" \
    --env "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" \
    --env "POSTGRES_DB=${POSTGRES_DB}" \
    --env "PGPORT=${POSTGRES_PORT}" \
    --env "PGDATA=/var/lib/postgresql/data/pgdata" \
    --env "LANG=C.UTF-8" --env "LC_ALL=C.UTF-8" \
    "$SIF_DIR/postgres.sif" "$INST_POSTGRES" \
    > "$LOG_DIR/postgres-start.log" 2>&1
fi

echo "→ pg_isready 대기..."
for i in $(seq 1 60); do
  if apptainer exec "instance://$INST_POSTGRES" \
       pg_isready -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
       >/dev/null 2>&1; then
    echo "✓ postgres ready (${i}s)"; break
  fi
  sleep 1
done

# ─────────────────────────────────────────────────────────────────────────────
# 2. Redis 접속 확인 (시스템 서비스)
# ─────────────────────────────────────────────────────────────────────────────
echo "→ Redis 접속 확인..."
if redis-cli -h "${REDIS_HOST:-127.0.0.1}" -p "${REDIS_PORT:-6379}" \
     ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD"} --no-auth-warning ping \
     2>/dev/null | grep -q PONG; then
  echo "✓ Redis ready"
else
  echo "[WARN] Redis 응답 없음 — Celery가 시작되지 않을 수 있음"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. Backend (native venv)
# ─────────────────────────────────────────────────────────────────────────────
BACKEND_VENV="$BACKEND_DIR/.venv"

if [[ ! -d "$BACKEND_VENV" ]]; then
  echo "→ backend venv 생성..."
  "$PYBIN" -m venv "$BACKEND_VENV"
fi

echo "→ backend pip install..."
"$BACKEND_VENV/bin/pip" install --quiet -r "$BACKEND_DIR/requirements.txt" \
  > "$LOG_DIR/backend-pip.log" 2>&1

# backend/.env 갱신
cat > "$BACKEND_DIR/.env" <<BENV
DATABASE_URL=$DB_URL
REDIS_URL=$REDIS_URL
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
DEEPL_API_KEY=${DEEPL_API_KEY:-}
REDDIT_CLIENT_ID=${REDDIT_CLIENT_ID:-}
REDDIT_CLIENT_SECRET=${REDDIT_CLIENT_SECRET:-}
TWITTER_USERNAME=${TWITTER_USERNAME:-}
TWITTER_PASSWORD=${TWITTER_PASSWORD:-}
API_KEY=${API_KEY:-change-me}
CORS_ORIGINS=${CORS_ORIGINS:-http://localhost:3000}
DEBUG=${DEBUG:-false}
LOG_LEVEL=${LOG_LEVEL:-INFO}
BENV

echo "→ alembic upgrade head"
cd "$BACKEND_DIR"
"$BACKEND_VENV/bin/alembic" upgrade head > "$LOG_DIR/alembic.log" 2>&1

echo "→ seed master data"
PYTHONPATH="$BACKEND_DIR" "$BACKEND_VENV/bin/python" -m app.seeds.seed_master \
  > "$LOG_DIR/seed.log" 2>&1 || echo "  [WARN] seed 실패 (이미 존재할 수 있음)"

# 기존 uvicorn 종료
if [[ -f "$LOG_DIR/backend.pid" ]] && kill -0 "$(cat "$LOG_DIR/backend.pid")" 2>/dev/null; then
  echo "  (기존 uvicorn 종료)"
  kill "$(cat "$LOG_DIR/backend.pid")" || true
  sleep 1
fi

require_port_free "${API_PORT:-8000}" "API"
echo "→ uvicorn 시작 (백그라운드, port=${API_PORT:-8000})"
PYTHONPATH="$BACKEND_DIR" \
nohup "$BACKEND_VENV/bin/uvicorn" app.main:app \
    --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}" \
    > "$LOG_DIR/backend.log" 2>&1 &
echo $! > "$LOG_DIR/backend.pid"

echo "→ /health 대기..."
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${API_PORT:-8000}/health" >/dev/null 2>&1; then
    echo "✓ backend ready (${i}회 시도)"; break
  fi
  sleep 2
done

# ─────────────────────────────────────────────────────────────────────────────
# 4. Crawler venv (Celery worker + beat)
# ─────────────────────────────────────────────────────────────────────────────
# 크롤러는 프로젝트 루트 .venv 공유 (이미 설치됨)
CRAWLER_VENV="$PROJECT_ROOT/.venv"

if [[ ! -d "$CRAWLER_VENV" ]]; then
  echo "→ crawler venv 생성..."
  "$PYBIN" -m venv "$CRAWLER_VENV"
  "$CRAWLER_VENV/bin/pip" install --quiet -r "$CRAWLER_DIR/requirements.txt" \
    > "$LOG_DIR/crawler-pip.log" 2>&1
fi

# 기존 celery 종료
for pidfile in "$LOG_DIR/celery-worker.pid" "$LOG_DIR/celery-beat.pid"; do
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    kill "$(cat "$pidfile")" || true
  fi
done
sleep 1

echo "→ Celery worker 시작 (백그라운드)"
cd "$CRAWLER_DIR"
DATABASE_URL="$DB_URL" REDIS_URL="$REDIS_URL" \
nohup "$CRAWLER_VENV/bin/celery" -A celery_app worker \
    --loglevel=info --concurrency="${CELERY_CONCURRENCY:-4}" \
    > "$LOG_DIR/celery-worker.log" 2>&1 &
echo $! > "$LOG_DIR/celery-worker.pid"

echo "→ Celery beat 시작 (백그라운드)"
DATABASE_URL="$DB_URL" REDIS_URL="$REDIS_URL" \
nohup "$CRAWLER_VENV/bin/celery" -A celery_app beat \
    --loglevel=info \
    > "$LOG_DIR/celery-beat.log" 2>&1 &
echo $! > "$LOG_DIR/celery-beat.pid"

# ─────────────────────────────────────────────────────────────────────────────
# 5. MCP 서버 (native venv — mcp-server 전용)
# ─────────────────────────────────────────────────────────────────────────────
MCP_VENV="$MCP_DIR/.venv"

if [[ ! -d "$MCP_VENV" ]]; then
  echo "→ mcp venv 생성..."
  "$PYBIN" -m venv "$MCP_VENV"
  "$MCP_VENV/bin/pip" install --quiet -r "$MCP_DIR/requirements.txt" \
    > "$LOG_DIR/mcp-pip.log" 2>&1
fi
if [[ -f "$LOG_DIR/mcp.pid" ]] && kill -0 "$(cat "$LOG_DIR/mcp.pid")" 2>/dev/null; then
  kill "$(cat "$LOG_DIR/mcp.pid")" || true; sleep 1
fi

require_port_free "${MCP_PORT:-8001}" "MCP"
echo "→ MCP 서버 시작 (백그라운드, port=${MCP_PORT:-8001})"
DATABASE_URL="$DB_URL" REDIS_URL="$REDIS_URL" \
nohup "$MCP_VENV/bin/python" "$MCP_DIR/server.py" \
    > "$LOG_DIR/mcp.log" 2>&1 &
echo $! > "$LOG_DIR/mcp.pid"

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo " ✅ SignalForge 서비스 기동 완료"
echo "================================================================"
echo "  API      → http://localhost:${API_PORT:-8000}"
echo "  API docs → http://localhost:${API_PORT:-8000}/docs"
echo "  MCP      → http://localhost:${MCP_PORT:-8001}"
echo ""
echo "  상태:   ./scripts/status.sh"
echo "  로그:   tail -f $LOG_DIR/backend.log"
echo "  중지:   ./scripts/down.sh"
echo "================================================================"
