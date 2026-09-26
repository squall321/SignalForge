#!/usr/bin/env bash
# Drive-Sync 표준 키트 — 공용 헬퍼.
#
# 호출자가 set -euo pipefail 한 다음 source 한다.
# PROJECT.conf 를 자동 로드하고 prefix-agnostic 변수를 export 한다.

# ── 1. 위치 ──────────────────────────────────────────────────────
DS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 2. PROJECT.conf 로드 ────────────────────────────────────────
if [[ ! -f "$DS_DIR/PROJECT.conf" ]]; then
  echo "[ERROR] $DS_DIR/PROJECT.conf 없음." >&2
  echo "        cp PROJECT.conf.example PROJECT.conf  후 값 채우기" >&2
  exit 1
fi
# shellcheck source=/dev/null
source "$DS_DIR/PROJECT.conf"

# 필수 변수 검증
for var in PROJ_PREFIX PROJ_NAME PROJ_ENV_FILE PROJ_DUMP_DIR; do
  if [[ -z "${!var:-}" ]]; then
    echo "[ERROR] PROJECT.conf: $var 가 비어있음." >&2
    exit 1
  fi
done
: "${PROJ_PG_INSTANCE:=}"
: "${PROJ_DRIVE_REMOTE_DEFAULT:=ApptainerImages}"
: "${PROJ_DRIVE_RETAIN_DEFAULT:=5}"
: "${PROJ_HEALTH_URL:=}"

# ── 3. 프로젝트 .env 로드 (POSTGRES_* 채우기) ───────────────────
if [[ ! -f "$PROJ_ENV_FILE" ]]; then
  echo "[ERROR] PROJ_ENV_FILE 없음: $PROJ_ENV_FILE" >&2
  exit 1
fi
# shellcheck source=/dev/null
set -a; source "$PROJ_ENV_FILE"; set +a
: "${POSTGRES_HOST:=127.0.0.1}"
: "${POSTGRES_PORT:?POSTGRES_PORT 필수 (.env 에서)}"
: "${POSTGRES_USER:?POSTGRES_USER 필수 (.env 에서)}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD 필수 (.env 에서)}"
: "${POSTGRES_DB:?POSTGRES_DB 필수 (.env 에서)}"

# ── 4. Drive 설정 (PROJECT.conf 우선, env override 가능) ────────
DRIVE_REMOTE_NAME="${PROJ_DRIVE_REMOTE_NAME:-$PROJ_DRIVE_REMOTE_DEFAULT}"
DRIVE_FOLDER="${PROJ_DRIVE_FOLDER:-${PROJ_NAME}/db-dumps}"
DRIVE_PATH="${DRIVE_REMOTE_NAME}:${DRIVE_FOLDER}"
DRIVE_RETAIN="${PROJ_DRIVE_RETAIN:-$PROJ_DRIVE_RETAIN_DEFAULT}"

# ── 5. dump 디렉터리 보장 ───────────────────────────────────────
mkdir -p "$PROJ_DUMP_DIR"

# ── 6. dump 파일명 규칙 (TS 기반 정렬용) ────────────────────────
ts_now() { date -u +"%Y%m%d-%H%M%SZ"; }
dump_name() { echo "${PROJ_PREFIX}-db-$(ts_now).sql.gz"; }
dump_glob() { echo "${PROJ_PREFIX}-db-*.sql.gz"; }

