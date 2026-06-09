# SignalForge Dashboard — Master Plan

## 1. 비전 한 줄
60+ 글로벌 커뮤니티에서 수집한 11만+ VOC를 **지식 그래프 · 시간 변화 · 커뮤니티 · 국가** 4개 렌즈로 교차 탐색해, 출시/PR/CS/엔지니어링 의사결정을 24시간 SLA 안에 내릴 수 있는 단일 셸을 만든다. 평면 집계가 아닌 **관계와 변곡점**을 1차 자산으로 다루며, URL 한 줄로 임원 공유가 가능한 사내 분석 워크벤치를 지향한다. 12주 안에 4단계로 P1 MVP → P4 실시간 알림까지 점진 적재한다.

## 2. 5개 트랙 요약 표

| 트랙 | 핵심 차트 | 신규 API | 의사결정 가치 | 우선순위 |
|---|---|---|---|---|
| T1 지식 그래프 | Cytoscape 5-type force-directed | `/kg/graph`, `/kg/node/{id}/samples`, `/kg/search` | 다축 동시 등장 패턴 발굴 — "S26+battery+Reddit+발열" | P2 (4주차~) |
| T2 기간별 변화 | Multi-line + Event/CP marker | `/analytics/temporal-series`, `/temporal-compare`, `/llm-narrative` | 변곡점 D+0 인지 → PR/CS 24h 대응 | P2 (4주차~) |
| T3 커뮤니티별 | Platform×Product Heatmap, Lag Timeline | `/community/platforms/health`, `/product-matrix`, `/dispersion`, `/early-signal`, `/clusters`, `/anomalies` | Early-warning 6h lead-time 확보 | P3 (8주차~) |
| T4 국가별 | Choropleth + Diffusion Player | `/country/choropleth`, `/country/{code}/drilldown`, `/country/diffusion`, `/country/product-compare` | 지역별 출시 전략 차등화, 블라인드스팟 식별 | P3 (8주차~) |
| T5 통합 셸 | KPI 4종 + Global Filter + URL sync | `/dashboard/overview`, `/dashboard/share-link`, WS `/ws/voc` 활성화 | 진입점 + 컨텍스트 유지 + 100ms 응답 | **P1 (1주차)** |

## 3. 통합 아키텍처 다이어그램

```
┌─ 사용자(사내 PM/마케팅/CS, 임원 공유 링크) ─────────────────────┐
│  Browser (Chrome/Edge) · Tablet(>=768px) · 임원 폰 Overview만   │
└──────────────────────┬──────────────────────────────────────────┘
                       │ HTTPS + Basic Auth(P1) / Google OAuth(P4)
                       ▼
┌─ Nginx Reverse Proxy ────────────────────────────────────────────┐
│  /dashboard/* → Vite static    /api/* → FastAPI    /ws/* → WS    │
└──────┬───────────────────────────┬───────────────────────┬───────┘
       ▼                           ▼                       ▼
┌─ React 18 + Vite 5 + AntD 5 ─┐  ┌─ FastAPI Analytics ─┐  ┌─ WS Hub ─┐
│ TanStack Query v5 (60s stale)│  │  GZip + exclude_none │  │ broadcast│
│ ECharts(map/time) Cytoscape   │  │  Redis L1 cache      │  │ debounce │
│ zustand + URL sync           │  │  TTL 5min, pub-sub   │  │ 1s/client│
└──────┬───────────────────────┘  └────────┬─────────────┘  └────┬─────┘
       │                                   │                     │
       └──────────────── React Query ──────┘                     │
                                           ▼                     │
                            ┌─ Redis (cache + pub-sub) ──────────┘
                            │  sf:{view}:{params}:hash TTL 5m
                            │  channel voc:new
                            └──────────┬─────────────────────────
                                       ▼
       ┌─ PostgreSQL 15 ───────────────────────────────────────┐
       │  voc_records (raw 11만→100만)                          │
       │  mv_voc_daily / mv_voc_category_daily                  │
       │  mv_platform_health / mv_country_daily / mv_kg_edges   │
       │  voc_keywords / timeline_events / voc_changepoints     │
       │  Celery beat: 10~60min REFRESH CONCURRENTLY            │
       └────────────────┬──────────────────────────────────────┘
                        ▲ INSERT
                        │
       ┌─ Celery Worker (Crawler) ─── publish voc:new ──────────
```

## 4. 단계별 로드맵 (4 Phase, 총 12주)

