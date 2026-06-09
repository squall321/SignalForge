#!/usr/bin/env bash
# =============================================================================
# SignalForge — 서비스 로그 tail
# 사용: ./scripts/logs.sh [backend|worker|beat|mcp|nginx]
# =============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SERVICE="${1:-backend}"

case "$SERVICE" in
    backend)
        tail -f "$PROJECT_ROOT/backend/logs/backend.log"
        ;;
    worker)
        tail -f "$PROJECT_ROOT/crawler/logs/worker.log"
        ;;
    beat)
        tail -f "$PROJECT_ROOT/crawler/logs/beat.log"
        ;;
    mcp)
        tail -f "$PROJECT_ROOT/mcp-server/logs/mcp.log"
        ;;
    postgres)
        tail -f "$PROJECT_ROOT/data/postgres/pg.log"
        ;;
    redis)
        tail -f "$PROJECT_ROOT/data/redis/redis.log"
        ;;
    nginx)
        apptainer exec instance://sf-nginx tail -f /var/log/nginx/access.log
        ;;
    *)
        echo "Usage: $0 [backend|worker|beat|mcp|postgres|redis|nginx]"
        exit 1
        ;;
esac
