#!/usr/bin/env bash
# PLM 거울을 Redis·beat 없이 한 번 받는다 — .env 를 읽어 crawler 이미지로 base.plm_mirror.sync 를 돈다
#
# beat 의 plm-sync-6h 는 Redis 가 있어야 돈다. Redis 를 두지 않는 서버(덤프로만 받는 cae00)는 이것을 cron 에
# 건다(SG-13). 워커 인스턴스가 없어도 된다 — 이미지와 /crawler 바인드만 쓴다. 수동 명령이 .env 를 안 읽어
# SF_SP_CORE_URL 이 비어 not_configured 로 끝나던 일도 없다.
#
#   bash scripts/plm-sync.sh                # 받기 — 결과 JSON 한 줄
#   bash scripts/plm-sync.sh --allow-drop   # 한 타입이 절반 아래로 줄어 held 된 것을 그대로 받아들인다
#   cron 예) 17 */6 * * *  bash /path/SignalForge/scripts/plm-sync.sh >> /path/SignalForge/logs/plm-sync.log 2>&1
#
# 종료 코드 — ok · not_configured 0, held 2, 그 밖(error) 1. 토큰은 파일(crawler/data/plm/sp_token 또는
# SF_SP_TOKEN_FILE)에만 있고 여기서 찍지 않는다. 거울은 crawler/data/plm/ — VOC DB·Drive 백업 밖이다.
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env          # .env → 환경(SF_SP_CORE_URL·SF_SP_TOKEN_FILE). apptainer 는 호스트 환경을 그대로 넘긴다
export_proxy      # 워커와 같은 프록시·예외(NO_PROXY) — up.sh 와 같다
require_apptainer

SIF="$APPT_DIR/sif/crawler.sif"
[[ -f "$SIF" ]] || { echo "[ERROR] crawler.sif 없음 — 먼저 ./scripts/build.sh crawler" >&2; exit 1; }
ALLOW_DROP=0
for a in "$@"; do
  case "$a" in
    --allow-drop) ALLOW_DROP=1 ;;
    *) echo "[ERROR] 모르는 인자: $a (쓸 수 있는 것: --allow-drop)" >&2; exit 1 ;;
  esac
done

rc=0
apptainer exec --bind "$CRAWLER_DIR:/crawler" --env SF_PLM_ALLOW_DROP="$ALLOW_DROP" "$SIF" python3 - <<'PY' || rc=$?
import json, os, sys
sys.path.insert(0, "/crawler")
from base import plm_mirror as P

r = P.sync(allow_drop=os.environ.get("SF_PLM_ALLOW_DROP") == "1")
print(json.dumps(r, ensure_ascii=False, default=str))
sys.exit({"ok": 0, "not_configured": 0, "held": 2}.get(r.get("status"), 1))
PY
exit "$rc"