| Phase | 기간 | 산출물 | 검증 기준 | 의존성 |
|---|---|---|---|---|
| **P1 MVP 셸** | W1~W2 (2주) | T5 셸, Overview 페이지, KPI 4종, 기존 5 endpoint 재사용, Nginx 배포, URL 공유 | URL incognito 재현 스크린샷 diff <2%, Overview p95 ≤200ms | 없음 (즉시 시작) |
| **P2 T1+T2** | W3~W6 (4주) | `mv_voc_daily/category_daily/kg_edges_daily` + `voc_keywords` + `timeline_events`, Graph 페이지, Timeline 페이지, 신규 7 endpoint | Graph TopN=80/90d p95 <600ms, Timeline ±0.5pp 기존 일치, CP 시드 재현 | P1 셸 + mv refresh 파이프라인 |
| **P3 T3+T4** | W7~W10 (4주) | `mv_platform_health/country_daily` + `country_meta` + 클러스터 batch, Platform 페이지, Geo 페이지, 신규 10 endpoint, Redis invalidation | Heatmap 2880셀 p95<700ms, Choropleth voc_total ±0.5% raw 일치, Early-signal 3건 ≥6h lead | P2의 mv 인프라 |
| **P4 실시간+알림** | W11~W12 (2주) | WS pub/sub 활성, LLM Narrative B 모듈, Slack/메일 spike alert, 임시 공유 토큰(30일), Google OAuth | INSERT → KPI 증분 ≤5초, narrative p95 <4s, spike 5분 내 Slack | P3 완료 + Redis pub-sub |

## 5. 즉시 시작 가능한 첫 5개 작업 (P1 잘게 쪼개기)

1. **Frontend** — Vite+React18+AntD5 스캐폴드 + `AppLayout`/`GlobalFilterBar`/`useFilterStore`(URL 양방향 동기화) — 2일
2. **Backend** — `GET /api/v1/dashboard/overview` (period·product·country·platform 파라미터, KPI 4종 + mini trend 14일 + top 5 sites) — 1.5일
3. **DB** — `mv_voc_daily` 정의 + 복합 UNIQUE 인덱스 + Celery beat 30분 `REFRESH CONCURRENTLY` 등록 — 1일
4. **Infra** — Nginx `/dashboard/`·`/api/`·`/ws/` 분기 + Basic Auth + GitHub Actions rsync 배포 파이프라인 — 1일
5. **QA** — Locust 시나리오(동시 100, Overview 호출) + URL 공유 incognito 재현 테스트 + 스크린샷 diff CI — 0.5일

총 6일, 1명 풀스택 기준. 검증 게이트 통과 시 P2 착수.

## 6. 신규 Backend API Endpoint 통합 표

| # | Track | Method · URL | 주요 파라미터 | 응답 schema 요약 | 신규 |
|---|---|---|---|---|---|
| 1 | T5 | GET `/api/v1/dashboard/overview` | period, product, country, platform | `{kpis:{voc_24h,neg_ratio,active_platforms,countries}, trend14d[], top_sites[]}` | 신규 |
| 2 | T5 | POST `/api/v1/dashboard/share-link` | filters, expires_days | `{token, url, expires_at}` | P4 신규 |
| 3 | T1 | GET `/api/v1/kg/graph` | start, end, edge_types, product_ids, top_n, min_weight, lang | `{period, nodes[], edges[], stats{node_n,edge_n,truncated}}` | 신규 |
| 4 | T1 | GET `/api/v1/kg/node/{id}/samples` | limit=5 | `[{voc_id, snippet_highlighted, sentiment, source_url, published_at}]` | 신규 |
| 5 | T1 | GET `/api/v1/kg/search` | q, limit=10 | `[{type, id, label, score}]` | 신규 |
| 6 | T2 | GET `/api/v1/analytics/temporal-series` | product, categories, from, to, bucket, metric, lang, include_events, include_changepoints | `{series[{category,points[{t,value,n,ci_low,ci_high}]}], events[], changepoints[]}` | 신규 |
| 7 | T2 | GET `/api/v1/analytics/temporal-compare` | mode(products\|periods\|categories), keys[] | `{a:{...series}, b:{...series}, diff[]}` | 신규 |
| 8 | T2 | POST `/api/v1/analytics/llm-narrative` | series_payload, lang | `{summary, citations[{metric,value,t}]}` cache 24h | P4 신규 |
| 9 | T3 | GET `/api/v1/community/platforms/health` | — | `[{platform_code, status, posts_24h, posts_7d, hours_since_last, sent_avg}]` | 신규 |
| 10 | T3 | GET `/api/v1/community/platforms/product-matrix` | since, products | `{cells:[{platform,product,n,sent_avg}]}` | 신규 |
| 11 | T3 | GET `/api/v1/community/platforms/dispersion` | product_id, since | `{boxplot:[{platform,q1,med,q3,whis_lo,whis_hi}], outliers[]}` | 신규 |
| 12 | T3 | GET `/api/v1/community/platforms/early-signal` | product_id, category | `{event:{spike_at,z}, timeline:[{platform_code,first_post_at,lag_hours}]}` | 신규 |
| 13 | T3 | GET `/api/v1/community/platforms/clusters` | k=6 | `{points:[{platform,x,y,cluster_id}], centroids[]}` | 신규 |
| 14 | T3 | GET `/api/v1/community/platforms/anomalies` | — | `[{platform_code, reason, since}]` | 신규 |
| 15 | T4 | GET `/api/v1/analytics/country/choropleth` | product_id, date_from, date_to, metric | `{items:[{iso2,iso3,voc_count,sent_avg,sent_z,covered}], totals}` | 신규 |
| 16 | T4 | GET `/api/v1/analytics/country/{code}/drilldown` | date_from, date_to | `{top_sites[], top_products[], top_categories[]}` | 신규 |
| 17 | T4 | GET `/api/v1/analytics/country/diffusion` | product_id, date_from, granularity | `{frames:[{day, items:[{iso2,n,sent_avg}]}]}` | 신규 |
| 18 | T4 | GET `/api/v1/analytics/country/product-compare` | product_id, countries | `{rows:[{country,n,sent_avg,ci_lo,ci_hi}]}` | 신규 |
| 19 | T5 | WS `/ws/voc` (활성화) | — | `{voc_id, product, platform, country, sentiment, ts}` 1초 디바운스 | P4 활성 |

