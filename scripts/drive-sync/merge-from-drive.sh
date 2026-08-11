#!/usr/bin/env bash
# SignalForge — Drive 의 dev VOC 덤프를 cae00 운영 DB 에 MERGE(추가·갱신). DROP 안 한다.
# cae00 자체 크롤 축적분은 유지하고 dev 신규/갱신 VOC 만 반영한다(restore-db.sh 의 DROP+통짜
# 복원과 반대 — 비파괴). AIDataHub merge-from-drive 와 같은 원리(staging → COPY → upsert).
#
# 병합 대상:
#   · voc_records     : 자연키 (platform_id, external_id) upsert. surrogate id 는 복사 안 함
#                       (dev/cae00 serial 충돌 방지 — 신규행은 cae00 시퀀스가 새 id 부여).
#   · voc_categories  : PK=code(자연키) upsert.
# 제외: products/platforms(seed_master 로 양쪽 결정적 동일), crawl_jobs(박스별 크롤 이력).
#
#   bash scripts/drive-sync/merge-from-drive.sh              # Drive 최신 덤프로 merge
#   SF_MERGE_DUMP=/path.sql.gz bash ... merge-from-drive.sh  # 로컬 덤프 지정
#   SF_MERGE_TARGET=<db> ...                                 # 대상 DB 오버라이드(테스트용)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_drive_common.sh"

INST="$PROJ_PG_INSTANCE"
[ -n "$INST" ] || { echo "[ERROR] PROJ_PG_INSTANCE 미설정 — 컨테이너 psql 불가"; exit 1; }
TARGET_DB="${SF_MERGE_TARGET:-$POSTGRES_DB}"
STAGE_DB="${TARGET_DB}_mergestage"
CFILE="/tmp/sf_mg.bin"   # 컨테이너 내부 COPY 경로(두 DB 가 같은 인스턴스라 공유)

# 인스턴스 안에서 임의 DB 로 psql
PIN() { local db="$1"; shift; PGPASSWORD="$POSTGRES_PASSWORD" apptainer exec "instance://$INST" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$db" "$@"; }

# 1) 덤프 확보 — 미지정이거나 파일이 없으면 Drive 최신을 받는다.
DUMP="${SF_MERGE_DUMP:-}"
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
  require_rclone
  GLOB_RE="$(dump_glob | sed 's/[.]/[.]/g; s/[*]/.*/g')"
  LATEST="$(rclone lsf "$DRIVE_PATH/" 2>/dev/null | grep -E "^${GLOB_RE}$" | sort | tail -1 || true)"
  [ -n "$LATEST" ] || { echo "· Drive 에 덤프 없음($DRIVE_PATH) — merge 생략(첫 배포면 정상)"; exit 0; }
  mkdir -p "$PROJ_DUMP_DIR"
  [ -f "$PROJ_DUMP_DIR/$LATEST" ] || { echo "→ Drive 덤프 다운로드: $LATEST"; rclone copy "$DRIVE_PATH/$LATEST" "$PROJ_DUMP_DIR/"; }
  DUMP="$PROJ_DUMP_DIR/$LATEST"
fi
[ -f "$DUMP" ] || { echo "[ERROR] 덤프 없음: $DUMP"; exit 1; }
echo "→ merge 소스: $DUMP ($(du -h "$DUMP" | cut -f1)) → 대상 DB: $TARGET_DB"

# 2) 운영 직전 스냅샷(롤백용, 로컬) — 대상이 운영 DB 일 때만.
if [ "$TARGET_DB" = "$POSTGRES_DB" ]; then
  BK="$PROJ_DUMP_DIR/${PROJ_PREFIX}-db-pre-merge-$(ts_now).sql.gz"
  pg_dump_cmd 2>/dev/null | gzip -c > "$BK" && echo "→ 사전 스냅샷: $BK (롤백용)" || echo "  ⚠ 사전 스냅샷 생략"
fi

# 3) staging DB 생성 + 덤프 로드(운영 무손상)
echo "→ staging '$STAGE_DB' 생성 + 덤프 로드"
PIN postgres -q -c "DROP DATABASE IF EXISTS \"$STAGE_DB\" WITH (FORCE);" -c "CREATE DATABASE \"$STAGE_DB\" OWNER \"$POSTGRES_USER\";" >/dev/null
gunzip -c "$DUMP" | PGPASSWORD="$POSTGRES_PASSWORD" apptainer exec -i "instance://$INST" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$STAGE_DB" -q -o /dev/null 2>/tmp/sf-merge-load.log \
  || { echo "[ERROR] staging 로드 실패 — /tmp/sf-merge-load.log"; tail -5 /tmp/sf-merge-load.log; \
       PIN postgres -q -c "DROP DATABASE IF EXISTS \"$STAGE_DB\" WITH (FORCE);" >/dev/null; exit 1; }

