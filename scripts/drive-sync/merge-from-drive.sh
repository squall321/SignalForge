#!/usr/bin/env bash
# SignalForge — Drive 의 dev VOC 덤프를 cae00 운영 DB 에 MERGE(추가·갱신). DROP 안 한다.
# cae00 자체 축적분은 유지하고 dev 신규/갱신 VOC 를 반영한다(restore-db.sh 의 DROP+통짜 복원과 반대 —
# 비파괴). AIDataHub merge-from-drive 와 같은 원리(staging → COPY → upsert).
#
# **정수 id 를 옮기지 않는다.** 카탈로그 id 는 설치 순서로 매겨져 서버마다 다르다 — dev 의 products 3 은
# GS25U 인데 새로 깐 cae00 의 3 은 2012년 모델이다. 예전에는 '양쪽 seed 가 같다' 고 보고 voc_records 의
# platform_id·product_id 를 정수 그대로 옮겨, 글이 엉뚱한 제품·플랫폼에 조용히 붙었다(cae00 2026-10-27 —
# dcinside 글이 naver_cafe 로, 공식 커뮤니티 앱 글이 wpnews 로, SG-02 · SG-15). 이제 code(자연키)로 번역한다.
#
# 병합(한 트랜잭션 — 끝의 표본 대조가 어긋나면 통째로 되돌린다)
#   · platforms · products : code upsert — 대상에 빠진 행을 넣는다. 플랫폼 is_active 는 서버 몫이라 그대로
#   · voc_categories       : code upsert
#   · voc_records          : (code 로 번역한 platform_id, external_id) upsert, product_id 도 code 로 번역
#   · voc_product_links · voc_defects : 글은 자연키로, 제품은 code 로 번역 — dev 가 아는 글의 링크·결함은 dev 와
#     같게(dev 가 뗀 링크는 뗀다). 예전 병합은 이 둘을 옮기지 않아 cae00 에 0행이었다(SG-05 · SG-17)
#   · 예전 병합이 정수 그대로 넣은 행 — 원천 행과 (원래 id, external_id) 가 같고 source_url 이나 content_hash
#     까지 같으면 제자리 플랫폼으로 옮긴다. 이미 제자리인 행은 건드리지 않는다(멱등)
# 병합 뒤 — 미리 계산하는 뷰를 다시 채운다(수집하지 않는 서버는 beat 가 없어 뷰가 비거나 낡는다, SG-06).
# 제외: crawl_jobs(박스별 크롤 이력). dev 에 없는 대상 행(이 서버가 모은 글, 병합 뒤 dev 가 지운 글)은 세기만 한다.
#
#   bash scripts/drive-sync/merge-from-drive.sh              # Drive 최신 덤프로 merge
#   SF_MERGE_DUMP=/path.sql.gz bash ... merge-from-drive.sh  # 로컬 덤프 지정
#   SF_MERGE_TARGET=<db> ...                                 # 대상 DB 오버라이드(테스트용)
#   SF_MERGE_SAMPLE=300                                      # 표본 대조 크기
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_drive_common.sh"

INST="$PROJ_PG_INSTANCE"
[ -n "$INST" ] || { echo "[ERROR] PROJ_PG_INSTANCE 미설정 — 컨테이너 psql 불가"; exit 1; }
TARGET_DB="${SF_MERGE_TARGET:-$POSTGRES_DB}"
STAGE_DB="${TARGET_DB}_mergestage"
SAMPLE="${SF_MERGE_SAMPLE:-300}"
CPREFIX="/tmp/sf_mg_${TARGET_DB}_$$"   # 컨테이너 안 COPY 파일(두 DB 가 같은 인스턴스라 공유)
LOG="/tmp/sf-merge-${TARGET_DB}.log"