# ── 6b. 검증을 통과한 덤프만 최종 이름을 갖는다 ──────────────────────────
#
# 왜 .part 를 거치는가.
#   실패한 덤프가 후보 이름을 가지면, `ls -t | head -1` 로 최신 덤프를 고르는
#   복원 경로(auto-pull.sh:206, bootstrap-new-server.sh:58)가 그것을 집어
#   DB 를 DROP 하고 빈 것을 복원한다. restore-db.sh 가 사후에 tables=0 을 잡아
#   실패로 끝내지만, 그 시점엔 DB 가 이미 비어 있고 수동 롤백이 필요하다.
#
# 전례 두 번.
#   - 20byte 덤프 654개 (apptainer exec 가 cron 에서 빈 출력을 냈다)
#   - 0바이트 덤프 187개 (2026-09-21~25, postgres 가 죽은 채 30분마다 생성)
#   두 번 다 '만들어진 뒤'에 알았다. 만들지 않는 것이 맞다.
#
# 검증 3단: 파이프 성공(pipefail) → gzip 무결성 → 최소 크기.
dump_verified() {   # $1=최종 경로  $2=--no-floor 면 크기 기준 생략
  local out="$1" nofloor="${2:-}" part="$1.part" sz min
  rm -f "$part"
  if ! pg_dump_cmd | gzip -c > "$part"; then
    rm -f "$part"; echo "[FAIL] pg_dump 실패 — 덤프를 만들지 않았다" >&2; return 1
  fi
  if ! gzip -t "$part" 2>/dev/null; then
    rm -f "$part"; echo "[FAIL] gzip 무결성 실패 — 잘린 덤프" >&2; return 1
  fi
  sz=$(stat -c %s "$part")
  if [ "$nofloor" != "--no-floor" ]; then
    min=$(dump_min_bytes)
    if [ "$sz" -lt "$min" ]; then
      rm -f "$part"
      echo "[FAIL] 덤프가 너무 작다: ${sz}B < 기준 ${min}B — DB 가 죽었거나 덤프가 잘렸다" >&2
      return 1
    fi
  fi
  mv -f "$part" "$out"
}

# ── 6c. 덤프 보존정책(얇게 만들기) — Drive·로컬 공용 ──────────────────────
#
# 규칙: 최근 24시간은 전량 보존 → 그 이후는 **하루 1개** → 보존기간 초과는 삭제.
# stdin 으로 파일명(내림차순)을 받아 **삭제할 이름만** 출력한다.
#
# 왜 공용으로 뽑았나 — 같은 규칙이 Drive 에만 있었고 로컬은 '7일 전량'이었다.
# 30분마다 314MB × 7일 = 105GB 로 /home 이 95% 까지 찼다(2026-09-25 실측).
# 디스크가 차면 postgres 가 죽고, 그게 이번 사고의 재발 경로다.
#
# ⚠️ '그날 것 중 최신 하나'를 남기므로, 그날 마지막 덤프가 망가졌으면 그날의 정상
#    백업이 지워진다. 그래서 dump_verified 가 검증 못 한 덤프에 최종 이름을 주지
#    않는 것이 이 정책의 전제다. 둘은 같이 있어야 의미가 있다.
thin_dumps() {   # $1=보존일수 ; stdin=파일명(내림차순) ; stdout=삭제할 이름
  PFX="$PROJ_PREFIX" DAYS="$1" NOW_OVERRIDE="${THIN_NOW:-}" python3 -c '
import os, re, sys, datetime as dt
pfx, days = os.environ["PFX"], int(os.environ["DAYS"])
ov = os.environ.get("NOW_OVERRIDE") or ""
now = (dt.datetime.strptime(ov, "%Y%m%d%H%M%S") if ov
       else dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))
rx = re.compile(rf"^{re.escape(pfx)}-db-(\d{{8}})-(\d{{6}})Z\.sql\.gz$")
keep_day = set()
for name in (l.strip() for l in sys.stdin if l.strip()):
    m = rx.match(name)
    if not m:                            # 이름 규칙이 다르면 건드리지 않는다(안전측)
        continue
    ts = dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    age_h = (now - ts).total_seconds() / 3600
    if age_h <= 24:                      # 최근 24시간은 전부 보존
        continue
    if age_h > days * 24:                # 보존기간 초과 → 삭제
        print(name); continue
    d = m.group(1)
    if d in keep_day:                    # 그날 것 중 최신 하나만 남긴다
        print(name)
    else:
        keep_day.add(d)
'
}

# 최소 크기 기준 — 직전 정상 백업들의 **중간값 절반**.
# 고정 상수는 DB 가 커지면 무의미해진다. 중간값 절반은 '갑자기 쪼그라든 덤프'를 잡되
# 정상적인 증가·감소는 통과시킨다. 이력이 없으면 1MB. DUMP_MIN_BYTES 로 덮어쓸 수 있다.
dump_min_bytes() {
  if [ -n "${DUMP_MIN_BYTES:-}" ]; then echo "$DUMP_MIN_BYTES"; return; fi
  local med
  med=$(find "$PROJ_DUMP_DIR" -maxdepth 1 -name "$(dump_glob)" -size +1M -printf '%s\n' 2>/dev/null \
        | sort -n | awk '{a[NR]=$1} END{if(NR) print a[int((NR+1)/2)]; else print 0}')
  if [ -z "$med" ] || [ "$med" -le 0 ]; then echo 1048576; else echo $((med / 2)); fi
}

