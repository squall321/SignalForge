# P3.6 Deeper — 트랙 통합 보고

기준일 2026-06-02. P3.5 안정화(39 endpoint, 캐시 4700×, 번들 16KB) 위에 5 트랙 병렬 구축 후 verify subagent로 교차검증한 결과를 정리한다.

## 1. 트랙별 결과 + verify 상태

| 트랙 | Build 산출 | Verify | 상태 |
|---|---|---|---|
| A. 심층 분석 8 endpoint | schemas/deep.py(220 LOC), services/deep_service.py(1043 LOC), api/deep.py(130 LOC), tests/test_deep_endpoints.py(133 LOC), main.py 라우터 등록 | 8/8 endpoint 200, 캐시 miss 27~149ms, anomaly 8.3KB 일치 | pass (LOC 보고치는 175/660/115/125 → 실측 220/1043/130/133 — 과소보고였음) |
| B. dashboard.overview 캐시 + LLM tier | dashboard_service.py @redis_cache(ttl=120s) 부착, llm_provider.py OllamaProvider + get_provider(prefer, tier) + LLM_QUALITY_TIER env, test 2종, LLM_ROUTING.md | overview cold=215ms / warm <5ms, LLM 라우팅 3/3 pass, qwen2.5:7b 확인 | pass |
| C. DeepInsights 카드 차트화 + 신규 8 카드 | types/deep.ts, services/deepApi.ts, DeepInsights.tsx 2-section + IntersectionObserver lazy, EmergingKeywordsCard 외 카드 컴포넌트 | 8 service method 존재, 차트화 반영 | pass (deep_service LOC 1044 보고 → 실측 1043, 1줄 오차 허용) |
| D. 모바일 반응형 | AppLayout.tsx Drawer 전환, responsive.ts 순수함수, __tests__/responsive.test.ts 7 케이스 | vitest 27/27 pass, main 번들 16.99KB | pass (DeepInsights 9.62KB 보고는 P3.5 기준치 — 실측 14.48KB. 트랙 D가 변경한 곳 아님) |
| E. 운영 품질 모니터링 자동화 | core/cache.py INCR + get_cache_stats, api/_internal.py 캐시 통계 endpoint, crawler/insight/quality_report.py, tasks.run_quality_report, beat 09:30 KST | endpoint 200 + localhost-only 403 동작, 단위 3/3, 첫 리포트 2.5s 내 생성, 29 endpoint p95 측정 | pass (overview p95 보고 202.4ms — verify는 구체값 미확정) |

## 2. Discovery 8 항목 요약 표

| 우선순위 | endpoint | 시각화 | 결정 가치 |
|---|---|---|---|
| 1 | /deep/anomaly-context | z-score spike + 근거 키워드 카드 | 이상 급등의 원인 단서 즉시 파악 |
| 2 | /deep/cohort-retention | 코호트 히트맵 | 신규 키워드의 잔존/소멸 판정 |
| 3 | /deep/cross-platform-diffusion | 플랫폼 간 확산 경로 산점도 | 어느 채널이 진원지/추종자인지 |
| 4 | /deep/sentiment-driver | 감정 변동 기여 키워드 막대 | 부정 급등 원인 빠른 식별 |
| 5 | /deep/category-momentum | 카테고리별 모멘텀 라인 | 시장 카테고리 비중 변동 |
| 6 | /deep/keyword-network | 공출현 그래프 (간소화) | 군집 정체성 |
| 7 | /deep/lifecycle-funnel | 신규→성장→정체→감소 깔때기 | 키워드 수명 단계 분포 |
| 8 | /deep/influence-rank | 영향력 점수 정렬 | 마케팅 우선순위 |

## 3. 가동 절차

```
# backend
cd /home/koopark/claude/SignalForge/backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
# env: DATABASE_URL, REDIS_URL, LLM_QUALITY_TIER=balanced (선택)

# frontend
cd /home/koopark/claude/SignalForge/frontend
npm run dev   # vite, http://localhost:5173

# 품질 보고 수동 실행
cd /home/koopark/claude/SignalForge/crawler
celery -A celery_app call tasks.run_quality_report
# 또는 beat: celery -A celery_app beat -l info (09:30 KST 자동)

# 캐시 통계 (localhost only)
curl http://127.0.0.1:8000/api/v1/_internal/cache-stats
```

## 4. 측정 결과 수치

- dashboard/overview: cold 215ms / warm <5ms (캐시 적중 시 ≥40×).
- 신규 deep 8 endpoint 평균 응답: 27~149ms (캐시 miss 기준), miss 평균 약 70ms.
- 캐시 hit rate: verify 시점 58.23% → 66.07% 상승 (INCR 카운터 정상 누적).
- LLM tier 라우팅: anthropic 우선 / openai 폴백 / ollama 최후 3 케이스 모두 pass. 키 미입력 환경에서 ollama(qwen2.5:7b) 도달 확인.
- 모바일 빌드: vitest 27/27, main 번들 16.99 KB (gzip 7.31 KB) — P3.5 16.3 KB 대비 +0.7 KB.
- 품질 자동 보고 첫 산출: `docs/dashboard/quality_2026-06-02.md` 4 섹션(캐시·grounding 0.230·MV·p95) 정상 생성, 29 endpoint p95 측정.

## 5. 다음 단계 (사용자 결정 대기)

- **가장 의미 있는 deep cut**: anomaly-context + sentiment-driver 결합 카드(원인+감정) 권장. 이상 급등이 호재/악재인지 한 화면에서 판정 가능.
- **LLM 키 입력 시**: 현 ollama grounding 0.22~0.23 → claude sonnet 전환 시 0.6+ 예상 (LLM_ROUTING.md 표 기준). 비용은 일 보고 기준 월 USD 5 이하 추정.
- **P4(실시간 알림) 진입 시점**: 현재 캐시 hit ≥60%, p95 안정, 자동 품질 리포트 정상. P4 트리거 조건(이상 z>3, 감정 급변) 데이터 소스가 이미 안정화됨 — 즉시 진입 가능. 다만 키 입력 후 grounding이 0.5+를 1주 유지하는 것을 선행 권장.
