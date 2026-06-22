#!/usr/bin/env bash
# SignalForge MCP server — reproducible detached start.
# FastMCP / transport=streamable-http. Endpoint: http://127.0.0.1:$MCP_PORT/mcp
# No auth (FastMCP default, no auth_server configured in server.py).
set -euo pipefail

HERE="/home/koopark/claude/SignalForge/mcp-server"
PORT="${MCP_PORT:-8001}"
LOG="/tmp/signalforge-mcp.log"

export MCP_PORT="$PORT"
export DATABASE_URL="postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge"

# Refuse to start if port already in use.
if ss -ltn 2>/dev/null | grep -qE "[:.]${PORT}\b"; then
  echo "ERROR: port ${PORT} already in use" >&2
  exit 1
fi

cd "$HERE"
setsid bash -c "exec '$HERE/.venv/bin/python' '$HERE/server.py'" \
  > "$LOG" 2>&1 < /dev/null &

echo "SignalForge MCP starting on http://127.0.0.1:${PORT}/mcp (log: $LOG)"
