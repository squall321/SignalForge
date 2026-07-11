#!/usr/bin/env bash
# SignalForge — 전체 서비스 기동 (전부 Apptainer 컨테이너)
# PostgreSQL / Backend / MCP / Crawler(worker+beat) / Frontend → Apptainer instance
#   코드는 bind-mount, 의존성은 이미지, 시크릿/설정은 host env 상속(load_env) + --env 오버라이드.
# Redis → 시스템 서비스 (이미 실행 중으로 가정)
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy
require_apptainer
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

PG_FRESH=0
if instance_running "$INST_POSTGRES"; then
  echo "✓ $INST_POSTGRES 이미 실행 중"
else
  PG_FRESH=1
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
# 3. 앱 서비스 — Apptainer 컨테이너 (backend / mcp / crawler worker+beat / frontend)
# ─────────────────────────────────────────────────────────────────────────────
for s in backend mcp crawler frontend; do
  [[ -f "$SIF_DIR/$s.sif" ]] || { echo "[ERROR] $s.sif 없음 — 먼저 ./scripts/build.sh $s" >&2; exit 1; }
done

# (a) 구버전 native 프로세스 정리 (컨테이너로 대체)
for pf in backend celery-worker celery-beat mcp; do
  p="$LOG_DIR/$pf.pid"
  if [[ -f "$p" ]] && kill -0 "$(cat "$p")" 2>/dev/null; then
    echo "  (native $pf 종료: $(cat "$p"))"; kill "$(cat "$p")" 2>/dev/null || true
  fi
  rm -f "$p"
done
pkill -f "$MCP_DIR/server.py" 2>/dev/null || true
pkill -f "$PROJECT_ROOT/frontend/serve_prod.py" 2>/dev/null || true
sleep 1

# (b) backend/.env (컨테이너가 /app/.env 로 읽음)
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
PORTAL_JWKS_URL=${PORTAL_JWKS_URL:-}
PORTAL_AUDIENCE=${PORTAL_AUDIENCE:-signalforge}
SF_SESSION_SECRET=${SF_SESSION_SECRET:-}
SESSION_COOKIE_PATH=${SESSION_COOKIE_PATH:-/signalforge/}
SESSION_TTL_SECONDS=${SESSION_TTL_SECONDS:-43200}
PORTAL_SSO_LANDING=${PORTAL_SSO_LANDING:-/signalforge/}
BENV

# (c) DB 마이그레이션 + 시드 — postgres 를 새로 띄운 경우만 (워치독 반복 호출 시 재실행 방지)
if [ "$PG_FRESH" -eq 1 ]; then
  echo "→ alembic upgrade head (컨테이너)"
  apptainer exec --bind "$BACKEND_DIR:/app" --env DATABASE_URL="$DB_URL" \
    "$SIF_DIR/backend.sif" sh -c "cd /app && alembic upgrade head" \
    > "$LOG_DIR/alembic.log" 2>&1 || echo "  [WARN] alembic 실패 — $LOG_DIR/alembic.log"
  echo "→ seed master data (컨테이너)"
  apptainer exec --bind "$BACKEND_DIR:/app" --env DATABASE_URL="$DB_URL" \
    "$SIF_DIR/backend.sif" sh -c "cd /app && python -m app.seeds.seed_master" \
    > "$LOG_DIR/seed.log" 2>&1 || echo "  [WARN] seed 실패(이미 존재 가능)"
else
  echo "✓ postgres 기존 유지 — alembic/seed skip"
fi

# (d) 인스턴스 재기동 헬퍼 — $1=이름 $2=헬스체크(eval 문자열) $3~=옵션+SIF.
#     이미 건강하면 skip → 워치독이 매분 호출해도 정상 서비스는 무중단.
sf_up() {
  local name="$1" check="$2"; shift 2
  # if 조건이라 set -e 안전. transient race(기동 직후 미리스닝 등) 흡수 위해 3회 재시도.
  local i
  for i in 1 2 3; do
    if eval "$check" >/dev/null 2>&1; then echo "✓ $name 정상 (skip)"; return 0; fi
    sleep 1
  done
  apptainer instance stop "$name" 2>/dev/null || true
  echo "→ instance $name"
  apptainer instance start "$@" "$name" > "$LOG_DIR/$name.log" 2>&1
}

sf_up sf-backend "ss -tln 2>/dev/null | grep -E '[:.]${API_PORT:-8000}\b' >/dev/null" \
  --bind "$BACKEND_DIR:/app" \
  --env API_PORT="${API_PORT:-8000}" --env DATABASE_URL="$DB_URL" --env REDIS_URL="$REDIS_URL" \
  "$SIF_DIR/backend.sif"

echo "→ backend /health 대기..."
for i in $(seq 1 30); do
  curl -sf "http://127.0.0.1:${API_PORT:-8000}/health" >/dev/null 2>&1 && { echo "✓ backend ready (${i})"; break; }
  sleep 2
done

sf_up sf-mcp "ss -tln 2>/dev/null | grep -E '[:.]${MCP_PORT:-8001}\b' >/dev/null" \
  --bind "$MCP_DIR:/mcp-server" \
  --env MCP_PORT="${MCP_PORT:-8001}" --env DATABASE_URL="$DB_URL" \
  "$SIF_DIR/mcp.sif"

sf_up sf-crawler-worker "ps -eo args 2>/dev/null | grep -E '[c]elery -A celery_app worker' >/dev/null" \
  --bind "$CRAWLER_DIR:/crawler" \
  --env DATABASE_URL="$DB_URL" --env REDIS_URL="$REDIS_URL" --env CELERY_CONCURRENCY="${CELERY_CONCURRENCY:-4}" \
  "$SIF_DIR/crawler.sif"

sf_up sf-crawler-beat "ps -eo args 2>/dev/null | grep -E '[c]elery -A celery_app beat' >/dev/null" \
  --bind "$CRAWLER_DIR:/crawler" \
  --env DATABASE_URL="$DB_URL" --env REDIS_URL="$REDIS_URL" --env CELERY_ROLE=beat \
  "$SIF_DIR/crawler.sif"

sf_up sf-frontend "ss -tln 2>/dev/null | grep -E '[:.]${WEB_PORT:-17370}\b' >/dev/null" \
  --env SF_WEB_PORT="${WEB_PORT:-17370}" \
  "$SIF_DIR/frontend.sif"

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo " ✅ SignalForge 서비스 기동 완료 (전체 Apptainer 컨테이너)"
echo "================================================================"
echo "  postgres → instance $INST_POSTGRES (:${POSTGRES_PORT})"
echo "  backend  → sf-backend   http://localhost:${API_PORT:-8000}"
echo "  mcp      → sf-mcp       http://localhost:${MCP_PORT:-8001}/mcp"
echo "  crawler  → sf-crawler-worker + sf-crawler-beat"
echo "  frontend → sf-frontend  http://localhost:${WEB_PORT:-17370}"
echo "  redis    → 시스템 서비스 (:${REDIS_PORT:-6379})"
