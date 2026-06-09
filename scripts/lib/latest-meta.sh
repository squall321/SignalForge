#!/usr/bin/env bash
# SignalForge — LATEST.json 메타 헬퍼 (Track Y3 / round=auto_sync)
#
# Drive 의 ApptainerImages:SignalForge/LATEST.json 을 SoT (Single Source of Truth)
# 로 두고, 양방향 자동 동기화에서 변경 감지를 O(1) 로 수행한다.
#
# 형식 (Y3 contract):
#   {
#     "sync_run_id":   "<utc_epoch>-<pid>",
#     "timestamp":     "2026-06-07T23:30:00Z",
#     "source_host":   "<fqdn>",
#     "alembic_head":  "0018",
#     "voc_count":     138805,
#     "db_dump": {
#         "sha256":   "abcd...",
#         "size_mb":  49,
#         "filename": "sf-db-20260607-2330Z.sql.gz"
#     },
#     "sif_changed":   ["backend","crawler"],
#     "sif_sha256sums": {"backend":"...", "crawler":"...", "frontend":"...", "mcp":"..."}
#   }
#
# 의존: jq, sha256sum, du, stat
#
# 사용 (소싱):
#   source "$(dirname "$0")/lib/latest-meta.sh"
#   build_latest_meta /tmp/LATEST.json   # 산출
#   detect_delta      remote_LATEST.json local_LATEST.json
#
# 단독 실행:
#   bash scripts/lib/latest-meta.sh build  [out]   # 기본: stdout
#   bash scripts/lib/latest-meta.sh diff   <remote> <local>   # exit 0=same, 1=delta
#   bash scripts/lib/latest-meta.sh print  <file>             # jq pretty
#
# audit: 호출 측에서 round=auto_sync track=Y3 으로 기록 권장.

set -uo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$LIB_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
SIF_DIR="${SF_SIF_DIR:-$PROJECT_ROOT/apptainer/sif}"
DUMP_DIR="${SF_DUMP_DIR:-$PROJECT_ROOT/backups}"
ALEMBIC_DIR="${SF_ALEMBIC_DIR:-$PROJECT_ROOT/backend/alembic/versions}"

# ── 내부 헬퍼 ────────────────────────────────────────────────────────
_require() {
  command -v "$1" >/dev/null 2>&1 || { echo "[lm] need: $1" >&2; return 1; }
}

_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

_size_mb() {
  # 정수 MB (반올림 X — 양측 동일 계산이 중요)
  local bytes
  bytes="$(stat -c%s "$1" 2>/dev/null || stat -f%z "$1")"
  echo $(( bytes / 1024 / 1024 ))
}

_alembic_head() {
  # 가장 큰 숫자 prefix 의 파일 — 단순/안정 (alembic CLI 의존 X)
  if [[ -d "$ALEMBIC_DIR" ]]; then
    ls "$ALEMBIC_DIR" 2>/dev/null \
      | grep -E '^[0-9]+_' \
      | sort \
      | tail -1 \
      | cut -d_ -f1
  else
    echo "unknown"
  fi
}

_voc_count() {
  # 가동 중 postgres 가 있으면 SELECT count; 없으면 unknown.
  # 매 메타 갱신마다 DB hit 가 부담이면 호출측이 SF_VOC_COUNT env 로 미리 주입.
  if [[ -n "${SF_VOC_COUNT:-}" ]]; then
    echo "$SF_VOC_COUNT"; return
  fi
  if command -v apptainer >/dev/null 2>&1 \
     && apptainer instance list 2>/dev/null | awk '{print $1}' | grep -qx sf_postgres; then
    local v
    v=$(PGPASSWORD="${POSTGRES_PASSWORD:-postgres}" \
        apptainer exec instance://sf_postgres \
        psql -h 127.0.0.1 -p "${POSTGRES_PORT:-5432}" \
             -U "${POSTGRES_USER:-postgres}" \
             -d "${POSTGRES_DB:-signalforge}" \
             -t -A -c "SELECT count(*) FROM voc_records" 2>/dev/null) || v=""
    [[ -n "$v" ]] && { echo "$v"; return; }
  fi
  echo "0"
}

_latest_dump() {
  # 가장 최근 sf-db-YYYYMMDD-HHMMSSZ.sql.gz (safety 접두는 제외)
  ls "$DUMP_DIR"/sf-db-[0-9]*.sql.gz 2>/dev/null | sort | tail -1
}

# ── 공용 API ────────────────────────────────────────────────────────

