#!/usr/bin/env bash
# SignalForge Y4 — pull 후 검증 / 롤백 헬퍼.
#
# 사용:
#   source "$SCRIPTS_DIR/_verify_helper.sh"
#   sf_snapshot_pre_restore         # restore 전 안전백업 — path 를 stdout
#   sf_verify_after_pull            # rc=0 정상 / rc=1 비정상 (사유 stderr)
#   sf_rollback "$SAFETY_PATH"      # 안전백업으로 복원
#
# 검증 항목 (모두 통과해야 rc=0):
#   V1. voc_records count 가 이전 대비 -50% 미만 (이전치는 PRE_VOC 파일에서)
#   V2. alembic_version.version_num 이 fs 의 최신 migration 과 일치
#   V3. backend health-check 200
#
# 호출자가 set -euo pipefail 후 source.

# ── PG 헬퍼 (drive-sync/_drive_common.sh 미로드 환경 대비 standalone) ─
sf_pg_psql() {
  # stdin/args 그대로 psql 에 위임. tuple-only 호출자가 -tAc 등 옵션 부여.
  PGPASSWORD="${POSTGRES_PASSWORD:?}" \
    psql -h "${POSTGRES_HOST:-127.0.0.1}" -p "${POSTGRES_PORT:?}" \
         -U "${POSTGRES_USER:?}" -d "${POSTGRES_DB:?}" "$@"
}

# ── V1. voc count (pre/post 비교) ────────────────────────────────────
sf_voc_count() {
  sf_pg_psql -tAc "SELECT count(*) FROM voc_records;" 2>/dev/null | tr -d '[:space:]'
}

# 사용:  PRE=$(sf_voc_count); ... restore ...; sf_voc_drop_pct "$PRE" "$(sf_voc_count)"
# stdout: 정수 백분율 (감소율, 음수면 증가). 비교는 호출자.
sf_voc_drop_pct() {
  local pre="$1" post="$2"
  [[ -z "$pre" || "$pre" -eq 0 ]] && { echo 0; return; }
  # (pre-post)*100/pre — 정수 floor
  echo $(( (pre - post) * 100 / pre ))
}

# ── V2. alembic head ────────────────────────────────────────────────
sf_alembic_db_head() {
  sf_pg_psql -tAc "SELECT version_num FROM alembic_version LIMIT 1;" 2>/dev/null | tr -d '[:space:]'
}