**총 19 endpoint** (신규 18 + WS 1). 기존 5종(`/sentiment-trend` 등)은 P1에서 Overview가 재사용 후 P2에서 `/temporal-series`로 점진 치환.

## 7. 의사결정 필요 사항 (사용자 결정)

1. **프론트 스택**: React+Vite+AntD 권고. Next.js는 SSR 불필요 + 차트 SDK 충돌로 비추. **승인 필요**.
2. **인증**: P1~P3 Nginx Basic Auth(임시) + IP 화이트리스트, P4 Google OAuth 2-role(viewer/admin). **외부 공개는 거절 권고** — VOC 본문 PII 가능성. 사내 정책 확인 필요.
3. **배포 위치**: 사내 온프레미스(백엔드와 같은 VPC) + Nginx 정적 서빙 권고. Vercel은 사내망 백엔드와 CORS·인증 복잡도로 비추. **사내 인프라팀 확정 필요**.
4. **데이터 보존**: `mv_voc_daily` 18개월, raw `voc_records` 무기한 권고. 100만 행 도달 시 월별 partitioning 평가. **보존 정책 승인 필요** (특히 PII 삭제 요청 대응 SLA).
5. **LLM 활용 깊이**: 최소(narrative 요약만, P4) vs 중간(narrative + 키워드 KeyBERT 재추출 + 이벤트 자동 라벨) vs 최대(자동 인사이트 카드 생성). 비용·hallucination 트레이드오프. **예산 한도 확인 필요** — OpenAI/Anthropic 월간 호출 cap.

## 8. 리스크 & 대응

1. **MV refresh 지연 vs 실시간성 충돌** — `mv_voc_daily`는 30분 주기, P4 WS는 5초 SLA 요구. 충돌 시 Overview KPI가 알림과 불일치. **대응**: WS는 raw count delta만 broadcast하고 sentiment/heatmap은 mv 갱신을 기다린다. UI에 "데이터 기준: 12분 전" 타임스탬프 명시.
2. **다국어 sentiment 모델 calibration 차이** — ko VADER 대체 모델과 en VADER의 절대값 분포가 다르면 T3 dispersion, T4 choropleth가 모델 차이를 인식 차이로 오해. **대응**: 절대값 비교 금지 원칙 — z-score 정규화 + 사이트/국가별 offset 보정 + UI 토글 "원본/보정". 단일 다국어 모델(XLM-R 기반) 통일을 P3 내 마이그레이션.
3. **번들 크기 + 차트 라이브러리 2종 학습비용** — AntD 3MB + ECharts + Cytoscape 동시 적재 시 첫 진입 5초+. **대응**: per-page code split (`React.lazy`) + AntD babel-plugin-import tree-shake (1.2MB로 축소) + Cytoscape는 T1 페이지에서만 dynamic import. 목표 Overview 첫 페인트 < 1.5s, Graph 페이지 첫 진입 < 3s.