# build_latest_meta [out_path]  — out 미지정 시 stdout
build_latest_meta() {
  _require jq || return 2
  local out="${1:-/dev/stdout}"

  local ts run_id host alembic voc
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  run_id="$(date -u +%s)-$$"
  host="$(hostname -f 2>/dev/null || hostname)"
  alembic="$(_alembic_head)"
  voc="$(_voc_count)"

  # DB dump 메타
  local dump_path dump_sha dump_size dump_name
  dump_path="$(_latest_dump || true)"
  if [[ -n "${dump_path:-}" && -f "$dump_path" ]]; then
    dump_sha="$(_sha256 "$dump_path")"
    dump_size="$(_size_mb "$dump_path")"
    dump_name="$(basename "$dump_path")"
  else
    dump_sha=""; dump_size=0; dump_name=""
  fi

  # SIF 메타 — 4종 (postgres 는 운영 baseline 이므로 sif_changed 후보 X)
  local sif_obj="{}"
  for svc in backend crawler frontend mcp; do
    local sp="$SIF_DIR/${svc}.sif"
    if [[ -f "$sp" ]]; then
      local sh; sh="$(_sha256 "$sp")"
      sif_obj="$(echo "$sif_obj" | jq --arg k "$svc" --arg v "$sh" '. + {($k): $v}')"
    fi
  done

  # sif_changed: 인자로 외부 전달 가능 (SF_SIF_CHANGED="backend,crawler"),
  # 미지정이면 빈 배열. (실제 변경 감지는 송신 측에서 직전 LATEST 와 비교 후 채움)
  local changed_json="[]"
  if [[ -n "${SF_SIF_CHANGED:-}" ]]; then
    changed_json="$(echo "$SF_SIF_CHANGED" \
                    | tr ',' '\n' \
                    | jq -R . \
                    | jq -s 'map(select(length>0))')"
  fi

  jq -n \
    --arg run_id   "$run_id" \
    --arg ts       "$ts" \
    --arg host     "$host" \
    --arg alembic  "$alembic" \
    --argjson voc  "${voc:-0}" \
    --arg dsha     "$dump_sha" \
    --argjson dsz  "${dump_size:-0}" \
    --arg dname    "$dump_name" \
    --argjson siframe "$sif_obj" \
    --argjson changed "$changed_json" \
    '{
       sync_run_id:  $run_id,
       timestamp:    $ts,
       source_host:  $host,
       alembic_head: $alembic,
       voc_count:    $voc,
       db_dump: {
         sha256:   $dsha,
         size_mb:  $dsz,
         filename: $dname
       },
       sif_changed:    $changed,
       sif_sha256sums: $siframe
     }' \
    > "$out"
}

# detect_delta <remote> <local>
#   exit 0  → 동일 (변경 없음)
#   exit 1  → 변경 있음
#   exit 2  → 입력 오류
#
# 비교 키 (변경으로 간주):
#   - db_dump.sha256 변화          → pull DB
#   - sif_sha256sums.* 변화        → pull SIF (해당 svc)
#   - alembic_head 변화            → schema migration alert
#
# 단순 timestamp / sync_run_id 차이는 변경으로 보지 않음.
# stdout: JSON 한 줄 — {"db": bool, "sif_changed": [...], "alembic": bool}
detect_delta() {
  _require jq || return 2
  local remote="$1" local_="$2"
  [[ -f "$remote" ]] || { echo "[lm] remote 없음: $remote" >&2; return 2; }
  if [[ ! -f "$local_" ]]; then
    # 로컬 첫 동기화 — 전부 받아야 함
    jq -n '{db:true, sif_changed:["backend","crawler","frontend","mcp"], alembic:true, reason:"no_local"}'
    return 1
  fi

  local delta
  delta="$(jq -n \
    --slurpfile r "$remote" \
    --slurpfile l "$local_" \
    '
    ($r[0]) as $R | ($l[0]) as $L |
    {
      db:      (($R.db_dump.sha256 // "") != ($L.db_dump.sha256 // "")),
      alembic: (($R.alembic_head // "")  != ($L.alembic_head // "")),
      sif_changed: (
        ($R.sif_sha256sums // {}) as $rs |
        ($L.sif_sha256sums // {}) as $ls |
        [ $rs | keys[] | select( (($rs[.] // "") != ($ls[.] // "")) ) ]
      )
    } | . + {reason: (
        if .db or .alembic or (.sif_changed|length>0) then "delta" else "same" end
      )}')"

  echo "$delta"

  local changed
  changed="$(echo "$delta" | jq -r '.reason')"
  [[ "$changed" == "same" ]] && return 0 || return 1
}

# print_meta <file>  — 사람이 읽기 좋게
print_meta() {
  _require jq || return 2
  jq . "$1"
}

# ── CLI 진입점 ───────────────────────────────────────────────────────
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  cmd="${1:-help}"
  case "$cmd" in
    build) shift; build_latest_meta "${1:-/dev/stdout}" ;;
    diff)  shift; detect_delta "${1:?remote}" "${2:?local}" ;;
    print) shift; print_meta "${1:?file}" ;;
    help|-h|--help)
      sed -n '1,45p' "$0" ;;
    *) echo "unknown cmd: $cmd  (build|diff|print)" >&2; exit 2 ;;
  esac
fi