# fs 에서 최신 migration prefix (예: "0018") 추출.
# scripts/_verify_helper.sh 가 호출되는 PROJECT_ROOT 가정.
sf_alembic_fs_head() {
  local versions_dir="${1:-$PROJECT_ROOT/backend/alembic/versions}"
  [[ -d "$versions_dir" ]] || { echo ""; return; }
  ls "$versions_dir"/*.py 2>/dev/null \
    | xargs -n1 basename 2>/dev/null \
    | grep -oE '^[0-9]+' \
    | sort -n \
    | tail -1
}

# ── V3. backend health ──────────────────────────────────────────────
sf_health_check() {
  local url="${1:-${PROJ_HEALTH_URL:-http://127.0.0.1:18000/health}}"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url" 2>/dev/null || echo 000)"
  [[ "$code" = "200" ]]
}

# ── 통합 검증: rc=0 정상, rc=1 이상 ──────────────────────────────────
# 환경변수:
#   SF_PRE_VOC          (필수) — restore 전 sf_voc_count 결과
#   SF_HEALTH_URL       (선택) — 미설정시 :18000/health
#   SF_VOC_DROP_LIMIT   (선택, 기본 50) — 이만큼 이상 줄면 fail
#   SF_SKIP_HEALTH      (선택, =1) — 백엔드 미가동 환경에서 V3 스킵
# stdout: 사람 친화 사유 (PASS/FAIL 라인)
# stderr: 실패 사유만
sf_verify_after_pull() {
  local pre="${SF_PRE_VOC:?SF_PRE_VOC 미설정}"
  local limit="${SF_VOC_DROP_LIMIT:-50}"
  local post drop_pct db_head fs_head rc=0

  post="$(sf_voc_count)"
  if [[ -z "$post" ]]; then
    echo "  [V1] voc_records count 조회 실패" >&2
    rc=1
  else
    drop_pct="$(sf_voc_drop_pct "$pre" "$post")"
    if [[ "$drop_pct" -gt "$limit" ]]; then
      echo "  [V1] FAIL voc drop ${drop_pct}% > ${limit}% (pre=$pre post=$post)" >&2
      rc=1
    else
      echo "  [V1] PASS voc pre=$pre post=$post drop=${drop_pct}%"
    fi
  fi

  db_head="$(sf_alembic_db_head)"
  fs_head="$(sf_alembic_fs_head)"
  if [[ -z "$db_head" ]]; then
    echo "  [V2] FAIL alembic_version 조회 실패" >&2
    rc=1
  elif [[ -z "$fs_head" ]]; then
    echo "  [V2] SKIP fs head 미발견 (versions 디렉터리 없음) — db_head=$db_head"
  elif [[ "$db_head" != "$fs_head"* ]]; then
    # db_head 가 fs_head prefix 로 시작하는지 (0018, 0018_xxx 둘 다 허용)
    echo "  [V2] FAIL alembic drift db=$db_head fs_head=$fs_head" >&2
    rc=1
  else
    echo "  [V2] PASS alembic db=$db_head fs=$fs_head"
  fi

  if [[ "${SF_SKIP_HEALTH:-0}" = "1" ]]; then
    echo "  [V3] SKIP (SF_SKIP_HEALTH=1)"
  elif sf_health_check; then
    echo "  [V3] PASS health 200"
  else
    echo "  [V3] FAIL health != 200" >&2
    rc=1
  fi

  # 머신 판독용 결과를 환경변수에 노출
  SF_VERIFY_POST_VOC="$post"
  SF_VERIFY_DROP_PCT="$drop_pct"
  SF_VERIFY_DB_HEAD="$db_head"
  SF_VERIFY_FS_HEAD="$fs_head"
  export SF_VERIFY_POST_VOC SF_VERIFY_DROP_PCT SF_VERIFY_DB_HEAD SF_VERIFY_FS_HEAD

  return "$rc"
}

# ── 안전백업 생성 (restore 전) ───────────────────────────────────────
# stdout: 생성된 sql.gz path. 실패시 rc=1.
sf_snapshot_pre_restore() {
  local out_dir="${1:-${PROJ_DUMP_DIR:-/home/koopark/claude/SignalForge/backups}}"
  mkdir -p "$out_dir"
  local ts path
  ts="$(date -u +%Y%m%d-%H%M%SZ)"
  path="$out_dir/sf-db-PRE-restore-${ts}.sql.gz"
  # apptainer 우선 (sf_postgres 가동 중)
  if command -v apptainer >/dev/null 2>&1 \
       && apptainer instance list 2>/dev/null | awk '{print $1}' | grep -qx "sf_postgres"; then
    PGPASSWORD="${POSTGRES_PASSWORD:?}" \
      apptainer exec instance://sf_postgres \
      pg_dump -h 127.0.0.1 -p "${POSTGRES_PORT:?}" \
              -U "${POSTGRES_USER:?}" -d "${POSTGRES_DB:?}" \
              --no-owner --no-privileges --clean --if-exists 2>/dev/null \
      | gzip -c > "$path" || { rm -f "$path"; return 1; }
  else
    PGPASSWORD="${POSTGRES_PASSWORD:?}" \
      pg_dump -h "${POSTGRES_HOST:-127.0.0.1}" -p "${POSTGRES_PORT:?}" \
              -U "${POSTGRES_USER:?}" -d "${POSTGRES_DB:?}" \
              --no-owner --no-privileges --clean --if-exists 2>/dev/null \
      | gzip -c > "$path" || { rm -f "$path"; return 1; }
  fi
  [[ -s "$path" ]] || { rm -f "$path"; return 1; }
  echo "$path"
}

# ── 롤백 (안전백업으로 복원) ─────────────────────────────────────────
sf_rollback() {
  local safety="$1"
  [[ -f "$safety" ]] || { echo "[ROLLBACK] safety 없음: $safety" >&2; return 2; }
  echo "[ROLLBACK] restoring from $safety"
  # DROP + CREATE 후 restore (DB 자체를 갈아끼움)
  if command -v apptainer >/dev/null 2>&1 \
       && apptainer instance list 2>/dev/null | awk '{print $1}' | grep -qx "sf_postgres"; then
    PGPASSWORD="${POSTGRES_PASSWORD:?}" \
      apptainer exec instance://sf_postgres \
      psql -h 127.0.0.1 -p "${POSTGRES_PORT:?}" -U "${POSTGRES_USER:?}" -d postgres \
           -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB:?}\";" \
           -c "CREATE DATABASE \"${POSTGRES_DB:?}\" OWNER \"${POSTGRES_USER:?}\";" >/dev/null \
      || { echo "[ROLLBACK] DROP/CREATE 실패" >&2; return 1; }
    gunzip -c "$safety" | PGPASSWORD="${POSTGRES_PASSWORD:?}" \
      apptainer exec instance://sf_postgres \
      psql -h 127.0.0.1 -p "${POSTGRES_PORT:?}" \
           -U "${POSTGRES_USER:?}" -d "${POSTGRES_DB:?}" >/dev/null \
      || { echo "[ROLLBACK] restore 실패" >&2; return 1; }
  else
    PGPASSWORD="${POSTGRES_PASSWORD:?}" \
      psql -h "${POSTGRES_HOST:-127.0.0.1}" -p "${POSTGRES_PORT:?}" \
           -U "${POSTGRES_USER:?}" -d postgres \
           -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB:?}\";" \
           -c "CREATE DATABASE \"${POSTGRES_DB:?}\" OWNER \"${POSTGRES_USER:?}\";" >/dev/null \
      || { echo "[ROLLBACK] DROP/CREATE 실패" >&2; return 1; }
    gunzip -c "$safety" | PGPASSWORD="${POSTGRES_PASSWORD:?}" \
      psql -h "${POSTGRES_HOST:-127.0.0.1}" -p "${POSTGRES_PORT:?}" \
           -U "${POSTGRES_USER:?}" -d "${POSTGRES_DB:?}" >/dev/null \
      || { echo "[ROLLBACK] restore 실패" >&2; return 1; }
  fi
  echo "[ROLLBACK] OK ($safety)"
  return 0
}
