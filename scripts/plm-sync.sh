#!/usr/bin/env bash
# PLM 거울을 Redis·beat 없이 한 번 받는다 — .env 를 읽어 crawler 이미지로 base.plm_mirror.sync(SP 키트 받기)를 돈다
#
# beat 의 plm-sync-6h 는 Redis 가 있어야 돈다. Redis 를 두지 않는 서버(덤프로만 받는 cae00)는 이것을 cron 에
# 건다(SG-13). 워커 인스턴스가 없어도 된다 — 이미지와 /crawler 바인드만 쓴다. 수동 명령이 .env 를 안 읽어
# SF_SP_CORE_URL 이 비어 not_configured 로 끝나던 일도 없다.
#
#   bash scripts/plm-sync.sh                        # 받기(증분 — 마지막 처음부터 받기가 7일을 넘었으면 처음부터)
#   bash scripts/plm-sync.sh --full                 # 처음부터 다시 받기(끝까지 받은 뒤 안 온 것을 정리)
#   bash scripts/plm-sync.sh --full --allow-shrink  # 절반 넘게 안 와 정리를 멈춘 것을, 토큰 범위를 확인한 뒤에만
#   cron 예) 17 */6 * * *  bash /path/SignalForge/scripts/plm-sync.sh >> /path/SignalForge/logs/plm-sync.log 2>&1
#
# 출력 — 키트 받기의 줄(타입별 수신 건수 · 확인 필요) 뒤에 결과 JSON 한 줄. 종료 코드 — 정상(ok)·not_configured·
# busy 0, 확인 필요(check) 2, 멈춤(error) 1. 토큰은 파일(crawler/data/plm/sp_token 또는 SF_SP_TOKEN_FILE)에만 있고
# 여기서 찍지 않는다. 거울은 crawler/data/plm/sp-core/ — VOC DB·Drive 백업 밖이다.
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env          # .env → 환경(SF_SP_CORE_URL·SF_SP_TOKEN_FILE). apptainer 는 호스트 환경을 그대로 넘긴다
export_proxy      # 워커와 같은 프록시·예외(NO_PROXY) — up.sh 와 같다
require_apptainer

SIF="$APPT_DIR/sif/crawler.sif"
[[ -f "$SIF" ]] || { echo "[ERROR] crawler.sif 없음 — 먼저 ./scripts/build.sh crawler" >&2; exit 1; }
FULL=0
ALLOW_SHRINK=0
for a in "$@"; do
  case "$a" in
    --full) FULL=1 ;;
    --allow-shrink) ALLOW_SHRINK=1 ;;
    --allow-drop) FULL=1; ALLOW_SHRINK=1 ;;      # 10-23 판의 이름 — 그때는 늘 전체를 받았다(= --full --allow-shrink)
    *) echo "[ERROR] 모르는 인자: $a (쓸 수 있는 것: --full · --allow-shrink)" >&2; exit 1 ;;
  esac
done

rc=0
apptainer exec --bind "$CRAWLER_DIR:/crawler" --env SF_PLM_FULL="$FULL" --env SF_PLM_ALLOW_SHRINK="$ALLOW_SHRINK" \
  "$SIF" python3 - <<'PY' || rc=$?
import json, os, sys
sys.path.insert(0, "/crawler")
from base import plm_mirror as P

r = P.sync(full=os.environ.get("SF_PLM_FULL") == "1", allow_shrink=os.environ.get("SF_PLM_ALLOW_SHRINK") == "1")
for line in r.pop("lines", []):
    print(line)
print(json.dumps(r, ensure_ascii=False, default=str))
sys.exit({"ok": 0, "not_configured": 0, "busy": 0, "check": 2}.get(r.get("status"), 1))
PY
exit "$rc"
