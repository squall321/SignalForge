#!/usr/bin/env bash
# SignalForge MCP server — reproducible detached start.
# FastMCP / transport=streamable-http. Endpoint: http://127.0.0.1:$MCP_PORT/mcp
# SF_MCP_TOKEN 설정 시 Authorization: Bearer 검증(에이전트용), 미설정 시 무인증 standalone.
set -euo pipefail

HERE="/home/koopark/claude/SignalForge/mcp-server"
PORT="${MCP_PORT:-8001}"
LOG="/tmp/signalforge-mcp.log"

export MCP_PORT="$PORT"
export DATABASE_URL="postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge"
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