# 겹쳐 돌지 않게 — 첫 병합은 옛 정수 복사 행을 거의 다 고쳐 써 오래 걸린다(실규모 123만 행 27분). 그동안 30분
# cron 이 또 부르면 같은 staging DB 를 지우고 다시 만들고 같은 행 잠금을 기다린다. 이번 차례는 넘긴다.
exec 8>"/tmp/sf-merge-${TARGET_DB}.lock"
flock -n 8 || { echo "· 다른 병합이 ${TARGET_DB} 에서 도는 중 — 이번 차례는 건너뛴다"; exit 0; }

# 인스턴스 안에서 임의 DB 로 psql
PIN() { local db="$1"; shift; PGPASSWORD="$POSTGRES_PASSWORD" apptainer exec "instance://$INST" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$db" "$@"; }

cleanup() {
  apptainer exec "instance://$INST" sh -c "rm -f ${CPREFIX}_*.bin" 2>/dev/null || true
  PIN postgres -q -c "DROP DATABASE IF EXISTS \"$STAGE_DB\" WITH (FORCE);" >/dev/null 2>&1 || true
}

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
trap cleanup EXIT
echo "→ staging '$STAGE_DB' 생성 + 덤프 로드"
PIN postgres -q -c "DROP DATABASE IF EXISTS \"$STAGE_DB\" WITH (FORCE);" -c "CREATE DATABASE \"$STAGE_DB\" OWNER \"$POSTGRES_USER\";" >/dev/null
gunzip -c "$DUMP" | PGPASSWORD="$POSTGRES_PASSWORD" apptainer exec -i "instance://$INST" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$STAGE_DB" -q -o /dev/null 2>/tmp/sf-merge-load.log \
  || { echo "[ERROR] staging 로드 실패 — /tmp/sf-merge-load.log"; tail -5 /tmp/sf-merge-load.log; exit 1; }

# 4) 표의 존재와 열 — psql 이 접속·인증 실패로 죽으면 stdout 이 빈 문자열이라 '테이블 없음'과 구별되지
#    않는다. 그러면 전부 skip 되고 '✓ merge 완료' 로 끝난다(한 행도 안 들어왔는데 성공). 't'/'f' 만 믿는다.
has_table() {  # $1=db $2=table → 0 있음 · 1 없음 · 2 조회 실패
  local r; r="$(PIN "$1" -tAc "SELECT to_regclass('public.$2') IS NOT NULL;" 2>/dev/null)"
  case "$r" in t) return 0 ;; f) return 1 ;; *) return 2 ;; esac
}
cols_of() {  # $1=db $2=table → 열 이름(정의 순서), 한 줄에 하나
  PIN "$1" -tAc "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='$2' ORDER BY ordinal_position;"
}
declare -A COLS   # 표 → 양쪽에 다 있는 열(대상 순서, 공백 구분)
TABLES=()
for t in platforms products voc_categories voc_records voc_product_links voc_defects; do
  for db in "$TARGET_DB" "$STAGE_DB"; do
    if has_table "$db" "$t"; then :; else
      rc=$?
      if [ "$rc" = 2 ]; then echo "    ✗ $t: $db 조회 실패(접속·인증 확인) — merge 중단" >&2; exit 1; fi
      case "$t" in
        platforms|products|voc_records) echo "    ✗ $t: $db 에 없음 — merge 할 수 없다" >&2; exit 1 ;;
        *) echo "    · $t: $db 에 없음 — skip"; continue 2 ;;
      esac
    fi
  done
  mapfile -t _tc < <(cols_of "$TARGET_DB" "$t")
  mapfile -t _sc < <(cols_of "$STAGE_DB" "$t")
  _common=()
  for c in "${_tc[@]}"; do
    for d in "${_sc[@]}"; do [ "$c" = "$d" ] && { _common+=("$c"); break; }; done
  done
  [ "${#_common[@]}" -gt 0 ] || { echo "    ✗ $t: 열 조회 실패" >&2; exit 1; }
  COLS[$t]="${_common[*]}"
  TABLES+=("$t")
