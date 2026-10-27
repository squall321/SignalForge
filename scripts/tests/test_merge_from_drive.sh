#!/usr/bin/env bash
# merge-from-drive 가 id 를 code 로 번역하고(카탈로그 순서가 다른 두 DB) 옛 정수 복사 행을 제자리로 옮기는지 고정한다
#   임시 DB 둘(원천·대상)을 운영 인스턴스 안에 만들고 지운다 — 운영 DB 는 건드리지 않는다.
#   T1 카탈로그 code upsert(빠진 플랫폼·제품을 넣는다, 대상에만 있는 행은 그대로)
#   T2 옛 정수 복사 행 → 제자리 플랫폼·제품(맞바꿈 자리 포함), 이 서버가 모은 행은 그대로
#   T3 링크·결함이 code 로 번역돼 온다 · 미리 계산하는 뷰가 채워진다
#   T4 두 번째 실행은 아무것도 바꾸지 않는다(멱등)
#   T5 dev 의 변경(링크 떼기·본문 갱신)이 따라온다
#   T6 표본 대조가 어긋나면 통째로 되돌린다(대상에 일부러 product_id 를 지우는 트리거)
#   T7 다른 병합이 돌고 있으면 건너뛴다
set -uo pipefail
PASS=0; FAIL=0
TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
MERGE="$SCRIPTS_DIR/drive-sync/merge-from-drive.sh"
pass() { echo "  [PASS] $*"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $*" >&2; FAIL=$((FAIL+1)); }

# shellcheck source=../drive-sync/_drive_common.sh
source "$SCRIPTS_DIR/drive-sync/_drive_common.sh"
INST="$PROJ_PG_INSTANCE"
if ! apptainer instance list 2>/dev/null | awk '{print $1}' | grep -qx "$INST"; then
  echo "· $INST 인스턴스가 없어 건너뛴다(통합 시험)"; exit 0
fi
PIN() { local db="$1"; shift; PGPASSWORD="$POSTGRES_PASSWORD" apptainer exec "instance://$INST" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$db" "$@" 2>/dev/null; }
SRC="sf_mgt_src_$$"; DST="sf_mgt_dst_$$"
TMP="$(mktemp -d)"
drop_all() {
  local d; for d in "$SRC" "$DST" "${DST}_mergestage"; do
    PIN postgres -q -c "DROP DATABASE IF EXISTS \"$d\" WITH (FORCE);" >/dev/null
  done
  rm -rf "$TMP"
}
trap drop_all EXIT

echo "================================================================"
echo " merge-from-drive — id 를 code 로 번역"
echo "================================================================"
for d in "$SRC" "$DST"; do PIN postgres -q -c "CREATE DATABASE \"$d\" OWNER \"$POSTGRES_USER\";" >/dev/null; done
# 스키마는 운영 그대로(열·제약이 실제와 같아야 시험이 뜻이 있다)
PGPASSWORD="$POSTGRES_PASSWORD" apptainer exec "instance://$INST" pg_dump -h 127.0.0.1 -p "$POSTGRES_PORT" \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" -s --no-owner --no-privileges \
  -t platforms -t products -t voc_categories -t voc_records -t voc_product_links -t voc_defects \
  > "$TMP/schema.sql" 2>/dev/null
[ -s "$TMP/schema.sql" ] || { fail "운영 스키마를 못 떴다"; exit 1; }
for d in "$SRC" "$DST"; do
  PIN "$d" -q -o /dev/null < "$TMP/schema.sql"
  PIN "$d" -q -c "CREATE MATERIALIZED VIEW t_platform_counts AS SELECT platform_id, count(*) AS n FROM voc_records GROUP BY 1 WITH NO DATA;
                  CREATE UNIQUE INDEX ON t_platform_counts (platform_id);" >/dev/null
done

# 원천(dev) — 카탈로그 순서 dcinside 1 · naver_cafe 2 · samsung_community 3 / GS25U 1 · GZF8 2 · GS26 3
PIN "$SRC" -q -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
INSERT INTO platforms (id, code, name) VALUES (1,'dcinside','DCInside'),(2,'naver_cafe','Naver Cafe'),(3,'samsung_community','Samsung Members');
INSERT INTO products (id, code, series_code, name_en, name_ko, category, released_at) VALUES
  (1,'GS25U','GS','Galaxy S25 Ultra','갤럭시 S25 울트라','phone','2025-01-22'),
  (2,'GZF8','GZ','Galaxy Z Fold8','갤럭시 Z 폴드8','phone','2026-07-25'),
  (3,'GS26','GS','Galaxy S26','갤럭시 S26','phone','2026-01-22');
INSERT INTO voc_categories (code, name_ko) VALUES ('battery','배터리');
INSERT INTO voc_records (id, platform_id, external_id, source_url, product_id, content_original, content_hash) VALUES
  (11, 1, 'd1', 'https://gall.dcinside.com/d1', 1, 'S25 울트라 발열', 'h-d1'),
  (12, 2, 'n1', 'https://cafe.naver.com/n1',    2, '폴드8 힌지 먼지 — S25U 보다', 'h-n1'),
  (13, 3, 'c1', 'https://r1.community.samsung.com/c1', 3, 'S26 배터리', 'h-c1'),
  (14, 1, 'd2', 'https://gall.dcinside.com/d2', NULL, '그냥 잡담', 'h-d2'),
  (15, 2, 'n2', 'https://cafe.naver.com/n2',    1, '새 글', 'h-n2'),
  (16, 1, 'same1', 'https://gall.dcinside.com/same1', 1, 'dc 의 same1', 'h-sd'),
  (17, 2, 'same1', 'https://cafe.naver.com/same1',    2, '카페의 same1', 'h-sn');
INSERT INTO voc_product_links (voc_id, product_id, role) VALUES (11,1,'primary'),(12,2,'primary'),(12,1,'compared'),(13,3,'primary');
INSERT INTO voc_defects (voc_id, component, symptom, severity) VALUES (12,'hinge','dust_ingress','degraded');
SELECT setval('platforms_id_seq', 3); SELECT setval('products_id_seq', 3); SELECT setval('voc_records_id_seq', 17);
SQL

# 대상(cae00) — 설치 순서가 달라 naver_cafe 1 · dcinside 2 · wpnews 3 / OLD12 1 · GS25U 2 · GZF7 3,
# 그 위에 예전 병합이 정수 그대로 넣은 행(원천 id 를 이 카탈로그로 읽으면 엉뚱한 곳)과 이 서버가 모은 행 하나
PIN "$DST" -q -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
INSERT INTO platforms (id, code, name) VALUES (1,'naver_cafe','Naver Cafe'),(2,'dcinside','DCInside'),(3,'wpnews','WP News');
INSERT INTO products (id, code, series_code, name_en, category, released_at) VALUES
  (1,'OLD12','GS','Galaxy S3','phone','2012-05-29'),(2,'GS25U','GS','Galaxy S25 Ultra','phone','2025-01-22'),
  (3,'GZF7','GZ','Galaxy Z Fold7','phone','2025-07-25');
SELECT setval('platforms_id_seq', 3); SELECT setval('products_id_seq', 3);
INSERT INTO voc_records (platform_id, external_id, source_url, product_id, content_original, content_hash) VALUES
  (1, 'd1', 'https://gall.dcinside.com/d1', 1, 'S25 울트라 발열', 'h-d1'),
  (2, 'n1', 'https://cafe.naver.com/n1',    2, '폴드8 힌지 먼지 — S25U 보다', 'h-n1'),
  (3, 'c1', 'https://r1.community.samsung.com/c1', 3, 'S26 배터리', 'h-c1'),
  (1, 'd2', 'https://gall.dcinside.com/d2', NULL, '그냥 잡담', 'h-d2'),
  (1, 'same1', 'https://gall.dcinside.com/same1', 1, 'dc 의 same1', 'h-sd'),
  (2, 'same1', 'https://cafe.naver.com/same1',    2, '카페의 same1', 'h-sn'),
  (1, 'native1', 'https://cafe.naver.com/native1', 2, '이 서버가 모은 글', 'h-native');
SQL

attribution() {  # external_id|플랫폼 code|제품 code — 대상 DB 를 code 로 읽는다
  PIN "$DST" -tA -c "SELECT v.external_id || '|' || pl.code || '|' || COALESCE(pr.code,'-') || '|' || v.content_original
                     FROM voc_records v JOIN platforms pl ON pl.id = v.platform_id LEFT JOIN products pr ON pr.id = v.product_id
                     ORDER BY v.external_id, pl.code"
}
links() {
  PIN "$DST" -tA -c "SELECT v.external_id || '|' || string_agg(p.code || ':' || l.role, ',' ORDER BY p.code)
                     FROM voc_product_links l JOIN voc_records v ON v.id = l.voc_id JOIN products p ON p.id = l.product_id
                     GROUP BY v.external_id ORDER BY 1"
}
run_merge() {  # 원천을 덤프해 대상에 병합
  PGPASSWORD="$POSTGRES_PASSWORD" apptainer exec "instance://$INST" pg_dump -h 127.0.0.1 -p "$POSTGRES_PORT" \
    -U "$POSTGRES_USER" -d "$SRC" --no-owner --no-privileges --clean --if-exists 2>/dev/null | gzip -c > "$TMP/src.sql.gz"
  SF_MERGE_DUMP="$TMP/src.sql.gz" SF_MERGE_TARGET="$DST" bash "$MERGE" > "$TMP/merge.out" 2>&1
}

echo "[T1·T2·T3] 첫 병합"
if run_merge; then pass "병합 성공"; else fail "병합 실패: $(tail -6 "$TMP/merge.out")"; fi
want="c1|samsung_community|GS26|S26 배터리
d1|dcinside|GS25U|S25 울트라 발열
d2|dcinside|-|그냥 잡담
n1|naver_cafe|GZF8|폴드8 힌지 먼지 — S25U 보다
n2|naver_cafe|GS25U|새 글
native1|naver_cafe|GZF7|이 서버가 모은 글
same1|dcinside|GS25U|dc 의 same1
same1|naver_cafe|GZF8|카페의 same1"
got="$(attribution)"
# native1 은 이 서버가 모은 행이라 원래 값(product_id 2 = 이 서버의 GS25U)을 그대로 둔다
want="${want/native1|naver_cafe|GZF7|/native1|naver_cafe|GS25U|}"
if [ "$got" = "$want" ]; then pass "모든 글이 code 로 제자리(맞바꿈 same1 포함), 이 서버의 글은 그대로"
else fail "귀속이 다르다:"$'\n'"$got"; fi
cat_ok="$(PIN "$DST" -tA -c "SELECT string_agg(code, ',' ORDER BY code) FROM platforms")|$(PIN "$DST" -tA -c "SELECT string_agg(code, ',' ORDER BY code) FROM products")"
[ "$cat_ok" = "dcinside,naver_cafe,samsung_community,wpnews|GS25U,GS26,GZF7,GZF8,OLD12" ] \
  && pass "카탈로그 — 빠진 플랫폼(공식 커뮤니티 앱)·제품을 넣고, 대상에만 있는 행은 남긴다" || fail "카탈로그: $cat_ok"
lk="$(links)"
[ "$lk" = $'c1|GS26:primary\nd1|GS25U:primary\nn1|GS25U:compared,GZF8:primary' ] \
  && pass "링크가 code 로 번역돼 온다" || fail "링크: $lk"
df="$(PIN "$DST" -tA -c "SELECT v.external_id || '|' || d.component || '/' || d.symptom FROM voc_defects d JOIN voc_records v ON v.id = d.voc_id")"
[ "$df" = "n1|hinge/dust_ingress" ] && pass "결함이 온다" || fail "결함: $df"
[ "$(PIN "$DST" -tA -c "SELECT ispopulated FROM pg_matviews WHERE matviewname='t_platform_counts'")" = t ] \
  && pass "미리 계산하는 뷰가 채워진다" || fail "뷰가 비었다"
sed 's/^/      │ /' "$TMP/merge.out" | grep -E "✓|⚠|·" | head -14
grep -q "제자리로 6" "$TMP/merge.out" && pass "옛 정수 복사 행 6개를 옮겼다고 보고" || fail "보고: $(grep -E '제자리|voc_records' "$TMP/merge.out")"

echo "[T4] 두 번째 실행 — 멱등"
before="$(attribution)|$(links)"
if run_merge && [ "$(attribution)|$(links)" = "$before" ] && grep -q "voc_records +0 ~0 · 옛 정수 id 행 제자리로 0" "$TMP/merge.out"; then
  pass "바뀌는 것이 없다"
else fail "두 번째 실행: $(grep -E 'voc_records|✗' "$TMP/merge.out")"; fi

echo "[T5] dev 의 변경이 따라온다"
PIN "$SRC" -q -c "DELETE FROM voc_product_links WHERE voc_id = 12 AND product_id = 1;
                  UPDATE voc_records SET content_original = 'S25 울트라 발열 — 고침' WHERE id = 11;" >/dev/null
run_merge
lk="$(links)"
if [ "$lk" = $'c1|GS26:primary\nd1|GS25U:primary\nn1|GZF8:primary' ] && attribution | grep -q "^d1|dcinside|GS25U|S25 울트라 발열 — 고침$"; then
  pass "dev 가 뗀 링크는 떼고 고친 본문은 고친다"
else fail "변경 반영: $lk / $(attribution | grep '^d1')"; fi

echo "[T6] 표본 대조가 어긋나면 되돌린다"
PIN "$DST" -q -c "CREATE FUNCTION _t_null_prod() RETURNS trigger LANGUAGE plpgsql AS \$\$ BEGIN NEW.product_id := NULL; RETURN NEW; END \$\$;
                  CREATE TRIGGER _t_np BEFORE UPDATE ON voc_records FOR EACH ROW EXECUTE FUNCTION _t_null_prod();" >/dev/null
PIN "$SRC" -q -c "UPDATE voc_records SET product_id = 2 WHERE id = 15;" >/dev/null
before="$(attribution)"
if SF_MERGE_SAMPLE=100 run_merge; then fail "어긋났는데 성공으로 끝났다"
elif grep -q "표본 대조 실패" "$TMP/merge.out" && [ "$(attribution)" = "$before" ]; then
  pass "실패로 끝나고 대상은 그대로(트랜잭션 되돌림)"
else fail "되돌림: $(tail -4 "$TMP/merge.out")"; fi
PIN "$DST" -q -c "DROP TRIGGER _t_np ON voc_records; DROP FUNCTION _t_null_prod();" >/dev/null

echo "[T7] 다른 병합이 돌고 있으면 건너뛴다(겹치지 않는다)"
( exec 8>"/tmp/sf-merge-${DST}.lock"; flock -n 8 && sleep 30 ) & HOLD=$!
sleep 1
if SF_MERGE_DUMP="$TMP/src.sql.gz" SF_MERGE_TARGET="$DST" timeout 20 bash "$MERGE" > "$TMP/merge.out" 2>&1 \
   && grep -q "건너뛴다" "$TMP/merge.out" && ! grep -q "staging" "$TMP/merge.out"; then
  pass "잠금을 못 잡으면 staging 도 안 만들고 0 으로 끝난다"
else fail "겹침: $(tail -3 "$TMP/merge.out")"; fi
kill "$HOLD" 2>/dev/null; wait "$HOLD" 2>/dev/null

echo "================================================================"
echo " 결과: PASS=$PASS FAIL=$FAIL"
echo "================================================================"
[ "$FAIL" -eq 0 ]
