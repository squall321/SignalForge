#!/usr/bin/env bash
# =============================================================================
# SignalForge — DB 관리 유틸리티
# 사용: ./scripts/db.sh [migrate|seed|reset|psql]
# =============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIF_DIR="$PROJECT_ROOT/apptainer/sif"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
    set -a && source "$PROJECT_ROOT/.env" && set +a
fi

CMD="${1:-help}"

case "$CMD" in
    migrate)
        echo "==> Alembic 마이그레이션 실행..."
        apptainer exec \
            --env "DATABASE_URL=postgresql+asyncpg://signalforge:${POSTGRES_PASSWORD:-signalforge_pass}@localhost:5432/signalforge" \
            --bind "$PROJECT_ROOT/backend:/app" \
            "$SIF_DIR/backend.sif" \
            sh -c "cd /app && alembic upgrade head"
        echo "✅ 마이그레이션 완료"
        ;;
    seed)
        echo "==> 마스터 데이터 시딩..."
        apptainer exec \
            --env "DATABASE_URL=postgresql+asyncpg://signalforge:${POSTGRES_PASSWORD:-signalforge_pass}@localhost:5432/signalforge" \
            --bind "$PROJECT_ROOT/backend:/app" \
            "$SIF_DIR/backend.sif" \
            sh -c "cd /app && python -m app.seeds.seed_master"
        echo "✅ 시딩 완료"
        ;;
    reset)
        echo "⚠️  DB 전체 초기화 (데이터 삭제)"
        read -p "계속하시겠습니까? [y/N] " confirm
        if [[ "$confirm" != "y" ]]; then
            echo "취소됨"
            exit 0
        fi
        apptainer exec \
            --env "DATABASE_URL=postgresql+asyncpg://signalforge:${POSTGRES_PASSWORD:-signalforge_pass}@localhost:5432/signalforge" \
            --bind "$PROJECT_ROOT/backend:/app" \
            "$SIF_DIR/backend.sif" \
            sh -c "cd /app && alembic downgrade base && alembic upgrade head"
        echo "✅ DB 초기화 완료"
        ;;
    psql)
        echo "==> PostgreSQL 접속..."
        apptainer exec instance://sf-postgres \
            su -c "psql -p 5432 -U signalforge -d signalforge" postgres
        ;;
    help|*)
        echo "Usage: $0 [migrate|seed|reset|psql]"
        echo ""
        echo "  migrate  Alembic 마이그레이션 실행"
        echo "  seed     마스터 데이터 시딩 (제품, 플랫폼, 카테고리)"
        echo "  reset    DB 완전 초기화 (주의: 데이터 삭제)"
        echo "  psql     PostgreSQL CLI 접속"
        ;;
esac