done
has() { local x; for x in "${TABLES[@]}"; do [ "$x" = "$1" ] && return 0; done; return 1; }
csv() { local IFS=,; echo "$*"; }
# 열 목록 조립 — $1=표 $2=빼는 열(공백 구분)
cols_except() { local c out=(); for c in ${COLS[$1]}; do [[ " $2 " == *" $c "* ]] || out+=("$c"); done; csv "${out[@]}"; }
set_list() {   # $1=표 $2=빼는 열 → "a=EXCLUDED.a, ..."  ·  distinct 비교용 두 줄은 아래
  local c out=(); for c in ${COLS[$1]}; do [[ " $2 " == *" $c "* ]] || out+=("$c=EXCLUDED.$c"); done; local IFS=,; echo "${out[*]}"
}
distinct_cond() {  # $1=표 $2=빼는 열 → "(t.a, t.b) IS DISTINCT FROM (EXCLUDED.a, EXCLUDED.b)" — 바뀐 행만 쓴다
  local c l=() r=(); for c in ${COLS[$1]}; do [[ " $2 " == *" $c "* ]] || { l+=("$1.$c"); r+=("EXCLUDED.$c"); }; done
  [ "${#l[@]}" -gt 0 ] || { echo "false"; return; }
  echo "($(csv "${l[@]}")) IS DISTINCT FROM ($(csv "${r[@]}"))"
}

# 5) staging → 파일(COPY binary). id 까지 — 원천 id 는 번역 표의 열쇠다.
for t in "${TABLES[@]}"; do
  PIN "$STAGE_DB" -v ON_ERROR_STOP=1 -q -c \
    "COPY (SELECT $(csv ${COLS[$t]}) FROM public.$t) TO '${CPREFIX}_$t.bin' (FORMAT binary);" 2>>"$LOG" \
    || { echo "    ✗ $t: staging COPY 실패 — $LOG"; exit 1; }
done

# 6) 대상 — 한 세션 · 한 트랜잭션
load_sql() {
  local t; for t in "${TABLES[@]}"; do
    echo "CREATE TEMP TABLE _s_$t (LIKE public.$t INCLUDING DEFAULTS);"
    echo "COPY _s_$t ($(csv ${COLS[$t]})) FROM '${CPREFIX}_$t.bin' (FORMAT binary);"
    echo "ANALYZE _s_$t;"
  done
}
voc_exprs() {  # voc_records 삽입 열의 값 — 두 id 만 번역
  local c out=(); for c in ${COLS[voc_records]}; do
    case "$c" in id) ;; platform_id) out+=("pm.dst") ;; product_id) out+=("rm.dst") ;; *) out+=("s.$c") ;; esac
  done; csv "${out[@]}"
}
move_set() {  # 옮기는 행을 원천 값(제품은 번역)으로 맞추는 SET — 키(id·platform_id·external_id) 빼고
  local c out=(); for c in ${COLS[voc_records]}; do
    case "$c" in id|platform_id|external_id) ;; product_id) out+=("product_id = rm.dst") ;; *) out+=("$c = s.$c") ;; esac
  done; local IFS=,; echo "${out[*]}"
}
voc_changed() {  # 대상 행(v)이 번역한 원천 값과 다른가 — 같은 행은 upsert 에 넣지 않는다
  local c l=() r=(); for c in ${COLS[voc_records]}; do
    case "$c" in id|platform_id|external_id) ;; product_id) l+=("v.product_id"); r+=("rm.dst") ;; *) l+=("v.$c"); r+=("s.$c") ;; esac
  done; echo "($(csv "${l[@]}")) IS DISTINCT FROM ($(csv "${r[@]}"))"
}
child_sql() {  # $1=표 $2=키 열(공백) — 글은 _vmap, 제품은 _rmap 로 번역. dev 가 아는 글은 dev 와 같게
  local t="$1" keys="$2" c ex=() on=()
  for c in ${COLS[$t]}; do
    case "$c" in voc_id) ex+=("vm.dst AS voc_id") ;; product_id) ex+=("rm.dst AS product_id") ;; *) ex+=("x.$c AS $c") ;; esac
  done
  for c in $keys; do on+=("w.$c = t.$c"); done
  local nk=() nl=() nr=(); for c in ${COLS[$t]}; do
    [[ " $keys created_at " == *" $c "* ]] || { nl+=("t.$c"); nr+=("w.$c"); }
  done
  local first="${keys%% *}" changed
  changed="t.$first IS NULL"; [ "${#nl[@]}" -gt 0 ] && changed+=" OR ($(csv "${nl[@]}")) IS DISTINCT FROM ($(csv "${nr[@]}"))"
  local join_r=""; [[ " ${COLS[$t]} " == *" product_id "* ]] && join_r="JOIN _rmap rm ON rm.src = x.product_id"
  local onj; onj="$(printf '%s AND ' "${on[@]}")"; onj="${onj% AND }"
  cat <<SQL
