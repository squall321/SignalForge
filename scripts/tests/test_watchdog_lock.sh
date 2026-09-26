#!/usr/bin/env bash
# 워치독 락이 fd 상속으로 영구 mute 되지 않는지 격리 검증한다.
#
# 막는 고장(2026-09-18 ~ 09-25 실제):
#   워치독이 `exec 9>lock` 으로 락을 잡고 up.sh 를 부르면, up.sh 가 띄운
#   apptainer 데몬이 **fd 9 를 상속**한다. 데몬은 죽지 않으니 락도 안 풀리고,
#   그 뒤 매분 `flock -n 9` 가 실패해 워치독이 조용히 exit 0 한다.
#   일주일간 backend·frontend·postgres·celery 가 내려간 채 아무도 몰랐다.
#
# 운영 워치독을 실행하지 않는다 — 락 취득/누수판정/재발급 로직만 검증한다.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WD="$HERE/../watchdog.sh"
TMP="$(mktemp -d)"
SPAWNED=""
trap 'rm -rf "$TMP"; for _p in $SPAWNED; do kill -9 "$_p" 2>/dev/null || true; done' EXIT
pass=0; fail=0
_ok(){ printf '  ✓ %s\n' "$1"; pass=$((pass+1)); }
_no(){ printf '  ✗ %s\n     %s\n' "$1" "${2:-}"; fail=$((fail+1)); }

# ── 1. 소스 규약: up.sh 를 락 fd 없이 부르는가 ────────────────────────────
if grep -qE '^\s*run_nolock\(\)\{.*9>&-' "$WD"; then
  _ok "run_nolock 이 fd 9 를 닫는다"
else
  _no "run_nolock 이 없거나 9>&- 를 안 쓴다" "$(grep -n 'run_nolock()' "$WD" || true)"
fi
bare=$(grep -nE '^\s*bash "\$HERE/up\.sh"' "$WD" || true)
if [ -z "$bare" ]; then
  _ok "up.sh 호출이 모두 run_nolock 을 거친다"
else
  _no "락 fd 를 물려주는 up.sh 호출이 남아 있다" "$bare"
fi

# ── 2. 조용한 skip 이 없는가 ──────────────────────────────────────────────
if grep -qE 'flock -n 9 \|\| exit 0' "$WD"; then
  _no "락 실패 시 로그 없이 exit 0 한다 (벙어리 복귀)" "$(grep -n 'flock -n 9 || exit 0' "$WD")"
else
  _ok "락 실패를 조용히 넘기지 않는다"
fi
if grep -q '_lock_holders' "$WD"; then
  _ok "락 보유자를 진단으로 남긴다"
else
  _no "락이 잡혔을 때 누가 잡았는지 남기지 않는다"
fi

# ── 3. 기제 검증: 9>&- 가 실제로 상속을 막는가 ────────────────────────────
LK="$TMP/probe.lock"
_holders(){ local n=0 pid fd
  for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
    [ -r "/proc/$pid/fd" ] || continue
    for fd in /proc/$pid/fd/*; do
      case "$(readlink "$fd" 2>/dev/null)" in *"$(basename "$LK")") n=$((n+1));; esac
    done
  done 2>/dev/null; echo "$n"; }

# 데몬 대역: fd 를 물려받은 채 오래 사는 프로세스. PID 를 파일로 받아 그것만 죽인다.
( exec 9>"$LK"; flock -n 9 || exit 1
  setsid sh -c 'echo $$ > "$1"; exec sleep 600' _ "$TMP/a.pid" >/dev/null 2>&1 & ) ; sleep 1
A=$(cat "$TMP/a.pid" 2>/dev/null || true); SPAWNED="$SPAWNED $A"
inherited=$(_holders)
[ -n "$A" ] && kill -9 "$A" 2>/dev/null; sleep 1; rm -f "$LK"

( exec 9>"$LK"; flock -n 9 || exit 1
  setsid sh -c 'echo $$ > "$1"; exec sleep 600' _ "$TMP/b.pid" >/dev/null 2>&1 9>&- & ) ; sleep 1
B=$(cat "$TMP/b.pid" 2>/dev/null || true); SPAWNED="$SPAWNED $B"
guarded=$(_holders)
[ -n "$B" ] && kill -9 "$B" 2>/dev/null

if [ "$inherited" -gt 0 ]; then
  _ok "상속 경로 재현됨 (보유 fd ${inherited}개) — 실패 모드가 실재한다"
else
  _no "상속을 재현하지 못했다 — 이 테스트가 아무것도 막지 못한다"
fi
if [ "$guarded" -eq 0 ]; then
  _ok "9>&- 가 상속을 막는다 (보유 fd 0개)"
else
  _no "9>&- 를 써도 데몬이 락을 물려받는다 (${guarded}개)"
fi

# ── 4. 누수된 락을 스스로 끊는가 (재발급 로직) ────────────────────────────
if grep -q 'leaked' "$WD" && grep -qE 'mv -f "\$LOCKFILE"' "$WD"; then
  _ok "워치독 아닌 보유자를 만나면 락을 재발급한다"
else
  _no "누수된 락을 끊지 못해, 경고만 하고 서비스는 계속 내려가 있게 된다"
fi

# ── 5. 경보 억제가 heartbeat 이 아닌 자기 타임스탬프를 쓰는가 ─────────────
# 락이 잡힌 동안 heartbeat 은 갱신될 수 없다 — 그걸 억제 기준으로 쓰면 매분 찍힌다.
blk=$(sed -n '/^if ! _take_lock/,/^fi$/p' "$WD")
if echo "$blk" | grep -q 'ALARM'; then
  _ok "경보 억제에 별도 타임스탬프(ALARM)를 쓴다"
else
  _no "경보 억제가 heartbeat 기준이라 매분 로그가 넘친다"
fi

# ── 6. worker 만이 아니라 beat 도 점검하는가 ──────────────────────────────
# beat 이 죽으면 전 스케줄이 멈추는데 포트 3종과 worker 는 살아 있어 전부 초록이다.
if grep -qE "celery_app beat" "$WD"; then
  _ok "celery beat 도 점검한다"
else
  _no "beat 를 점검하지 않는다 — beat 만 죽으면 전 스케줄 정지에 신호 0"
fi
if grep -q 'celerybeat-schedule' "$WD"; then
  _ok "beat 이 '살아있지만 일하지 않는' 상태도 본다"
else
  _no "beat 프로세스 존재만 보고 스케줄 진척은 보지 않는다"
fi

printf '\n  통과 %d / 실패 %d\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
