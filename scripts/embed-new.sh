#!/usr/bin/env bash
# 새 VOC 본문을 bge-m3 로 임베딩해 벡터 저장소에 덧붙인다 — Redis·beat 없는 서버(cae00)가 cron 으로 beat embed-new-2h 를 대신한다
#
# beat 의 embed-new-2h(2시간마다 600초)는 Redis·워커가 있어야 돈다. 덤프로만 받는 cae00 은 이것을 cron 에 건다
# (2026-11-11 결정 — 벡터는 그 서버의 VOC id 로 그 서버에서 짓는다. dev 벡터는 id 가 달라 옮기지 않는다).
# manifest 의 커서를 이어받아 예산만큼 하고 멈춘다 — 다음 실행이 이어 간다. 저장소 잠금이 있어 겹치면 뒤 것이 건너뛴다.
#
#   bash scripts/embed-new.sh             # 600초 예산(기본) — cron
#   bash scripts/embed-new.sh 0           # 예산 없이 끝까지 — 처음 채우기
#   bash scripts/embed-new.sh --status    # 읽기만 — 벡터 수 · 커서 · 남은 행(0 이면 다 따라잡았다) · 맞춰 보기
#                                         #   (alignment — 저장소가 이 DB 의 id 로 지은 것인가. differ · missing 이 있으면 다시 채운다)
#   cron 예) 37 */2 * * *  cd ~/Projects/SignalForge && bash scripts/embed-new.sh >> logs/embed-new.log 2>&1
#
# ollama 와 bge-m3 가 이 서버에 있어야 한다 — EMB_URL(기본 http://127.0.0.1:11434). 끝나는 코드 — 정상·건너뜀 0,
# 임베딩 실패(그 앞까지만 썼다) 1. 저장소는 crawler/data/vectors/bge-m3/ — MCP 는 manifest 가 바뀌면 다시 읽는다.
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env          # .env → POSTGRES_* · EMB_*. apptainer 는 호스트 환경을 그대로 넘긴다
export_proxy      # NO_PROXY 에 127.0.0.1 — ollama 는 프록시를 타지 않는다
require_apptainer

SIF="$APPT_DIR/sif/crawler.sif"
[[ -f "$SIF" ]] || { echo "[ERROR] crawler.sif 없음 — 먼저 ./scripts/build.sh crawler" >&2; exit 1; }
ARG="${1:-600}"
[[ "$ARG" == "--status" || "$ARG" =~ ^[0-9]+$ ]] || { echo "[ERROR] 인자는 예산(초) 또는 --status: $ARG" >&2; exit 1; }
# up.sh 와 같은 주소. --env 로 넘기지 않는다 — 명령줄(ps)에 비밀번호가 보인다
export DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}/${POSTGRES_DB}"

if [[ "$ARG" == "--status" ]]; then
  exec apptainer exec --bind "$CRAWLER_DIR:/crawler" "$SIF" sh -c "cd /crawler && python3 scripts/build_embeddings.py --status"
fi

echo "[$(date '+%F %T')] embed-new 예산 ${ARG}s"
apptainer exec --bind "$CRAWLER_DIR:/crawler" --env EMB_BUDGET_SEC="$ARG" \
  "$SIF" sh -c "cd /crawler && python3 scripts/build_embeddings.py"
