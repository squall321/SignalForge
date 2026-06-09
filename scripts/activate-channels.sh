#!/usr/bin/env bash
# SignalForge — Reddit / Bluesky 채널 활성화 자동 스크립트
#
# 동작:
#   1) .env 의 REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET 검사
#       → 둘 다 채워져 있으면 platforms.is_active=true (reddit) + 첫 수집 1회.
#       → 비어 있으면 가이드 출력 (docs/dashboard/REDDIT_OAUTH_GUIDE.md).
#   2) .env 의 BLUESKY_HANDLE/BLUESKY_PASSWORD 검사
#       → 둘 다 채워져 있으면 platforms.is_active=true (bluesky) + 첫 수집 1회.
#       → 비어 있으면 가이드 출력 (docs/dashboard/BLUESKY_GUIDE.md).
#
# 키가 둘 다 없어도 안전하게 종료 (exit 0). 사용자에게 다음 단계만 안내한다.
# 첫 수집 호출은 crawler 내부의 BaseCrawler.crawl() 만 실행하며 DB 적재는
# 별도 파이프라인(celery 등)에서 처리한다 — 이 스크립트는 *연결 가능성*만 검증.
#
# 실행:
#   bash /home/koopark/claude/SignalForge/scripts/activate-channels.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

# ── 색상 (TTY 일 때만) ─────────────────────────────────────────────
if [[ -t 1 ]]; then
  C_INFO='\033[1;36m'; C_OK='\033[1;32m'; C_WARN='\033[1;33m'; C_ERR='\033[1;31m'; C_OFF='\033[0m'
else
  C_INFO=''; C_OK=''; C_WARN=''; C_ERR=''; C_OFF=''
fi

log_info()  { printf "${C_INFO}[INFO]${C_OFF} %s\n" "$*"; }
log_ok()    { printf "${C_OK}[OK]${C_OFF} %s\n"   "$*"; }
log_warn()  { printf "${C_WARN}[WARN]${C_OFF} %s\n" "$*"; }
log_err()   { printf "${C_ERR}[ERR]${C_OFF} %s\n"  "$*" >&2; }

# ── .env 로드 ────────────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  log_err ".env 없음: $ENV_FILE"
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
POSTGRES_PORT="${POSTGRES_PORT:-5434}"
POSTGRES_USER="${POSTGRES_USER:-signalforge}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-signalforge_pass}"
POSTGRES_DB="${POSTGRES_DB:-signalforge}"

# ── DB UPDATE 유틸 ──────────────────────────────────────────────────
db_activate() {
  local code="$1"
  if ! command -v psql >/dev/null 2>&1; then
    log_warn "psql 미설치 — DB 활성화 skip (수동: UPDATE platforms SET is_active=true WHERE code='$code';)"
    return 1
  fi
  PGPASSWORD="$POSTGRES_PASSWORD" psql \
    -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -v ON_ERROR_STOP=1 \
    -c "UPDATE platforms SET is_active = true WHERE code = '$code';" \
    >/dev/null 2>&1
}

# ── 크롤러 첫 호출 (검증용) ─────────────────────────────────────────
probe_crawler() {
  local platform="$1"
  # crawler venv 우선 → backend venv → 시스템 python 순.
  local py=""
  for cand in \
    "$PROJECT_ROOT/crawler/.venv/bin/python" \
    "$PROJECT_ROOT/backend/.venv/bin/python" \
    "$(command -v python3.12 2>/dev/null || true)" \
    "$(command -v python3 2>/dev/null || true)"
  do
    if [[ -n "$cand" && -x "$cand" ]]; then py="$cand"; break; fi
  done
  if [[ -z "$py" ]]; then
    log_warn "python 미발견 — $platform 첫 호출 skip"
    return 1
  fi

  ( cd "$PROJECT_ROOT/crawler" && "$py" -m "scripts.test_${platform}" ) || {
    log_warn "$platform 첫 호출 실패 (rc=$?) — 로그 확인 후 키/네트워크 점검"
    return 1
  }
}

# ── Reddit 가이드 출력 ──────────────────────────────────────────────
print_reddit_guide() {
  cat <<'EOF'
[Reddit] REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET 가 비어 있어 활성화 skip.

다음 단계로 5분 내 키 발급 가능:
  1. https://www.reddit.com/prefs/apps 접속 → "create app..." → script 타입.
  2. Developer Platform 폼 통과 (2024 이후 필수):
       - "Will your app train AI models?" → No
       - "Will your app post on behalf of users?" → No
       - OAuth scopes → read 만 선택.
  3. 발급된 14자 client_id / 27자 client_secret 을 .env 에 입력:
       REDDIT_CLIENT_ID=...
       REDDIT_CLIENT_SECRET=...
       REDDIT_USER_AGENT="SignalForge/1.0 by /u/<your-username>"
  4. 본 스크립트 재실행: bash scripts/activate-channels.sh

상세 가이드: docs/dashboard/REDDIT_OAUTH_GUIDE.md
EOF
}

# ── Bluesky 가이드 출력 ─────────────────────────────────────────────
print_bluesky_guide() {
  cat <<'EOF'
[Bluesky] BLUESKY_HANDLE / BLUESKY_PASSWORD 가 비어 있어 활성화 skip.

다음 단계로 5분 내 키 발급 가능:
  1. https://bsky.app 가입 (이메일 인증 + 핸들 선택, 예: signalforge.bsky.social).
  2. Settings → Privacy and security → App Passwords → "Add app password".
       이름: signalforge-collector → 발급된 19자 패스워드 즉시 복사 (재확인 불가).
  3. .env 에 입력:
       BLUESKY_HANDLE=signalforge.bsky.social
       BLUESKY_PASSWORD=xxxx-xxxx-xxxx-xxxx
  4. 본 스크립트 재실행: bash scripts/activate-channels.sh

상세 가이드: docs/dashboard/BLUESKY_GUIDE.md
EOF
}

# ── 메인 ────────────────────────────────────────────────────────────
log_info "SignalForge channel activator — .env=$ENV_FILE"

# Reddit
reddit_id="${REDDIT_CLIENT_ID:-}"
reddit_secret="${REDDIT_CLIENT_SECRET:-}"
if [[ -n "$reddit_id" && -n "$reddit_secret" ]]; then
  log_info "Reddit 키 감지 — 활성화 시도"
  if db_activate "reddit"; then
    log_ok "platforms.is_active = true (code=reddit)"
  else
    log_warn "Reddit DB 활성화 실패 또는 skip"
  fi
  log_info "Reddit 첫 호출 (OAuth → listing fetch) 실행 중..."
  probe_crawler "reddit" || log_warn "Reddit 첫 호출 실패 — 키 재확인 / Developer Platform 승인 상태 확인"
else
  print_reddit_guide
fi

echo

# Bluesky
bsky_handle="${BLUESKY_HANDLE:-}"
bsky_password="${BLUESKY_PASSWORD:-}"
if [[ -n "$bsky_handle" && -n "$bsky_password" ]]; then
  log_info "Bluesky 키 감지 — 활성화 시도"
  if db_activate "bluesky"; then
    log_ok "platforms.is_active = true (code=bluesky)"
  else
    log_warn "Bluesky DB 활성화 실패 또는 skip"
  fi
  log_info "Bluesky 첫 호출 (createSession → searchPosts) 실행 중..."
  probe_crawler "bluesky" || log_warn "Bluesky 첫 호출 실패 — handle/app-password 재확인"
else
  print_bluesky_guide
fi

echo
log_info "완료. 다음 단계: celery beat/worker 가 실행 중이면 다음 스케줄에서 자동 수집됩니다."
exit 0