# 4) 테이블 merge. $1=table $2=conflict-key(csv) $3=exclude-id(1이면 id 제외)
: > /tmp/sf-merge.log
merge_table() {
  local t="$1" pk="$2" exid="${3:-0}" where="${4:-}"
  # psql 이 접속·인증 실패로 죽으면 stdout 이 빈 문자열이라 '테이블 없음'과 구별되지 않는다.
  # 그러면 전 테이블이 skip 되고 rc 도 안 올라가 '✓ merge 완료' 로 끝난다 — 한 행도 안
  # 들어왔는데 성공으로 보고되는 것이다(비밀번호 회전·포트 변경·컨테이너 재기동 직후에 흔하다).
  # 'f' 가 나와야 진짜 '없음'이고, 빈 문자열은 조회 자체가 실패한 것이다.
  for db in "$TARGET_DB" "$STAGE_DB"; do
    local _ex; _ex="$(PIN "$db" -tAc "SELECT to_regclass('public.$t') IS NOT NULL;" 2>/dev/null)"
    case "$_ex" in
      t) : ;;
      f) echo "    · $t: $db 에 없음 — skip"; return 0 ;;
      *) echo "    ✗ $t: $db 조회 실패(접속·인증 확인) — merge 중단" >&2; return 1 ;;
    esac
  done
  local exclude="$pk"; [ "$exid" = "1" ] && exclude="$pk,id"
  # 삽입 컬럼 = 전체 - (exid 면 id 제외). upsert SET = 전체 - (pk ∪ id).
  local ins; ins="$(PIN "$TARGET_DB" -tAc "SELECT string_agg(quote_ident(column_name),',' ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='public' AND table_name='$t'$([ "$exid" = "1" ] && echo " AND column_name<>'id'");")"
  local setc; setc="$(PIN "$TARGET_DB" -tAc "SELECT string_agg(quote_ident(column_name)||'=EXCLUDED.'||quote_ident(column_name),', ') FROM information_schema.columns WHERE table_schema='public' AND table_name='$t' AND column_name <> ALL (string_to_array('$exclude',','));")"
  local action="DO UPDATE SET $setc"; [ -n "$setc" ] || action="DO NOTHING"

  local before after
  before="$(PIN "$TARGET_DB" -tAc "SELECT count(*) FROM $t;")"
  apptainer exec "instance://$INST" rm -f "$CFILE" 2>/dev/null || true
  PIN "$STAGE_DB" -v ON_ERROR_STOP=1 -q -c "COPY (SELECT $ins FROM public.$t) TO '$CFILE' (FORMAT binary);" 2>>/tmp/sf-merge.log \
    || { echo "    ✗ $t: staging COPY 실패"; return 1; }
  if PIN "$TARGET_DB" -v ON_ERROR_STOP=1 -q >>/tmp/sf-merge.log 2>&1 <<SQL
CREATE TEMP TABLE _mg (LIKE public.$t INCLUDING DEFAULTS);
ALTER TABLE _mg DROP COLUMN IF EXISTS id;
COPY _mg ($ins) FROM '$CFILE' (FORMAT binary);
INSERT INTO public.$t ($ins) SELECT $ins FROM _mg ${where:+WHERE $where} ON CONFLICT ($pk) $action;
DROP TABLE _mg;
SQL
  then after="$(PIN "$TARGET_DB" -tAc "SELECT count(*) FROM $t;")"; echo "    ✓ $t: +$((after-before)) (총 $after)"
  else echo "    ✗ $t: merge 실패 — /tmp/sf-merge.log 확인"; apptainer exec "instance://$INST" rm -f "$CFILE" 2>/dev/null || true; return 1; fi
  apptainer exec "instance://$INST" rm -f "$CFILE" 2>/dev/null || true
}

echo "→ 테이블 merge (부모→자식)"
rc=0
merge_table voc_categories "code"                  0 || rc=1
# voc_records 는 platform_id/product_id FK 참조 — 대상에 없는 참조(seed 드리프트)는 배치 전체를
# 죽이지 않고 해당 행만 건너뛴다(정상 cae00 는 seed_master 로 다 있어 전량 반영). +delta 로 확인.
merge_table voc_records    "platform_id,external_id" 1 \
  "(platform_id IS NULL OR platform_id IN (SELECT id FROM public.platforms)) AND (product_id IS NULL OR product_id IN (SELECT id FROM public.products))" \
  || rc=1

# 5) staging 정리
PIN postgres -q -c "DROP DATABASE IF EXISTS \"$STAGE_DB\" WITH (FORCE);" >/dev/null
[ "$rc" = 0 ] && echo "✓ SF merge 완료 — 운영 유지 + dev 신규 VOC 반영" \
             || { echo "⚠ 일부 merge 실패 — /tmp/sf-merge.log (운영은 스냅샷으로 롤백 가능)"; }
exit "$rc"
