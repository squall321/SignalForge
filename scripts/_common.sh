#!/usr/bin/env bash
# SignalForge — Apptainer 스크립트 공용 라이브러리 (AIDataHub 패턴)
#
# 사용: 각 스크립트 상단에서
#   source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
APPT_DIR="$PROJECT_ROOT/apptainer"
DATA_DIR="$PROJECT_ROOT/data"
LOG_DIR="$PROJECT_ROOT/logs"

BACKEND_DIR="$PROJECT_ROOT/backend"
CRAWLER_DIR="$PROJECT_ROOT/crawler"
MCP_DIR="$PROJECT_ROOT/mcp-server"

# ── 사내 표준 프록시 폴백 ────────────────────────────────────────────
DEFAULT_FALLBACK_PROXY="http://168.219.61.252:8080"

# ── .env 로드 ────────────────────────────────────────────────────────
load_env() {
  local env_file="$PROJECT_ROOT/.env"
  if [[ ! -f "$env_file" ]]; then
    if [[ -f "$PROJECT_ROOT/.env.example" ]]; then
      cp "$PROJECT_ROOT/.env.example" "$env_file"
      echo "[INFO] .env 자동 생성 (.env.example 복사) — 필요 시 수정 후 재실행"
    else
      echo "[ERROR] .env / .env.example 둘 다 없음" >&2
      exit 1
    fi
  fi
  set -a
  # shellcheck disable=SC1090
  . "$env_file"
  set +a

  : "${APP_NAME:=sf}"
  INST_PREFIX="$APP_NAME"
  INST_POSTGRES="${INST_PREFIX}_postgres"
  export APP_NAME INST_PREFIX INST_POSTGRES
}

# ── 프록시 export ────────────────────────────────────────────────────
export_proxy() {
  local hp="${HTTPS_PROXY:-${https_proxy:-}}"
  local hpp="${HTTP_PROXY:-${http_proxy:-}}"
  local np="${NO_PROXY:-${no_proxy:-}}"

  if [[ -z "$hp" && -n "${BUILD_PROXY_HTTPS:-}" && "${BUILD_PROXY_HTTPS:-}" != "off" ]]; then
    hp="$BUILD_PROXY_HTTPS"
  fi
  if [[ -z "$hpp" ]]; then
    local cand="${BUILD_PROXY_HTTP:-${BUILD_PROXY_HTTPS:-}}"
    if [[ -n "$cand" && "$cand" != "off" ]]; then hpp="$cand"; fi
  fi

  if [[ "${BUILD_PROXY_HTTPS:-}" != "off" ]]; then
    if [[ -z "$hp" ]]; then
      hp="$DEFAULT_FALLBACK_PROXY"
      echo "[INFO] HTTPS_PROXY 미설정 — DEFAULT_FALLBACK_PROXY 적용 ($DEFAULT_FALLBACK_PROXY)"
    fi
    if [[ -z "$hpp" ]]; then hpp="$DEFAULT_FALLBACK_PROXY"; fi
  fi

  local extra="localhost,127.0.0.1,::1"
  if [[ -z "$np" ]]; then
    np="$extra"
  elif [[ ",$np," != *",localhost,"* ]]; then
    np="$np,$extra"
  fi

  if [[ -n "$hp" || -n "$hpp" ]]; then
    export HTTPS_PROXY="$hp"  https_proxy="$hp"
    export HTTP_PROXY="$hpp"  http_proxy="$hpp"
    export NO_PROXY="$np"     no_proxy="$np"
    echo "[INFO] proxy: https=$HTTPS_PROXY no=$NO_PROXY"
  fi
}

# ── 사전 검증 ────────────────────────────────────────────────────────
require_apptainer() {
  if ! command -v apptainer >/dev/null 2>&1; then
    echo "[ERROR] apptainer 미설치" >&2; exit 1
  fi
  echo "[OK] apptainer $(apptainer --version 2>&1 | awk '{print $NF}')"
}

require_python_venv() {
  PYBIN="python3"
  command -v python3.12 >/dev/null 2>&1 && PYBIN="python3.12"
  if ! command -v "$PYBIN" >/dev/null 2>&1; then
    echo "[ERROR] python3 미설치" >&2; exit 1
  fi
  echo "[OK] $($PYBIN --version) + venv"
}

require_port_free() {
  local port="$1" name="$2"
  if ss -tnl 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}\$"; then
    echo "[ERROR] $name 포트 ${port} 이미 사용 중" >&2; exit 1
  fi
  echo "[OK] port ${port} (${name}) 가용"
}

instance_running() {
  apptainer instance list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$1"
}

ensure_dirs() {
  mkdir -p \
    "$DATA_DIR/postgres" \
    "$DATA_DIR/postgres-run" \
    "$LOG_DIR"
}

# ── 폴백 프록시 포함 명령 실행 ───────────────────────────────────────
_run_with_fallback() {
  if "$@"; then return 0; fi
  local rc=$?
  local fb="${BUILD_PROXY_HTTPS:-}"
  if [[ -z "$fb" || "$fb" == "off" ]]; then
    echo "[ERROR] 1차 시도 실패 (rc=$rc) — BUILD_PROXY_HTTPS 미설정이라 폴백 없음" >&2
    return "$rc"
  fi
  echo "[WARN] 1차 실패 — BUILD_PROXY 적용 후 재시도..."
  local np_extra="localhost,127.0.0.1,::1"
  local np="${NO_PROXY:-$np_extra}"
  [[ ",$np," != *",localhost,"* ]] && np="$np,$np_extra"
  env \
    HTTPS_PROXY="$fb" https_proxy="$fb" \
    HTTP_PROXY="$fb"  http_proxy="$fb" \
    NO_PROXY="$np"    no_proxy="$np" \
    "$@"
}

build_or_pull() {
  local sif="$1" src="$2" def="${3:-}"
  local force="${FORCE:-0}"
  if [[ "$force" -eq 0 && -f "$sif" ]]; then
    echo "✓ skip  $(basename "$sif") (exists)"
    return 0
  fi
  if [[ -n "$def" ]]; then
    echo "→ build $(basename "$sif") from $(basename "$def")"
    _run_with_fallback apptainer build --fakeroot --force "$sif" "$def"
  else
    echo "→ pull  $(basename "$sif") from $src"
    _run_with_fallback apptainer pull --force "$sif" "$src"
  fi
}