CREATE TEMP TABLE _w_$t AS
  SELECT $(local IFS=,; echo "${ex[*]}") FROM _s_$t x JOIN _vmap vm ON vm.src = x.voc_id $join_r;
CREATE INDEX ON _w_$t ($(csv $keys));
ANALYZE _w_$t;
WITH d AS (DELETE FROM public.$t t USING _vmap vm
           WHERE t.voc_id = vm.dst AND NOT EXISTS (SELECT 1 FROM _w_$t w WHERE $onj)
           RETURNING 1)
SELECT 'R|${t}_del|' || count(*) FROM d;
WITH up AS (INSERT INTO public.$t ($(csv ${COLS[$t]}))
            SELECT $(printf 'w.%s,' ${COLS[$t]} | sed 's/,$//') FROM _w_$t w
            LEFT JOIN public.$t t ON $onj
            WHERE $changed
            ON CONFLICT ($(csv $keys)) DO $( s="$(set_list "$t" "$keys created_at")"; [ -n "$s" ] \
                && echo "UPDATE SET $s WHERE $(distinct_cond "$t" "$keys created_at")" || echo "NOTHING")
            RETURNING (xmax = 0) AS ins)
SELECT 'R|${t}|' || count(*) FILTER (WHERE ins) || '|' || count(*) FILTER (WHERE NOT ins) FROM up;
SQL
}

