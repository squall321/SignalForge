#!/usr/bin/env bash
# 덤프 보존정책(thin_dumps)이 정상 백업을 지우지 않는지 검증한다.
#
# 이 로직은 백업을 **삭제**한다. 틀리면 복구 수단이 사라지는데, 지금까지
# 테스트가 없었다. 규칙은 "최근 24시간 전량 → 이후 하루 1개 → 보존기간 초과 삭제".
#
# THIN_NOW 로 기준 시각을 고정해 날짜 의존 없이 검증한다.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_PREFIX="sf"
# 헬퍼만 쓰기 위해 _drive_common.sh 전체를 source 하지 않는다(.env·rclone 의존).
# 함수 본문을 그대로 떼어와 같은 코드를 검증한다.
eval "$(sed -n '/^thin_dumps() {/,/^}/p' "$HERE/../drive-sync/_drive_common.sh")"

NOW=20260925120000
pass=0; fail=0
_ok(){ printf '  ✓ %s\n' "$1"; pass=$((pass+1)); }
_no(){ printf '  ✗ %s\n     기대:%s\n     실제:%s\n' "$1" "$2" "$3"; fail=$((fail+1)); }
_run(){ printf '%s\n' "$@" | THIN_NOW="$NOW" thin_dumps "${DAYS:-5}"; }

# ── 1. 최근 24시간은 한 개도 지우지 않는다 ────────────────────────────────
out=$(_run sf-db-20260925-113001Z.sql.gz sf-db-20260925-103001Z.sql.gz \
           sf-db-20260924-130001Z.sql.gz)
[ -z "$out" ] && _ok "최근 24시간은 전량 보존" || _no "최근 24시간 보존" "(없음)" "$out"

# ── 2. 24시간 지난 같은 날은 최신 하나만 남는다 ───────────────────────────
out=$(_run sf-db-20260923-130001Z.sql.gz sf-db-20260923-070001Z.sql.gz \
           sf-db-20260923-010001Z.sql.gz)
exp=$'sf-db-20260923-070001Z.sql.gz\nsf-db-20260923-010001Z.sql.gz'
[ "$out" = "$exp" ] && _ok "하루 1개로 얇게 만든다(최신 보존)" || _no "일별 1개" "$exp" "$out"

# ── 3. 보존기간 초과는 전부 삭제 ──────────────────────────────────────────
out=$(_run sf-db-20260910-130001Z.sql.gz sf-db-20260901-130001Z.sql.gz)
exp=$'sf-db-20260910-130001Z.sql.gz\nsf-db-20260901-130001Z.sql.gz'
[ "$out" = "$exp" ] && _ok "보존기간(5일) 초과는 삭제" || _no "기간 초과 삭제" "$exp" "$out"

# ── 4. 이름 규칙이 다른 파일은 절대 건드리지 않는다 ───────────────────────
out=$(_run sf-db-safety-20260901-130001Z.sql.gz RESTORE-GUIDE-x.md other.tar.gz \
           sf-db-20260901-130001Z.sql.gz.sha256)
[ -z "$out" ] && _ok "규칙 밖 파일은 삭제 목록에 넣지 않는다" \
  || _no "규칙 밖 파일 보호" "(없음)" "$out"

# ── 5. 경계: 정확히 24시간 된 것은 보존한다 ───────────────────────────────
out=$(_run sf-db-20260924-120000Z.sql.gz)
[ -z "$out" ] && _ok "경계(정확히 24h)는 보존" || _no "24h 경계" "(없음)" "$out"

# ── 6. 하루치 전부를 지우는 일은 없어야 한다 ──────────────────────────────
# 그날 것을 전부 삭제 목록에 넣으면 그 날짜의 복구 지점이 사라진다.
DAYS=5 out=$(_run sf-db-20260922-230001Z.sql.gz sf-db-20260922-120001Z.sql.gz \
                  sf-db-20260922-010001Z.sql.gz)
n_in=3; n_del=$(printf '%s' "$out" | grep -c . || true)
[ "$n_del" -eq $((n_in - 1)) ] && _ok "하루치 중 정확히 1개는 남긴다 (${n_del}/${n_in} 삭제)" \
  || _no "하루 1개 보존" "$((n_in-1))개 삭제" "${n_del}개 삭제"

# ── 7. 빈 입력에 죽지 않는다 ──────────────────────────────────────────────
out=$(printf '' | THIN_NOW="$NOW" thin_dumps 5); rc=$?
[ "$rc" -eq 0 ] && [ -z "$out" ] && _ok "빈 입력 안전" || _no "빈 입력" "rc=0 출력없음" "rc=$rc '$out'"

printf '\n  통과 %d / 실패 %d\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
