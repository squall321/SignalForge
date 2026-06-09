#!/usr/bin/env bash
# SignalForge Y4 — flock 기반 동시 실행 가드 (push/pull 양쪽 공통).
#
# 사용:
#   source "$SCRIPTS_DIR/_lock_helper.sh"
#   sf_lock_acquire push 300   # push 측: timeout 300s (5분)
#   sf_lock_acquire pull 600   # pull 측: timeout 600s (10분)
#
# 디자인:
#   * /var/lock 은 보통 root 전용. user 쓰기 가능시 사용, 아니면
#     ${XDG_RUNTIME_DIR:-/tmp}/sf-lock 폴백.
#   * flock -n (non-blocking) 으로 시도 → 실패시 이미 점유 — 즉시 skip.
#     timeout 인자는 락 보유 후의 wall-clock 한도 (background watchdog).
#   * trap 으로 종료 시 자동 해제 (fd 9 닫힘).
#   * audit 호출자가 sf_lock_skip 이벤트 기록.

# 호출자가 set -euo pipefail 한 후 source 한다고 가정.

# ── 락 파일 경로 산정 ────────────────────────────────────────────────
sf_lock_dir() {
  if [[ -w /var/lock ]]; then
    echo "/var/lock"
  else
    local d="${XDG_RUNTIME_DIR:-/tmp}/sf-lock"
    mkdir -p "$d" 2>/dev/null || true
    echo "$d"
  fi
}

# sf_lock_path push|pull
sf_lock_path() {
  local kind="$1"
  case "$kind" in
    push) echo "$(sf_lock_dir)/sf_sync_to.lock" ;;
    pull) echo "$(sf_lock_dir)/sf_sync_from.lock" ;;
    *)    echo "[ERROR] sf_lock_path: kind=$kind (push|pull)" >&2; return 2 ;;
  esac
}

# sf_lock_acquire push|pull [timeout_s]
#   성공: rc=0 (락 보유, fd 9 점유)
#   실패: rc=1 (이미 점유 중) — 호출자가 skip 처리
sf_lock_acquire() {
  local kind="$1"
  local timeout="${2:-300}"
  local lockfile
  lockfile="$(sf_lock_path "$kind")" || return 2

  # fd 9 를 lockfile 에 점유
  exec 9>"$lockfile" || {
    echo "[ERROR] sf_lock_acquire: cannot open $lockfile" >&2
    return 2
  }

  if ! flock -n 9; then
    # 점유 중 — 누가 잡았는지 PID 힌트
    local holder
    holder=$(cat "$lockfile" 2>/dev/null || echo "?")
    echo "[LOCK] $kind 점유 중 (holder pid=$holder, file=$lockfile)" >&2
    exec 9>&-
    return 1
  fi

  # 점유 성공 — 내 PID 기록 + wall-clock watchdog
  echo "$$" >&9 || true
  SF_LOCK_KIND="$kind"
  SF_LOCK_FILE="$lockfile"
  SF_LOCK_TIMEOUT="$timeout"
  SF_LOCK_START="$(date -u +%s)"
  export SF_LOCK_KIND SF_LOCK_FILE SF_LOCK_TIMEOUT SF_LOCK_START

  # 종료 시 자동 해제
  trap 'sf_lock_release' EXIT INT TERM

  # background watchdog (timeout 초과 시 부모 SIGTERM)
  # 중요 (다중 fd 격리):
  #   * fd 9 (lockfile) 를 닫지 않으면 watchdog 가 락을 영구 점유함
  #   * stdout/stderr 를 redirect 하지 않으면 $(...) command-substitution 의
  #     pipe 가 watchdog 종료까지 닫히지 않아 caller 가 hang 됨
  if [[ "$timeout" -gt 0 ]]; then
    (
      exec 9>&-                # ★ 락 fd 상속 끊기
      exec </dev/null >/dev/null 2>&1   # ★ stdio 격리 (caller pipe 차단)
      sleep "$timeout"
      kill -TERM "$$" 2>/dev/null || true
    ) &
    SF_LOCK_WATCHDOG_PID=$!
    disown "$SF_LOCK_WATCHDOG_PID" 2>/dev/null || true
    export SF_LOCK_WATCHDOG_PID
  fi

  return 0
}

sf_lock_release() {
  # watchdog kill
  if [[ -n "${SF_LOCK_WATCHDOG_PID:-}" ]]; then
    kill "$SF_LOCK_WATCHDOG_PID" 2>/dev/null || true
    unset SF_LOCK_WATCHDOG_PID
  fi
  # fd 9 해제 (flock 자동 해제)
  exec 9>&- 2>/dev/null || true
  unset SF_LOCK_KIND SF_LOCK_FILE SF_LOCK_TIMEOUT SF_LOCK_START
}

# 락 보유 경과 (초) — 디버깅용
sf_lock_elapsed() {
  [[ -z "${SF_LOCK_START:-}" ]] && { echo 0; return; }
  echo $(( $(date -u +%s) - SF_LOCK_START ))
}