{
  echo "\\set ON_ERROR_STOP 1"
  echo "BEGIN;"
  load_sql
  # 6a 카탈로그 — code upsert. 플랫폼 is_active 는 그 서버의 수집 스위치라 덮지 않는다
  cat <<SQL
WITH up AS (INSERT INTO public.platforms ($(cols_except platforms id)) SELECT $(cols_except platforms id) FROM _s_platforms
            ON CONFLICT (code) DO UPDATE SET $(set_list platforms "id code is_active")
              WHERE $(distinct_cond platforms "id code is_active")
            RETURNING (xmax = 0) AS ins)
SELECT 'R|platforms|' || count(*) FILTER (WHERE ins) || '|' || count(*) FILTER (WHERE NOT ins) FROM up;
WITH up AS (INSERT INTO public.products ($(cols_except products id)) SELECT $(cols_except products id) FROM _s_products
            ON CONFLICT (code) DO UPDATE SET $(set_list products "id code created_at")
              WHERE $(distinct_cond products "id code created_at")
            RETURNING (xmax = 0) AS ins)
SELECT 'R|products|' || count(*) FILTER (WHERE ins) || '|' || count(*) FILTER (WHERE NOT ins) FROM up;
CREATE TEMP TABLE _pmap AS SELECT s.id AS src, t.id AS dst FROM _s_platforms s JOIN public.platforms t ON t.code = s.code;
CREATE UNIQUE INDEX ON _pmap (src); CREATE UNIQUE INDEX ON _pmap (dst); ANALYZE _pmap;
CREATE TEMP TABLE _rmap AS SELECT s.id AS src, t.id AS dst FROM _s_products s JOIN public.products t ON t.code = s.code;
CREATE UNIQUE INDEX ON _rmap (src); ANALYZE _rmap;
CREATE INDEX ON _s_voc_records (platform_id, external_id);
CREATE UNIQUE INDEX ON _s_voc_records (id);
ANALYZE _s_voc_records;
SQL
  has voc_categories && cat <<SQL
WITH up AS (INSERT INTO public.voc_categories ($(cols_except voc_categories "")) SELECT $(cols_except voc_categories "") FROM _s_voc_categories
            ON CONFLICT (code) DO $( s="$(set_list voc_categories code)"; [ -n "$s" ] \
                && echo "UPDATE SET $s WHERE $(distinct_cond voc_categories code)" || echo "NOTHING")
            RETURNING (xmax = 0) AS ins)
SELECT 'R|voc_categories|' || count(*) FILTER (WHERE ins) || '|' || count(*) FILTER (WHERE NOT ins) FROM up;
SQL
  # 6b 예전 병합이 정수 그대로 넣은 행을 제자리로. 근거는 '원래 id 로 읽은 원천 행과 같은 글' 이고
  #    (source_url 이나 content_hash 가 같다), 번역한 자리로 읽어도 같은 글이면 이미 제자리다.
  #    옮길 자리에 다른 행이 있으면(이 서버가 모은 같은 글 등) 옮기지 않고 센다.
  cat <<SQL
CREATE TEMP TABLE _fix AS
  SELECT t.id, t.external_id, m.dst AS new_pid, s.id AS src, false AS done
  FROM public.voc_records t
  JOIN _s_voc_records s ON s.platform_id = t.platform_id AND s.external_id = t.external_id
  JOIN _pmap m ON m.src = s.platform_id
  WHERE m.dst <> t.platform_id
    AND (t.source_url = s.source_url OR t.content_hash = s.content_hash)
    AND NOT EXISTS (SELECT 1 FROM _pmap m2
                    JOIN _s_voc_records s2 ON s2.platform_id = m2.src AND s2.external_id = t.external_id
                    WHERE m2.dst = t.platform_id
                      AND (t.source_url = s2.source_url OR t.content_hash = s2.content_hash));
CREATE UNIQUE INDEX ON _fix (id);
CREATE TEMP TABLE _fix_blocked (id bigint);
DO \$\$ BEGIN
  LOOP   -- 막힌 행이 빠지면 그 행의 자리가 또 다른 이동을 막는다 — 더 빠지는 것이 없을 때까지
    WITH b AS (DELETE FROM _fix f USING public.voc_records o
               WHERE o.platform_id = f.new_pid AND o.external_id = f.external_id
                 AND NOT EXISTS (SELECT 1 FROM _fix f2 WHERE f2.id = o.id)
               RETURNING f.id)
    INSERT INTO _fix_blocked SELECT id FROM b;
    EXIT WHEN NOT FOUND;
  END LOOP;
END \$\$;
-- 옮기기 — 지금 빈 자리로 가는 행부터 차례로(옮긴 행이 비운 자리로 다음 차례가 간다). 옮기면서 원천 값(번역한
-- 제품 포함)으로 함께 맞춘다. voc_records 는 색인 열이 바뀌면 HOT 이 안 돼 행마다 FTS GIN 색인까지 다시 쓴다 —
-- 비켜 놓기 · 되돌리기 · upsert 로 세 번 쓰면 실규모 첫 병합이 10분 넘게 한 문장에 묶였다(2026-10-27 실측).
-- 한 번에 끝내고, 서로 자리를 바꾸는 고리(A→B, B→A)만 external_id 를 잠시 비켜 놓았다가 옮긴다.
DO \$\$
DECLARE n bigint;
BEGIN
  LOOP
    WITH mv AS (
      SELECT f.id, f.new_pid, f.src FROM _fix f
      WHERE NOT f.done
        AND NOT EXISTS (SELECT 1 FROM public.voc_records o
                        WHERE o.platform_id = f.new_pid AND o.external_id = f.external_id)),
    u AS (
      UPDATE public.voc_records v SET platform_id = mv.new_pid, $(move_set)
      FROM mv JOIN _s_voc_records s ON s.id = mv.src LEFT JOIN _rmap rm ON rm.src = s.product_id
      WHERE v.id = mv.id
      RETURNING v.id)
    UPDATE _fix f SET done = true FROM u WHERE f.id = u.id;
    GET DIAGNOSTICS n = ROW_COUNT;
    EXIT WHEN n = 0;
  END LOOP;
END \$\$;
UPDATE public.voc_records v SET external_id = chr(1) || v.id::text FROM _fix f WHERE v.id = f.id AND NOT f.done;
UPDATE public.voc_records v SET platform_id = f.new_pid, external_id = f.external_id, $(move_set)
  FROM _fix f JOIN _s_voc_records s ON s.id = f.src LEFT JOIN _rmap rm ON rm.src = s.product_id
  WHERE v.id = f.id AND NOT f.done;
SELECT 'R|repaired|' || (SELECT count(*) FROM _fix) || '|' || (SELECT count(*) FROM _fix_blocked);
SQL
  # 6c voc_records — 번역한 키로 upsert. 새것·바뀐 것만 넣는다 — ON CONFLICT 는 WHERE 를 보기 전에 기존 행을
  #    잠가(행 머리에 쓰기) 그대로 넘기면 30분마다 전 행의 페이지를 더럽힌다
  cat <<SQL
WITH up AS (INSERT INTO public.voc_records ($(cols_except voc_records id))
            SELECT $(voc_exprs) FROM _s_voc_records s
            JOIN _pmap pm ON pm.src = s.platform_id
            LEFT JOIN _rmap rm ON rm.src = s.product_id
            LEFT JOIN public.voc_records v ON v.platform_id = pm.dst AND v.external_id = s.external_id
            WHERE s.external_id IS NOT NULL AND (v.id IS NULL OR $(voc_changed))
            ON CONFLICT (platform_id, external_id) DO UPDATE SET $(set_list voc_records "id platform_id external_id")
              WHERE $(distinct_cond voc_records "id platform_id external_id")
            RETURNING (xmax = 0) AS ins)
SELECT 'R|voc_records|' || count(*) FILTER (WHERE ins) || '|' || count(*) FILTER (WHERE NOT ins) FROM up;
SELECT 'R|nokey|' || count(*) FROM _s_voc_records WHERE platform_id IS NULL OR external_id IS NULL;
CREATE TEMP TABLE _vmap AS
  SELECT s.id AS src, v.id AS dst FROM _s_voc_records s
  JOIN _pmap pm ON pm.src = s.platform_id
  JOIN public.voc_records v ON v.platform_id = pm.dst AND v.external_id = s.external_id;
CREATE UNIQUE INDEX ON _vmap (src); CREATE UNIQUE INDEX ON _vmap (dst); ANALYZE _vmap;
SQL
  has voc_product_links && child_sql voc_product_links "voc_id product_id"
  has voc_defects && child_sql voc_defects "voc_id component symptom"
  # 6d 남은 것 세기 — 제자리로 못 옮긴 옛 행 · dev 에 없는 행
  cat <<SQL
SELECT 'R|raw_left|' || count(*) FROM public.voc_records t
  JOIN _s_voc_records s ON s.platform_id = t.platform_id AND s.external_id = t.external_id
  JOIN _pmap m ON m.src = s.platform_id
  WHERE m.dst <> t.platform_id AND (t.source_url = s.source_url OR t.content_hash = s.content_hash)
    AND NOT EXISTS (SELECT 1 FROM _vmap vm WHERE vm.dst = t.id);
SELECT 'R|not_in_source|' || count(*) FROM public.voc_records t WHERE NOT EXISTS (SELECT 1 FROM _vmap vm WHERE vm.dst = t.id);
SQL
  # 6e 표본 대조 — 원천 표본을 대상에서 code 로 찾아 제품·본문·링크·결함이 같은지. 하나라도 어긋나면 되돌린다
  cat <<SQL
CREATE TEMP TABLE _smp AS SELECT id FROM _s_voc_records
  WHERE platform_id IS NOT NULL AND external_id IS NOT NULL ORDER BY random() LIMIT $SAMPLE;
CREATE TEMP TABLE _chk AS
  SELECT sm.id AS sid, v.id AS vid,
         spr.code AS s_prod, tpr.code AS t_prod,
         (s.source_url IS NOT DISTINCT FROM v.source_url AND s.content_hash IS NOT DISTINCT FROM v.content_hash
          AND s.content_original IS NOT DISTINCT FROM v.content_original) AS same_body
  FROM _smp sm
  JOIN _s_voc_records s ON s.id = sm.id
  LEFT JOIN _s_products spr ON spr.id = s.product_id
  LEFT JOIN _vmap vm ON vm.src = sm.id
  LEFT JOIN public.voc_records v ON v.id = vm.dst
  LEFT JOIN public.products tpr ON tpr.id = v.product_id;
SQL
  if has voc_product_links; then cat <<SQL
ALTER TABLE _chk ADD COLUMN s_links text, ADD COLUMN t_links text;
CREATE INDEX ON _s_voc_product_links (voc_id);
UPDATE _chk c SET
  s_links = (SELECT string_agg(p.code || ':' || l.role, ',' ORDER BY p.code) FROM _s_voc_product_links l
             JOIN _s_products p ON p.id = l.product_id WHERE l.voc_id = c.sid),
  t_links = (SELECT string_agg(p.code || ':' || l.role, ',' ORDER BY p.code) FROM public.voc_product_links l
             JOIN public.products p ON p.id = l.product_id WHERE l.voc_id = c.vid);
SQL
  else echo "ALTER TABLE _chk ADD COLUMN s_links text, ADD COLUMN t_links text;"; fi
  if has voc_defects; then cat <<SQL
ALTER TABLE _chk ADD COLUMN s_def text, ADD COLUMN t_def text;
CREATE INDEX ON _s_voc_defects (voc_id);
UPDATE _chk c SET
  s_def = (SELECT string_agg(d.component || '/' || d.symptom || '/' || d.severity, ',' ORDER BY d.component, d.symptom)
           FROM _s_voc_defects d WHERE d.voc_id = c.sid),
  t_def = (SELECT string_agg(d.component || '/' || d.symptom || '/' || d.severity, ',' ORDER BY d.component, d.symptom)
           FROM public.voc_defects d WHERE d.voc_id = c.vid);
SQL
  else echo "ALTER TABLE _chk ADD COLUMN s_def text, ADD COLUMN t_def text;"; fi
  cat <<'SQL'
DO $$
DECLARE r record;
BEGIN
  SELECT count(*) AS n,
         count(*) FILTER (WHERE vid IS NULL) AS missing,
         count(*) FILTER (WHERE vid IS NOT NULL AND t_prod IS DISTINCT FROM s_prod) AS prod_bad,
         count(*) FILTER (WHERE vid IS NOT NULL AND NOT same_body) AS body_bad,
         count(*) FILTER (WHERE vid IS NOT NULL AND t_links IS DISTINCT FROM s_links) AS link_bad,
         count(*) FILTER (WHERE vid IS NOT NULL AND t_def IS DISTINCT FROM s_def) AS def_bad
    INTO r FROM _chk;
  IF r.missing + r.prod_bad + r.body_bad + r.link_bad + r.def_bad > 0 THEN
    RAISE EXCEPTION 'merge 표본 대조 실패 — 표본 % 중 빠짐 % · 제품 어긋남 % · 본문 어긋남 % · 링크 어긋남 % · 결함 어긋남 %',
      r.n, r.missing, r.prod_bad, r.body_bad, r.link_bad, r.def_bad;
  END IF;
  RAISE NOTICE 'GUARD|%', r.n;
END $$;
SQL
  echo "SELECT 'R|guard|' || count(*) FROM _chk;"
  echo "COMMIT;"
} > "/tmp/sf-merge-${TARGET_DB}.sql"

