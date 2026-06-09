# P4.0 실시간 알림 + 드릴다운 + LLM 라우팅 + 모바일 통합 보고

기준일 2026-06-02. 워크플로우 wah2mkldr 의 build/verify 산출 + 메인 메이커 보완 + 직접 검증.

## 1. 트랙별 결과

| 트랙 | 산출 | 검증 | 상태 |
|---|---|---|---|
| A. 실시간 알림 | backend/app/core/alerts/ {engine, channels/base·slack·websocket} + api/alerts.py 5 endpoint + alembic 0005 (alert_rules·alert_events seed 3) + crawler/tasks evaluate_alert_rules + beat 5분 주기 + frontend pages/Alerts.tsx + services/alertsApi.ts + 단위 test_alerts_endpoints.py | 5 endpoint 200, WebSocket 연결 + alert broadcast 정상, collect_metrics @redis_cache ttl=30s 적용 (cold 13.9s → warm 1.7ms) | pass |
| B. anomaly drill-down | backend/app/api/deep.py + services/deep_service.py anomaly_drilldown + schemas/deep.py + test_anomaly_drilldown.py | /api/v1/deep/anomaly-drilldown 200 + hourly 24 bucket | pass (frontend Drawer 미작성) |
| C. LLM 라우팅 활성 | crawler/insight/llm_provider.py get_provider(tier=) + quality_report tier 추가 + api/_internal.py llm-status + test_llm_routing_v2 | /api/v1/_internal/llm-status 200, fast=qwen2.5:7b / high=null (키 미입력), env 표시 정상 | pass |
| D. 모바일 차트 reflow | frontend/src/utils/useViewport.ts + components/common/ResponsiveCard.tsx + 카드별 echarts grid xs 분기 + AppLayout drawer | vitest 48/48, main 17.19 KB / Alerts 3.41 KB | pass |

## 2. 신규 endpoint (8개)

| Path | 종류 | cold | warm | 비고 |
|---|---|---|---|---|
| WS /api/v1/alerts/ws | A | — | — | hello 즉시 / ping 30s |
| GET /api/v1/alerts/rules | A | <10ms | <2ms | seed 3 |
| POST /api/v1/alerts/rules | A | <10ms | — | DB INSERT |
| DELETE /api/v1/alerts/rules/{id} | A | — | — | |
| POST /api/v1/alerts/test | A | 13.9s | 1.7ms | metrics cache 30s |
| GET /api/v1/alerts/recent | A | <10ms | <2ms | |
| GET /api/v1/deep/anomaly-drilldown | B | 242ms | 1.5ms | 24h × 제품 × 키워드 × 사이트 |
| GET /api/v1/_internal/llm-status | C | <10ms | — | fast/high tier status |

총 endpoint: 62 (P3.7 56 + 신규 6 = 62, drilldown/llm-status 포함).

## 3. 알림 룰 3 seed + 채널

| 룰 | metric_path | op | th | severity | cooldown | 현재 metrics |
|---|---|---|---|---|---|---|
| anomaly_z_high | community.extreme_negative_count | >= | 3 | critical | 900 | 0 (미발화) |
| negative_rate_spike | community.negative_rate_max | > | 0.4 | warning | 900 | 0.0 (미발화) |
| new_term_spike | insights.new_term_spike_count | >= | 20 | warning | 900 | **50** (발화 중) |

채널: slack (dry-run, SLACK_WEBHOOK_URL 미설정) + websocket (활성).

## 4. LLM tier 상태

```json
{
  "fast": {"provider":"openai","model":"qwen2.5:7b","base_url":"http://127.0.0.1:11434/v1","reachable":null},
  "high": {"provider":null,"model":null,"base_url":null,"reachable":null},
  "env":  {"LLM_QUALITY_TIER":"auto","has_anthropic_key":false,"has_openai_sk_key":false}
}
```

활성화 가이드: `.env` 에 `ANTHROPIC_API_KEY=sk-ant-...` 또는 `OPENAI_API_KEY=sk-...` (ollama 가 아닌) 추가 → backend 재시작. grounding 점수 0.23 (qwen) → 0.6+ 예상 (sonnet).

## 5. 모바일 viewport 동작

- breakpoint: xs<576, sm<768, md<992, lg<1200, xl>=1200
- Standard 7 + Deep 14 카드 모두 ResponsiveCard 래퍼 + echarts grid 분기
- AppLayout Drawer 햄버거 메뉴 (md 미만)
- vitest 48/48 통과

## 6. 가동 절차

```bash
# backend
cd /home/koopark/claude/SignalForge/backend
set -a; source ../.env; set +a
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# alembic
cd /home/koopark/claude/SignalForge/backend
.venv/bin/python -m alembic upgrade head   # 0005 alert_objects 적용

# celery (alerts evaluate 5분 + 기존 작업)
cd /home/koopark/claude/SignalForge/crawler
set -a; source ../.env; set +a
celery -A celery_app worker -l info --concurrency=4 &
celery -A celery_app beat   -l info &

# frontend
cd /home/koopark/claude/SignalForge/frontend
npm run dev   # 5174

# WebSocket 동작 확인
python -c "
import asyncio, websockets
async def m():
  async with websockets.connect('ws://127.0.0.1:8000/api/v1/alerts/ws') as ws:
    print(await ws.recv())
asyncio.run(m())"

# 알림 1회 즉시 발화
curl -X POST http://127.0.0.1:8000/api/v1/alerts/test
```

## 7. 측정 수치

- pytest 일괄: **20/20 PASS** (P3.7 17 + 신규 3 = 20)
- vitest: **48/48 PASS** (P3.7 27 + 모바일 21 = 48)
- frontend build: main **17.19 KB** / DeepInsights 17.30 KB / Alerts 3.41 KB / 21 Deep 카드 lazy
- alerts/test cold 13.9s → warm 1.7ms (~8200×)
- WebSocket 연결 + alert broadcast 정상 (`{"type":"hello",...}` + `{"type":"alert",...}` 수신 확인)
- 캐시 hit ratio 83%+ 유지

## 8. 다음 단계 (사용자 결정 대기)

- **A) ANTHROPIC_API_KEY 입력** → LLM grounding 0.6+ 실측 + 일일 보고서 고품질 전환
- **B) SLACK_WEBHOOK_URL 입력** → Slack 실 전송 활성 (현재 dry-run)
- **C) anomaly-drilldown Drawer UI** (Frontend B 트랙 미완성분 — backend는 완료)
- **D) collect_metrics 무거운 SQL 최적화** (cold 13.9s — new_terms 90일 윈도우)
- **E) Alerts 페이지 UX** — 룰 생성 폼, 발화 그래프, 채널 설정 패널