# ── 7. PG 명령 추상화 (host client 우선, 없으면 apptainer instance) ────
# ⚠️ cron/detached 에서 `apptainer exec instance://` 는 cgroup manager(systemd/DBUS
#    세션 없음) 로 실패하면서도 '빈 출력'을 내 빈 dump 를 만든다(654개 20byte 백업 사고).
#    host 에 pg_dump 가 있으면(대부분) loopback trust 로 직접 뜨는 게 cron-safe 라 우선.
pg_dump_cmd() {
  # stdout 으로 평문 SQL 출력 — 호출자가 gzip
  if command -v pg_dump >/dev/null 2>&1; then
    PGPASSWORD="$POSTGRES_PASSWORD" \
      pg_dump -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" \
              -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
              --no-owner --no-privileges --clean --if-exists
  elif [[ -n "$PROJ_PG_INSTANCE" ]] && instance_running "$PROJ_PG_INSTANCE"; then
    PGPASSWORD="$POSTGRES_PASSWORD" \
      apptainer exec "instance://$PROJ_PG_INSTANCE" \
      pg_dump -h 127.0.0.1 -p "$POSTGRES_PORT" \
              -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
              --no-owner --no-privileges --clean --if-exists
  else
    echo "[ERROR] pg_dump 불가 — host client 도 instance 도 없음" >&2
    return 1
  fi
}

psql_cmd() {
  # 인자: psql 추가 옵션 (예: -c "SELECT 1"). host client 우선(cron-safe), 없으면 instance.
  if command -v psql >/dev/null 2>&1; then
    PGPASSWORD="$POSTGRES_PASSWORD" \
      psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" \
           -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
           "$@"
  elif [[ -n "$PROJ_PG_INSTANCE" ]] && instance_running "$PROJ_PG_INSTANCE"; then
    PGPASSWORD="$POSTGRES_PASSWORD" \
      apptainer exec "instance://$PROJ_PG_INSTANCE" \
      psql -h 127.0.0.1 -p "$POSTGRES_PORT" \
           -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
           "$@"
  else
    echo "[ERROR] psql 불가 — host client 도 instance 도 없음" >&2
    return 1
  fi
}

instance_running() {
  command -v apptainer >/dev/null 2>&1 || return 1
  # pipefail + 조기종료(grep -q)는 SIGPIPE(141) 오판을 만든다 — 목록을 먼저 받는다(실측 14.7%).
  local _il
  _il="$(apptainer instance list 2>/dev/null)" || return 1
  case $'\n'"$(printf '%s\n' "$_il" | awk '{print $1}')"$'\n' in
    *$'\n'"$1"$'\n'*) return 0 ;;
    *) return 1 ;;
  esac
}

# ── 8. rclone 가용성 ────────────────────────────────────────────
require_rclone() {
  if ! command -v rclone >/dev/null 2>&1; then
    echo "[ERROR] rclone 미설치. sudo apt install -y rclone 또는" >&2
    echo "        curl https://rclone.org/install.sh | sudo bash" >&2
    exit 1
  fi
}

remote_configured() {
  local rc="${RCLONE_CONFIG:-$HOME/.config/rclone/rclone.conf}"
  [[ -f "$rc" ]] && grep -q "^\[$DRIVE_REMOTE_NAME\]" "$rc"
}

# ── 9. 공용 sha256 ─────────────────────────────────────────────
file_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# ── 10. (선택) health-check ────────────────────────────────────
health_check() {
  [[ -z "$PROJ_HEALTH_URL" ]] && return 0
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$PROJ_HEALTH_URL" || echo 000)"
  if [[ "$code" = "200" ]]; then
    echo "[OK] health 200 — $PROJ_HEALTH_URL"
    return 0
  fi
  echo "[WARN] health $code — $PROJ_HEALTH_URL" >&2
  return 1
}