echo "→ 병합(한 트랜잭션 — 카탈로그 code upsert · id 번역 · 링크·결함 · 표본 대조 $SAMPLE)"
: > "$LOG"
if ! OUT="$(PIN "$TARGET_DB" -q -tA < "/tmp/sf-merge-${TARGET_DB}.sql" 2>>"$LOG")"; then
  echo "    ✗ merge 실패 — 운영 DB 는 그대로다(트랜잭션 되돌림). $LOG:"
  grep -E "ERROR|표본" "$LOG" | tail -4 | sed 's/^/      /'
  exit 1
fi
rep() { awk -F'|' -v k="$1" '$1=="R" && $2==k {print $3 (NF>3 ? "|" $4 : "")}' <<<"$OUT"; }
pair() { local v; v="$(rep "$1")"; echo "+${v%%|*} ~${v#*|}"; }
echo "    ✓ 카탈로그 — platforms $(pair platforms) · products $(pair products)$(has voc_categories && echo " · voc_categories $(pair voc_categories)")"
r="$(rep repaired)"
echo "    ✓ voc_records $(pair voc_records) · 옛 정수 id 행 제자리로 ${r%%|*}"
has voc_product_links && echo "    ✓ voc_product_links $(pair voc_product_links) -$(rep voc_product_links_del)"
has voc_defects && echo "    ✓ voc_defects $(pair voc_defects) -$(rep voc_defects_del)"
echo "    ✓ 표본 대조 $(rep guard)건 — 제품·본문·링크·결함 일치"
[ "${r#*|}" != 0 ] && echo "    ⚠ 옮길 자리에 다른 행이 있어 못 옮긴 옛 행 ${r#*|} (같은 글이 두 벌일 수 있다 — $LOG 와 함께 보라)"
[ "$(rep raw_left)" != 0 ] && echo "    ⚠ 아직 원래 정수 id 자리에 있는 옛 행 $(rep raw_left)"
echo "    · dev 덤프에 없는 행 $(rep not_in_source) — 이 서버가 모았거나 병합 뒤 dev 가 지운 글(건드리지 않는다)"
[ "$(rep nokey)" != 0 ] && echo "    ⚠ 원천의 키 없는 행(platform_id·external_id NULL) $(rep nokey) — 자연키가 없어 옮기지 않았다"

