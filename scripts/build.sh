#!/usr/bin/env bash
# SignalForge — Apptainer 이미지 빌드 (AIDataHub 패턴)
# 사용: ./scripts/build.sh [postgres|backend|crawler|mcp|all] [--force]
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy
require_apptainer

[[ "${2:-}" == "--force" || "${1:-}" == "--force" ]] && FORCE=1 || FORCE=0
export FORCE

SIF_DIR="$APPT_DIR/sif"
mkdir -p "$SIF_DIR"

TARGET="${1:-all}"
[[ "$TARGET" == "--force" ]] && TARGET="all"

case "$TARGET" in
  postgres)
    # 1) base pull
    build_or_pull "$SIF_DIR/postgres-base.sif" "docker://postgres:16-alpine"
    # 2) startscript 래퍼 — def 파일에 절대경로 주입 후 빌드
    if [[ "$FORCE" -eq 1 || ! -f "$SIF_DIR/postgres.sif" ]]; then
      TMP_DEF="$(mktemp /tmp/sf-postgres-XXXX.def)"
      sed "s|POSTGRES_BASE_SIF_PLACEHOLDER|$SIF_DIR/postgres-base.sif|" \
        "$APPT_DIR/postgres.def" > "$TMP_DEF"
      echo "→ build postgres.sif from postgres.def"
      _run_with_fallback apptainer build --fakeroot --force "$SIF_DIR/postgres.sif" "$TMP_DEF"
      rm -f "$TMP_DEF"
    else
      echo "✓ skip  postgres.sif (exists)"
    fi
    ;;
  backend)
    build_or_pull "$SIF_DIR/backend.sif" "" "$APPT_DIR/backend.def"
    ;;
  crawler)
    build_or_pull "$SIF_DIR/crawler.sif" "" "$APPT_DIR/crawler.def"
    ;;
  mcp)
    build_or_pull "$SIF_DIR/mcp.sif" "" "$APPT_DIR/mcp.def"
    ;;
  all)
    build_or_pull "$SIF_DIR/postgres-base.sif" "docker://postgres:16-alpine"
    # postgres wrapper
    if [[ "$FORCE" -eq 1 || ! -f "$SIF_DIR/postgres.sif" ]]; then
      TMP_DEF="$(mktemp /tmp/sf-postgres-XXXX.def)"
      sed "s|POSTGRES_BASE_SIF_PLACEHOLDER|$SIF_DIR/postgres-base.sif|" \
        "$APPT_DIR/postgres.def" > "$TMP_DEF"
      echo "→ build postgres.sif from postgres.def"
      _run_with_fallback apptainer build --fakeroot --force "$SIF_DIR/postgres.sif" "$TMP_DEF"
      rm -f "$TMP_DEF"
    else
      echo "✓ skip  postgres.sif (exists)"
    fi
    build_or_pull "$SIF_DIR/backend.sif"       "" "$APPT_DIR/backend.def"
    build_or_pull "$SIF_DIR/crawler.sif"       "" "$APPT_DIR/crawler.def"
    build_or_pull "$SIF_DIR/mcp.sif"           "" "$APPT_DIR/mcp.def"
    ;;
  *)
    echo "Usage: $0 [postgres|backend|crawler|mcp|all] [--force]"
    exit 1
    ;;
esac

echo
echo "✓ images ready in $SIF_DIR"
echo "  다음: ./scripts/up.sh"
