#!/usr/bin/env bash
# SignalForge — Portal Deploy Stage 1
# 4종 apptainer 이미지 빌드 + MANIFEST.json
#
# 정책:
#   - 가동 중인 sf_postgres instance 가 잡고 있는 postgres.sif 는 절대 변경 X
#   - postgres 신 빌드는 postgres.sif.new 에만 기록 (swap 은 본 트랙에서 X)
#   - backend / crawler / mcp 는 신 빌드 (없거나 --force 시)
#
# 사용:
#   ./scripts/build_stage1.sh           # 기본 (sif 존재 시 skip)
#   ./scripts/build_stage1.sh --force   # 전체 강제 재빌드
#
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy
require_apptainer

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1
export FORCE

SIF_DIR="$APPT_DIR/sif"
mkdir -p "$SIF_DIR"

ROUND="portal_deploy"
TRACK="S1"
AUDIT_DIR="$PROJECT_ROOT/logs/audit"
mkdir -p "$AUDIT_DIR"
AUDIT_LOG="$AUDIT_DIR/portal_deploy_S1.jsonl"

_iso_now() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
_audit() {
  # _audit event key=val ...
  local event="$1"; shift
  local extra=""
  for kv in "$@"; do
    # naive escape — 값에 따옴표·백슬래시 사용 시 사전 처리 필요
    local k="${kv%%=*}"
    local v="${kv#*=}"
    extra+=",\"${k}\":\"${v//\"/\\\"}\""
  done
  printf '{"ts":"%s","round":"%s","track":"%s","event":"%s"%s}\n' \
    "$(_iso_now)" "$ROUND" "$TRACK" "$event" "$extra" >> "$AUDIT_LOG"
}

_audit start force="$FORCE" host="$(hostname)"

# ── 0. 가동 중 postgres.sif baseline ────────────────────────────────────
PG_SIF="$SIF_DIR/postgres.sif"
PG_BASELINE_SHA=""
PG_BASELINE_MTIME=""
if [[ -f "$PG_SIF" ]]; then
  PG_BASELINE_SHA="$(sha256sum "$PG_SIF" | awk '{print $1}')"
  PG_BASELINE_MTIME="$(stat -c %Y "$PG_SIF")"
  echo "[BASE] postgres.sif sha256=${PG_BASELINE_SHA:0:12}… mtime_epoch=$PG_BASELINE_MTIME"
  _audit pg_baseline sha256="$PG_BASELINE_SHA" mtime_epoch="$PG_BASELINE_MTIME"
fi

# ── 1. postgres-base (없으면 pull, 기존 재사용) ───────────────────────────
build_or_pull "$SIF_DIR/postgres-base.sif" "docker://postgres:16-alpine"

# ── 2. postgres.sif.new 빌드 (가동 중 postgres.sif 와 분리) ───────────────
PG_NEW="$SIF_DIR/postgres.sif.new"
if [[ "$FORCE" -eq 1 || ! -f "$PG_NEW" ]]; then
  TMP_DEF="$(mktemp /tmp/sf-postgres-XXXX.def)"
  sed "s|POSTGRES_BASE_SIF_PLACEHOLDER|$SIF_DIR/postgres-base.sif|" \
    "$APPT_DIR/postgres.def" > "$TMP_DEF"
  echo "→ build postgres.sif.new (가동 중 postgres.sif 보존)"
  _audit build_start image=postgres.sif.new
  _run_with_fallback apptainer build --fakeroot --force "$PG_NEW" "$TMP_DEF"
  rm -f "$TMP_DEF"
  _audit build_done image=postgres.sif.new
else
  echo "✓ skip  postgres.sif.new (exists)"
fi

