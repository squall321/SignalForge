#!/usr/bin/env bash
# 연결 점검 — 수신 코드를 붙이기 전에 세 가지를 확인합니다.
set -u
# 주소 · 토큰은 환경변수가 먼저, 없으면 config.ini 에서 읽는다.
ini() { [ -f config.ini ] && sed -n "s/^$1[[:space:]]*=[[:space:]]*//p" config.ini | head -1; }
BASE="${SP_BASE:-$(ini base_url)}"
BASE="${BASE:-{BASE}}"
TOKEN="${SP_TOKEN:-$(ini token)}"
case "$BASE" in
  http://*|https://*) ;;
  *) echo "주소가 없습니다 — config.ini 의 base_url(끝이 /api) 또는 SP_BASE 를 지정하세요."; exit 1;;
esac
case "$BASE" in *"<"*) echo "config.ini 의 base_url 에 공개 측이 알려 준 주소를 적으세요: $BASE"; exit 1;; esac
[ -n "$TOKEN" ] || { echo "토큰이 없습니다 — config.ini 의 token 또는 SP_TOKEN 을 지정하세요."; exit 1; }

echo "1. 인증 없이 호출하면 거부되는가 (401 이 정상)"
curl -s -o /dev/null -w "   HTTP %{http_code}\n" "$BASE/core"

echo "2. 카탈로그를 읽을 수 있는가"
CATALOG=$(curl -s "$BASE/core" -H "Authorization: Bearer $TOKEN")
FIRST=$(printf '%s' "$CATALOG" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit('   읽지 못했습니다 — 주소 · 방화벽 · 프록시를 확인하세요')
if 'types' not in d:
    sys.exit('   거절되었습니다: ' + str((d.get('error') or {}).get('message') or d)[:200])
print('   시스템', d['system'], '· 공개 타입', [t['slug'] for t in d['types']], file=sys.stderr)
print(d['types'][0]['slug'] if d['types'] else '')
") || exit 1
[ -n "$FIRST" ] || { echo "   공개된 타입이 없습니다 — 공개 측 담당자에게 확인하세요."; exit 1; }

echo "3. 첫 페이지를 받을 수 있는가"
curl -s "$BASE/core/$FIRST?limit=2" -H "Authorization: Bearer $TOKEN" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit('   읽지 못했습니다')
if 'items' not in d:
    sys.exit('   거절되었습니다: ' + str((d.get('error') or {}).get('message') or d)[:200])
print('  ', d['type_slug'], '· 행', len(d['items']), '· 다음 페이지', bool(d['next']))
" || exit 1
echo "연결 점검 통과 — python sp_core_pull.py --config config.ini 로 받으세요."
