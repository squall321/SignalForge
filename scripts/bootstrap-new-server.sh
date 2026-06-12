#!/usr/bin/env bash
# 새 서버 원샷 부트스트랩 — 자산 수신 → 기동 → DB 복원 → 재가동 → 검증 게이트.
#
# SERVER_SETUP.md 의 3~6 단계를 자동화한다. 선행 (수동 1회):
#   1. 선행 패키지: apptainer / redis-server(+requirepass) / rclone / python3.12+ / node20
#   2. bash scripts/drive-sync/setup-drive-sync.sh   (rclone 토큰)
#   3. .env 준비 (cp .env.example .env 후 비밀 채우기, 또는 원본 서버에서 scp)
#
# 사용:
#   bash scripts/bootstrap-new-server.sh             # 전체 (수신+기동+복원+검증)
#   bash scripts/bootstrap-new-server.sh --no-sync   # Drive 수신 생략 (이미 받았을 때)
#   bash scripts/bootstrap-new-server.sh --no-restore # DB 복원 생략 (빈 DB + alembic 만)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DO_SYNC=1; DO_RESTORE=1
for arg in "$@"; do
  case "$arg" in
    --no-sync) DO_SYNC=0 ;;
    --no-restore) DO_RESTORE=0 ;;
  esac
done

fail() { echo "[FAIL] $1" >&2; exit 1; }
step() { echo ""; echo "════ $1 ════"; }

# ── 0. 선행 검증 ─────────────────────────────────────────────
step "0. 선행 검증"
[[ -f .env ]] || fail ".env 없음 — cp .env.example .env 후 비밀 채우기 (SERVER_SETUP.md 4단계)"
command -v apptainer >/dev/null || fail "apptainer 미설치"
command -v rclone >/dev/null || fail "rclone 미설치"
set -a; source .env; set +a
[[ -n "${POSTGRES_PASSWORD:-}" ]] || fail ".env 의 POSTGRES_PASSWORD 비어있음"
[[ -n "${REDIS_PASSWORD:-}" ]] || fail ".env 의 REDIS_PASSWORD 비어있음"
redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping 2>/dev/null | grep -q PONG \
  || fail "Redis 응답 없음 — requirepass 가 .env 와 일치하는지 확인"
echo "  .env / apptainer / rclone / redis ✓"

# ── 1. Drive 자산 수신 ───────────────────────────────────────
if [[ $DO_SYNC -eq 1 ]]; then
  step "1. Drive 자산 수신 (SIF + DB dump)"
  bash scripts/sync-from-drive.sh
else
  step "1. Drive 수신 생략 (--no-sync)"
fi

# ── 2. 전체 기동 (postgres → backend → worker → beat) ────────
step "2. 서비스 기동 (up.sh)"
bash scripts/up.sh

# ── 3. DB 복원 (연결 정지 → restore → MV refresh) ───────────
if [[ $DO_RESTORE -eq 1 ]]; then
  step "3. DB 복원"
  LATEST_DUMP=$(ls -t backups/*-db-*.sql.gz 2>/dev/null | grep -v safety | head -1)
  [[ -n "$LATEST_DUMP" ]] || fail "backups/ 에 dump 없음 — sync-from-drive.sh 먼저"
  echo "  대상: $LATEST_DUMP"

  echo "  → DB 연결 프로세스 일시 정지 (DROP 가능하게)"
  pkill -f "celery -A celery_app" 2>/dev/null || true
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  sleep 5

  bash scripts/drive-sync/restore-db.sh "$LATEST_DUMP" --yes

  echo "  → MV refresh"
  PGPASSWORD="$POSTGRES_PASSWORD" psql -h "${POSTGRES_HOST:-127.0.0.1}" -p "${POSTGRES_PORT:-5434}" \
    -U "$POSTGRES_USER" "$POSTGRES_DB" -c "REFRESH MATERIALIZED VIEW kpi_overview;" 2>/dev/null \
    || echo "  (kpi_overview refresh 실패 — backend 기동 후 10분 주기 task 가 처리)"

  step "4. 서비스 재가동"
  cd backend
  setsid nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${API_PORT:-18000}" \
    > ../logs/backend_"${API_PORT:-18000}".log 2>&1 < /dev/null &
  echo $! > ../logs/backend.pid
  cd ../crawler
  setsid nohup ../.venv/bin/celery -A celery_app worker --loglevel=info --concurrency=6 \
    > ../logs/celery-worker.log 2>&1 < /dev/null &
  echo $! > ../logs/celery-worker.pid
  rm -f celerybeat-schedule*
  setsid nohup ../.venv/bin/celery -A celery_app beat --loglevel=info \
    > ../logs/celery-beat.log 2>&1 < /dev/null &
  echo $! > ../logs/celery-beat.pid
  cd "$ROOT"
  sleep 15
else
  step "3-4. DB 복원 생략 (--no-restore, alembic 빈 스키마 상태)"
fi

# ── 5. 검증 게이트 (r6 정착 기준 — 전부 자동) ────────────────
step "5. 검증 게이트"
PASS=0; TOTAL=5
API="http://127.0.0.1:${API_PORT:-18000}"

# G1: worker Redis URL (r6 P0 재발 방지)
if grep -q ':\*\*@' logs/celery-worker.log 2>/dev/null; then
  echo "  G1 worker redis banner ':**@' ✓"; PASS=$((PASS+1))
else
  echo "  G1 worker redis banner ✗ — REDIS_URL env 누락 의심 (logs/celery-worker.log 확인)"
fi

# G2: backend health
for i in $(seq 1 15); do
  curl -sf -o /dev/null --max-time 3 "$API/health" && break; sleep 2
done
if curl -sf -o /dev/null "$API/health"; then
  echo "  G2 backend /health 200 ✓"; PASS=$((PASS+1))
else echo "  G2 backend /health ✗"; fi

# G3: regression 14 checks
CHECKS=$(curl -sf "$API/api/v1/_internal/regression-baseline" 2>/dev/null \
  | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('checks',[])))" 2>/dev/null || echo 0)
if [[ "$CHECKS" -ge 14 ]]; then
  echo "  G3 regression $CHECKS checks ✓"; PASS=$((PASS+1))
else echo "  G3 regression checks=$CHECKS ✗"; fi

# G4: data-quality (mx_match)
MX=$(curl -sf "$API/api/v1/_internal/data-quality" 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('mx_match_pct',0))" 2>/dev/null || echo 0)
if python3 -c "exit(0 if float('$MX' or 0) >= 70 else 1)" 2>/dev/null; then
  echo "  G4 data-quality mx_match=${MX}% ✓"; PASS=$((PASS+1))
else echo "  G4 data-quality mx_match=${MX}% ✗ (복원 직후면 정상 — voc 데이터 확인)"; fi

# G5: collection-stats
if curl -sf -o /dev/null "$API/api/v1/_internal/collection-stats"; then
  echo "  G5 collection-stats 200 ✓"; PASS=$((PASS+1))
else echo "  G5 collection-stats ✗"; fi

echo ""
echo "════════════════════════════════════════"
if [[ $PASS -eq $TOTAL ]]; then
  echo " ✅ 부트스트랩 완료 — 게이트 $PASS/$TOTAL"
  echo "    Frontend dev: cd frontend && npm install && npm run dev  (:17370)"
  echo "    추종 모드:    crontab 에 */5 auto-pull.sh (SERVER_SETUP.md 7단계)"
else
  echo " ⚠️  게이트 $PASS/$TOTAL — 실패 항목 위 로그 확인"
  exit 1
fi