# ── 3. backend / crawler / mcp ───────────────────────────────────────────
# 주의: _common.sh::_run_with_fallback 의 if-test 가 실패 rc 를 흘리는 버그가 있어
#       build 실패 시에도 set -e 가 안 잡힌다. → 빌드 후 직접 sif 존재·mtime 검증.
BUILD_FAILED=()
for svc in backend crawler mcp; do
  sif="$SIF_DIR/${svc}.sif"
  def="$APPT_DIR/${svc}.def"
  if [[ "$FORCE" -eq 1 || ! -f "$sif" ]]; then
    _audit build_start image="${svc}.sif"
    pre_mtime=$(stat -c %Y "$sif" 2>/dev/null || echo 0)
    build_or_pull "$sif" "" "$def" || true
    post_mtime=$(stat -c %Y "$sif" 2>/dev/null || echo 0)
    if [[ ! -f "$sif" || "$post_mtime" == "$pre_mtime" ]]; then
      echo "[FAIL] ${svc}.sif 빌드 실패 (파일 없음 또는 mtime 무변경)" >&2
      _audit build_failed image="${svc}.sif"
      BUILD_FAILED+=("$svc")
    else
      _audit build_done image="${svc}.sif"
    fi
  else
    echo "✓ skip  ${svc}.sif (exists)"
  fi
done

if (( ${#BUILD_FAILED[@]} > 0 )); then
  echo "[FATAL] 실패한 이미지: ${BUILD_FAILED[*]}" >&2
  _audit end status=fail failed_images="${BUILD_FAILED[*]}"
  exit 3
fi

# ── 4. 가동 중 postgres.sif 무변경 검증 ─────────────────────────────────
if [[ -n "$PG_BASELINE_SHA" ]]; then
  CUR_SHA="$(sha256sum "$PG_SIF" | awk '{print $1}')"
  CUR_MTIME="$(stat -c %Y "$PG_SIF")"
  if [[ "$CUR_SHA" != "$PG_BASELINE_SHA" || "$CUR_MTIME" != "$PG_BASELINE_MTIME" ]]; then
    echo "[FATAL] postgres.sif 가 변경됨 — 가동 중 instance 위험" >&2
    _audit pg_drift baseline_sha="$PG_BASELINE_SHA" current_sha="$CUR_SHA" baseline_mtime="$PG_BASELINE_MTIME" current_mtime="$CUR_MTIME"
    exit 2
  fi
  echo "[OK] postgres.sif 무변경 (sha256·mtime 동일)"
  _audit pg_invariant ok="1"
fi

# ── 5. MANIFEST.json 생성 ────────────────────────────────────────────────
MANIFEST="$SIF_DIR/MANIFEST.json"
BUILT_AT="$(_iso_now)"
emit_entry() {
  local name="$1"; local path="$2"
  if [[ ! -f "$path" ]]; then return; fi
  local sha; sha="$(sha256sum "$path" | awk '{print $1}')"
  local size_b; size_b="$(stat -c %s "$path")"
  local size_mb; size_mb="$(awk -v b="$size_b" 'BEGIN{printf "%.2f", b/1024/1024}')"
  local mtime; mtime="$(stat -c %y "$path" | sed 's/ /T/; s/\..*/Z/')"
  printf '  "%s": {"path": "%s", "sha256": "%s", "size_mb": %s, "mtime": "%s"}' \
    "$name" "$path" "$sha" "$size_mb" "$mtime"
}

{
  echo "{"
  echo "  \"_generated_at\": \"$BUILT_AT\","
  echo "  \"_round\": \"$ROUND\","
  echo "  \"_track\": \"$TRACK\","
  first=1
  for entry in \
      "postgres:$SIF_DIR/postgres.sif" \
      "postgres_new:$SIF_DIR/postgres.sif.new" \
      "postgres_base:$SIF_DIR/postgres-base.sif" \
      "backend:$SIF_DIR/backend.sif" \
      "crawler:$SIF_DIR/crawler.sif" \
      "mcp:$SIF_DIR/mcp.sif"; do
    name="${entry%%:*}"; path="${entry#*:}"
    if [[ -f "$path" ]]; then
      [[ $first -eq 1 ]] && first=0 || echo ","
      emit_entry "$name" "$path"
    fi
  done
  echo
  echo "}"
} > "$MANIFEST"

echo
echo "✓ MANIFEST: $MANIFEST"
cat "$MANIFEST"
_audit manifest_emitted path="$MANIFEST"
_audit end status=ok

echo
echo "다음:"
echo "  - 검증:  bash scripts/tests/test_build_manifest.sh"
echo "  - swap(별도 트랙): mv $PG_NEW $PG_SIF   (단, sf_postgres instance 정지 후)"
