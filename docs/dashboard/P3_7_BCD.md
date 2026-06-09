# P3.7 BCD 통합 보고 (2026-06-02)

## 1. 트랙별 결과 + verify 상태

| 트랙 | 산출 | verify |
|---|---|---|
| B. anomaly-context + sentiment-driver 결합 카드 | backend 2 endpoint, schema 5 클래스, 단위 1 파일 (sentiment+anomaly 검증), frontend AnomalyDriverCard.tsx (gzip 1.48 KB) | pass — 캐시 TTL 600s/300s, 응답 페이로드/빌드 크기 보고와 일치 |
| C. 테스트 인프라 + MV refresh | conftest.py 신규(autouse engine.dispose) 운영 코드 무수정, crawler tasks.py run_refresh_p2_mvs psql subprocess + CONCURRENTLY/blocking fallback, beat 30분 주기 등록 | partial — pytest 17 passed/0 fail (B 결합 카드 케이스 1 증가, 보고 16→실측 17), MV는 mv_voc_daily 1종만 실재(나머지 4종 미생성) |
| D. 추가 deep cut 5 endpoint | backend 5 endpoint(@redis_cache 900s), schema 5 클래스, 단위 1 파일(pytest 1 + 격리 5), frontend 5 카드 + deepApi 확장 | pass — 응답 보고 범위 내, ProductFunnelCard 18.4 KB(<20 KB) 충족 |

## 2. 신규 endpoint (7개 = B 2 + D 5)

| # | Path | 캐시 TTL | cold(보고) | warm 실측 | 사이즈 실측 |
|---|---|---|---|---|---|
| 1 | GET /api/v1/deep/sentiment-driver | 600s | 30~80ms | 1.4ms | 1,926 B |
| 2 | GET /api/v1/deep/anomaly-with-drivers | 300s | 60~120ms | 0.9ms | 638 B |
| 3 | GET /api/v1/deep/category-momentum | 900s | — | 1.5ms | 7,488 B |
| 4 | GET /api/v1/deep/keyword-network | 900s | — | 1.6ms | 16,309 B |
| 5 | GET /api/v1/deep/lifecycle-funnel | 900s | — | 1.0ms | 1,328 B |
| 6 | GET /api/v1/deep/influence-rank | 900s | — | 1.2ms | 4,790 B |
| 7 | GET /api/v1/deep/product-funnel?product=galaxy | 900s | — | 21.2ms | 102 B |

deep route 총합: P3.6 8 + 결합 2 + D 5 = **15** (api/deep.py grep 검증).

## 3. MV 신선도 + pytest 일괄

```
mv_voc_daily : 704s ago (~12분)
mv_*         : 4종 미존재 (생성/마이그레이션 미적용)
```

- crawler beat 30분 주기는 등록되어 있으나 mv_voc_daily 외 대상 부재.
- pytest: `cd backend && set -a; source .env; set +a; .venv/bin/python -m pytest tests/ -q`  → **17 passed, 19 warnings in 19.21s** (충돌 0).

## 4. 가동 절차

```bash
# backend
cd /home/koopark/claude/SignalForge/backend
set -a; source .env; set +a
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# crawler worker + beat
cd /home/koopark/claude/SignalForge/crawler
set -a; source ../backend/.env; set +a
celery -A celery_app worker -l info --concurrency=4 &
celery -A celery_app beat   -l info &

# frontend (dev)
cd /home/koopark/claude/SignalForge/frontend && npm run dev
# build
cd /home/koopark/claude/SignalForge/frontend && npm run build
```

검증 명령:
- 7 endpoint smoke: `for p in sentiment-driver anomaly-with-drivers category-momentum keyword-network lifecycle-funnel influence-rank "product-funnel?product=galaxy"; do curl -s -o /dev/null -w "$p %{http_code} %{time_total}s\n" "http://localhost:8000/api/v1/deep/$p"; done`
- 단위: `.venv/bin/python -m pytest tests/test_combined_card_endpoints.py tests/test_deep_v2_endpoints.py -q`

## 5. 측정 수치

- 응답: 7개 warm 0.9~21.2ms (그 중 6개 <2ms = Redis 적중), product-funnel만 21.2ms (path param 검증으로 캐시 키 분기).
- 빌드 chunk gzip: AnomalyDriver 2.83 KB, CategoryMomentum 1.71 KB, KeywordNetwork 2.0 KB, LifecycleFunnel 1.48 KB, InfluenceRank 1.81 KB, ProductFunnel 1.92 KB, DeepInsights shell 16.3 KB. **SentimentDriverCard 단독 chunk 부재** — AnomalyDriverCard 내부에 inline 됐을 가능성 확인 필요.
- 캐시 hit: redis NOAUTH로 stats 직접 측정 실패. warm 응답 시간으로 간접 확인(<2ms).
- MV 신선도: mv_voc_daily 704s, 나머지 4종 미존재.

## 6. 다음 단계 (사용자 결정 대기)

1. **MV 4종 부재 처리** — alembic migration 누락인지 의도된 deferred인지 확인 필요. crawler beat가 빈 대상으로 idle.
2. **SentimentDriverCard 독립 chunk** — 결합 카드에 흡수됐다면 의도 확인, 아니면 lazy import 분리.
3. **Redis AUTH 자격 정리** — stats/cache hit 모니터링 자동화에 필요.
4. **product-funnel 캐시 키** — path/query 분기에 따른 적중률 측정 후 TTL 재검토.
5. **pytest 17 vs 보고 16** — 결합 카드 케이스 1건 추가분 이외 신규 케이스 없는지 재확인.

verify 종합: B pass · C partial(MV 4종 미존재) · D pass.
