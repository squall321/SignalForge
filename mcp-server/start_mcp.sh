#!/usr/bin/env bash
# SignalForge MCP server — reproducible detached start.
# FastMCP / transport=streamable-http. Endpoint: http://127.0.0.1:$MCP_PORT/mcp
# SF_MCP_TOKEN 설정 시 Authorization: Bearer 검증(에이전트용), 미설정 시 무인증 standalone.
set -euo pipefail

# 스크립트 실제 위치 기준(하드코딩 금지) — 서버마다 체크아웃 경로가 달라도(~/Projects, ~/claude 등) 동작.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 실가동 포트 8013. server.py 기본값은 8001 이지만 이 호스트는 8001 을 AIDataHub api_server 가
# 점유 중이라, SF MCP 는 8013 으로 띄운다(8001 default 면 포트사용중 가드가 기동을 거부함).
PORT="${MCP_PORT:-8013}"
LOG="/tmp/signalforge-mcp.log"

export MCP_PORT="$PORT"
# DB 는 상위 .env 의 POSTGRES_* 로 짓는다 — 옛 기본 비밀번호를 박아 두지 않는다(2026-11-27). .env 를 통째로 읽지 않는 것은
# 위에서 정한 MCP_PORT 를 덮지 않으려는 것이다(SF_MCP_TOKEN 줄과 같은 방식)
_env() { grep -E "^$1=" "$HERE/../.env" 2>/dev/null | head -1 | cut -d= -f2- || true; }   # 없는 키는 빈 값(set -e 로 끝나지 않게)
PGPW="${POSTGRES_PASSWORD:-$(_env POSTGRES_PASSWORD)}"
[ -n "$PGPW" ] || { echo "상위 .env 에 POSTGRES_PASSWORD 가 없다" >&2; exit 1; }
PGPORT_="${POSTGRES_PORT:-$(_env POSTGRES_PORT)}"
export DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER:-signalforge}:${PGPW}@127.0.0.1:${PGPORT_:-5434}/${POSTGRES_DB:-signalforge}"
# 에이전트용 MCP 토큰 — gitignore된 상위 .env 에서 읽는다(없으면 무인증/standalone).
export SF_MCP_TOKEN="${SF_MCP_TOKEN:-$(grep -E '^SF_MCP_TOKEN=' "$HERE/../.env" 2>/dev/null | head -1 | cut -d= -f2-)}"

# Refuse to start if port already in use.
if ss -ltn 2>/dev/null | grep -qE "[:.]${PORT}\b"; then
  echo "ERROR: port ${PORT} already in use" >&2
  exit 1
fi

cd "$HERE"
setsid bash -c "exec '$HERE/.venv/bin/python' '$HERE/server.py'" \
  > "$LOG" 2>&1 < /dev/null &

echo "SignalForge MCP starting on http://127.0.0.1:${PORT}/mcp (log: $LOG)"
