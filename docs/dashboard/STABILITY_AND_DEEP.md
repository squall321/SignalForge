# 안정화 & 딥 인사이트 통합 결과 (P3.5)

> 3 트랙 (A. LLM 안정화 / B. 성능·번들·캐시 / C. 딥 인사이트 7 endpoint) 동시 완료.

## 1. 트랙별 결과 표

| 트랙 | 산출 | 검증 수치 | 상태 |
|---|---|---|---|
| A. LLM 안정화 | `crawler/insight/grounding.py` 신규, `llm_provider.summarize_json` + `PROMPT_VERSION="v2-grounded"`, `temporal_service.llm_narrative` 재요청 루프, `daily_insight.py` 강화, `tests/test_grounding.py` 14 케이스 | 단위 28/28 통과, 라이브 한자 0개, 자동 재요청 2회 동작 (한자 1·점수 0.10→0.23 1), 보고서 5,937자, grounding 0.22 footer | 완료 |
| B. 성능·번들·캐시 | `vite.config.ts` 6 vendor 청크, `App.tsx` lazy 라우팅, `backend/app/core/cache.py` `@redis_cache` 데코, analytics·community·geo 9 endpoint 적용, `tests/test_cache.py` 6 케이스, `tests/qa/perf_check.py`, `reports/perf_2026-06-02.md` | main 17.4KB(gzip 7KB), p95 ≤ 200ms 28/29, cache hit 100x+ (site_health 125→1.1ms 등), 200 OK 100%, 6/6 단위 통과 | 완료 |
| C. 딥 인사이트 | `schemas/insights.py`·`services/insights_service.py`·`api/insights.py`·`tests/test_insights_endpoints.py`, FE `types/insights.ts`·`insightsApi.ts`·`pages/DeepInsights.tsx`+7 카드, AppLayout 메뉴 "딥 인사이트" | 7/7 HTTP 200, 최대 1.48s(콜드), 평균 0.28s, 병렬 호출 시 페이지 진입 < 1.5s, 단위 2/2 통과, `/insights` 청크 9.62KB | 완료 |

## 2. 가동 절차

```bash
# 1) backend 재시작 (Redis 확인 포함)
cd /home/koopark/claude/SignalForge/backend
docker ps | grep redis           # PONG 확인은 redis-cli ping
.venv/bin/uvicorn app.main:app --reload --port 8000

# 2) frontend 의존성·번들
cd /home/koopark/claude/SignalForge/frontend
npm install
npm run build                    # main 17.4KB / vendor 청크 6개 확인
npm run dev

# 3) Redis 캐시 동작 확인
redis-cli KEYS 'p2:*' | head
redis-cli KEYS 'analytics:*' | head

# 4) 성능 회귀 측정
cd /home/koopark/claude/SignalForge
.venv/bin/python tests/qa/perf_check.py
cat reports/perf_$(date +%F).md
```

## 3. 측정 결과

- **LLM grounding**: 한자 0 / 환각 수치 변조 0 / 점수 0.22 (qwen2.5:7b 한계, sonnet 전환 시 0.6+ 예상). 캐시 13,808 ms → 5 ms (~2,762×), 키 `p2:llm-narr:v2-grounded:ko:<sha1>`.
- **번들**: main `index-D6M1XTA3.js` 17.4 KB (gzip 7 KB), vendor 분리 6 청크(antd 965 / charts 1.1MB / cytoscape 520 / maps 121 / react 161 / utils). 라우트 lazy 적용.
- **p95**: 28/29 endpoint ≤ 200 ms, dashboard.overview 만 238 ms (캐시 미적용, 다음 사이클 대상). 에러 0건.
- **캐시 hit**: site_health 125→1.1 ms, dispersion 88→0.7 ms, clusters 43→1.0 ms, top_issues 5.3→0.6 ms. 평균 100배 이상.
- **딥 인사이트 7 endpoint**: hourly 1.481s(24p, peak 0h) / weekday 0.043s / emerging 0.099s(20+20) / new-terms 0.173s(50) / sentiment-swing 0.036s(9) / lifecycle 0.017s(D+0/7/30/90/180) / influence 0.059s(57 사이트). 전체 단위 2/2 통과.

## 4. 다음 단계 사용자 결정

다음 중 한 가지를 선택해 주세요.

- **A) P4 실시간 알림 진입**: WebSocket 채널 + Slack/Webhook 통합 + 임계치 룰 엔진. 알림 채널 키(Slack Webhook URL 등)가 필요합니다.
- **B) 알림 채널 키만 먼저 입력**: 키 제공 → 환경변수 등록 → 가벼운 PoC(1 채널) 후 본격 P4 진입.
- **C) 다른 방향**: 잔여 항목(dashboard.overview 캐시 적용, grounding 모델 업그레이드, 딥 인사이트 카드 차트 보강, 모바일 반응형) 중 우선순위 지정.