# 7) 미리 계산하는 뷰 — 수집하지 않는 서버는 beat 가 없어 병합이 채운다. 비었거나 UNIQUE 인덱스가
#    표현식이면 CONCURRENTLY 가 안 되어 일반 REFRESH(짧은 잠금)로 한다.
echo "→ 미리 계산하는 뷰 다시 채우기"
while IFS='|' read -r mv populated; do
  [ -n "$mv" ] || continue
  if [ "$populated" = t ] && PIN "$TARGET_DB" -q -c "REFRESH MATERIALIZED VIEW CONCURRENTLY $mv;" </dev/null >/dev/null 2>&1; then
    echo "    ✓ $mv"
  elif PIN "$TARGET_DB" -q -v ON_ERROR_STOP=1 -c "REFRESH MATERIALIZED VIEW $mv;" </dev/null >>"$LOG" 2>&1; then
    echo "    ✓ $mv (일반 REFRESH)"
  else
    echo "    ⚠ $mv REFRESH 실패 — $LOG"
  fi
done < <(PIN "$TARGET_DB" -tA -F'|' -c "SELECT matviewname, ispopulated FROM pg_matviews WHERE schemaname='public' ORDER BY 1;")

echo "✓ SF merge 완료 — 운영 유지 + dev 반영(id 는 code 로 번역)"
