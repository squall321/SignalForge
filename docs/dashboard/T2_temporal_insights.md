# T2. 기간별 소비자 인식 변화 인사이트 (Temporal Perception Drift)

> "S25 출시 후 4주간 카메라 부정 비율이 12%→27%로 급등했다" — 한 문장으로 출하/PR/엔지니어링 의사결정을 트리거하는 뷰.

---

## 1. 목적 (Why)

이 트랙은 단순 시계열 차트가 아니라 **변화 그 자체**를 1차 자산으로 다룬다. 사용자가 얻어야 할 결정 4종:

- **PR/CS 대응 타이밍**: 부정 spike 발생 시각을 D+0 이내 인지 → 이슈 응답 SLA 24h.
- **소프트웨어 패치 효과 검증**: One UI 8.1 배포일 마커 전후 software 카테고리 negative 비율이 떨어졌는가.
- **제품 출시 사이클 학습**: S24→S25→S26 출시 후 4주 카메라 부정률 곡선 overlay → 다음 모델 PR 캠페인 설계.
- **경쟁사 사건 영향 흡수도**: iPhone 16 Pro 배터리 이슈 보도일 기준 S25U battery comparison 카테고리 mention 변화.

핵심 가설: VOC는 "수준(level)"보다 "변곡점(change-point)"에서 의사결정 가치가 폭발한다.

---

## 2. 데이터 모델

### 사용 컬럼
`voc_records`의 `published_at`, `product_id`, `categories[]`, `sentiment_score`, `sentiment_label`, `language_detected`, `platform_id`, `engagement_score`.

### 신규 자산

**Materialized View `mv_voc_daily`** (재계산 비용 차단):
```sql
CREATE MATERIALIZED VIEW mv_voc_daily AS
SELECT
  date_trunc('day', published_at)::date         AS bucket_day,
  product_id,
  unnest(categories)                            AS category,
  language_detected,
  COUNT(*)                                      AS n,
  COUNT(*) FILTER (WHERE sentiment_label='negative') AS n_neg,
  COUNT(*) FILTER (WHERE sentiment_label='positive') AS n_pos,
  AVG(sentiment_score)                          AS avg_score,
  SUM(engagement_score)                         AS engagement_sum
FROM voc_records
WHERE published_at >= now() - INTERVAL '18 months'
GROUP BY 1,2,3,4;

CREATE INDEX ix_mvd_pdc ON mv_voc_daily(product_id, bucket_day, category);
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_voc_daily;  -- Celery 30min
```

**Event 테이블 `timeline_events`** (수동 + 자동 큐레이션):
```sql
CREATE TABLE timeline_events (
  id BIGSERIAL PRIMARY KEY,
  event_date DATE NOT NULL,
  event_type TEXT NOT NULL,          -- launch|os_update|incident|press|patch
  product_id INT NULL,               -- NULL=global
  title TEXT NOT NULL,
  source_url TEXT,
  severity SMALLINT DEFAULT 1        -- 1~5
);
```

**Change-point 후보 캐시 `voc_changepoints`**: 일배치로 PELT/CUSUM 결과 저장 (product×category×bucket).

`sentiment_ko` vs VADER 정합성: `language_detected IN ('ko','en')` 별도 series로 분리해 dual-line 으로 그리고, 두 line의 standardized z-score를 별도 계산해 "양쪽이 동시에 움직인 변곡점만 confirmed=true 플래그".

---

## 3. 시각화 명세

### 차트 선택 매트릭스

| 차트 | 사용 시점 | 축 |
|---|---|---|
| **Multi-line** | 카테고리 ≤4개, 추세 비교 | x=time, y=neg_ratio(%) |
| **Stacked Area (100%)** | 카테고리 구성 변화 | x=time, y=share, color=category |
| **Calendar Heatmap** | 6개월 일별 부정량 패턴 (요일성 발견) | 7×26 grid, color=n_neg |
| **Stream/Diverging Bar** | pos vs neg 같은 축 위/아래 대칭 | x=time, y=±count |
| **Slope Chart** | T1 vs T2 두 시점 단순 비교 | 2 points × N categories |

기본 뷰는 **Multi-line + Event Marker overlay**.

### ASCII Mockup

```
S25 Ultra · Negative Ratio (%) · Last 90d · weekly · ko+en
 40 ┤                       ▲launch(2/8)        ▲OS8.1(4/3)
 35 ┤                       │                   │
 30 ┤             camera ●──┼──●──●            │
 25 ┤  ●──●──●            │     \●──●──●──●──●●
 20 ┤        \●         battery │                ●──●
 15 ┤         \●──●──●─────────●─────●──●──●──●
 10 ┤  software ◇──◇──◇──◇──◇──◇──◇──◇──◇──◇──◇
  5 ┤
  0 └──────────────────────────────────────────────
     W-12  W-10  W-8  W-6  W-4  W-2  W0    +2   +4
     ★ CP detected 3/15 camera +14pp p=0.003
```

색상: 카테고리 12종은 ColorBrewer Set3 + 부정 라인은 채도↑/긍정 라인은 채도↓. 이벤트 마커는 vertical dashed + tooltip. Change-point는 별표(★) + p-value.

인터랙션: x축 brush로 zoom, 라인 클릭 → drill (해당 카테고리 raw VOC top-20 modal), 마커 hover → 이벤트 카드.

---

## 4. API Endpoint

### `GET /analytics/temporal-series`
파라미터:
- `product`: str (필수, 단일 또는 `A,B` 콤마)
- `categories`: str (콤마, 기본 all)
- `from`, `to`: ISO date (기본 to=today, from=-90d)
- `bucket`: `day|week|month` (기본 week)
- `metric`: `neg_ratio|pos_ratio|avg_score|count|engagement` (기본 neg_ratio)
- `lang`: `ko|en|all` (기본 all)
- `include_events`: bool (기본 true)
- `include_changepoints`: bool (기본 true)

응답:
```json
{
  "product": "GS25U",
  "bucket": "week",
  "metric": "neg_ratio",
  "series": [
    {"category":"camera","points":[
      {"t":"2026-03-02","value":0.18,"n":412,"ci_low":0.15,"ci_high":0.21},
      {"t":"2026-03-09","value":0.27,"n":389,"ci_low":0.23,"ci_high":0.31}
    ]},
    {"category":"battery","points":[...]}
  ],
  "events":[
    {"date":"2026-02-08","type":"launch","title":"S25U 글로벌 출시","severity":5}
  ],
  "changepoints":[
    {"category":"camera","t":"2026-03-15","delta_pp":14.2,"p_value":0.003,"direction":"up","confirmed":true}
  ],
  "llm_summary":"지난 4주간 camera 부정 비율이 13pp 상승. ko/en 양쪽 동조. 출시일 기준 +5주 시점이며 야간 촬영 관련 키워드가 38% 점유."
}
```

### `GET /analytics/temporal-compare`
`mode=products|periods|categories` + 두 series 동기화 응답.

### `POST /analytics/llm-narrative` (B 모듈 연동)
입력으로 series + changepoints 요약을 LLM에 던져 240자 내러티브 1개 + bullet 3개 반환. 캐시 키 = hash(product,bucket,from,to,metric).

---

## 5. 프론트 컴포넌트 구조

```
pages/TemporalInsights.tsx
├── <FilterBar/>            product, categories, range, bucket, metric, lang
├── <KPIStrip/>             period_total, neg_ratio_now vs T-1, CP_count
├── <MainTimeline/>         (left col, 8/12)
│   ├── <Chart type="line"/>      Recharts/ECharts
│   ├── <EventMarkerLayer/>
│   └── <ChangePointLayer/>
├── <SidePanel/>            (right col, 4/12)
│   ├── <LLMNarrative/>     B 모듈 호출, skeleton→stream
│   ├── <ChangePointList/>  클릭 → MainTimeline focus
│   └── <EventEditor/>      수동 이벤트 추가 (관리자)
├── <CompareDrawer/>        제품A vs B / 기간A vs B 토글
└── <DrillModal/>           라인 클릭 시 raw VOC 20건
```

상태: React Query + URL state (zustand `useTemporalParams`), brush range는 debounced 400ms로 API 재호출.

---

## 6. 인터랙션 흐름

1. FilterBar에서 `S25U` + `last 90d` + `week` 선택 → `/temporal-series` 호출.
2. KPIStrip: "negative 24.1% (▲ +6.2pp WoW)". 빨간색.
3. MainTimeline: camera line이 3/15 ★ 표시.
4. ★ 클릭 → ChangePointList 동기 highlight + LLMNarrative 자동 갱신.
5. 사용자 "출시일 마커 의심" → 마커 hover → "S25U 글로벌 출시 2026-02-08". CP는 +5주이므로 launch 직접 영향이 아님을 시각적으로 판단.
6. camera 라인 클릭 → DrillModal 20건 → 야간 모드 불만 8건 발견 → "비교 모드" 진입 → S24U 같은 출시 +5주 구간 overlay → 작년에는 동일 spike 없음 확인.
7. 우측 LLMNarrative "공유" → 마크다운/Slack 포맷 export.

---

## 7. 단계적 구현

**MVP (Week 1)**
- `mv_voc_daily` + `/temporal-series` (events, changepoints는 빈 배열)
- Multi-line + bucket switch + 1 product, 1 metric
- 검증: 기존 `/sentiment-trend`와 숫자 일치

**강화 (Week 2)**
- `timeline_events` 시드 (제품 출시일 48종 + OS 업데이트 12건)
- Calendar heatmap, Stacked area, Slope chart 토글
- `/temporal-compare` (제품 2개 overlay)
- ko/en dual-line, CI band

**고도화 (Week 3+)**
- `ruptures` (PELT) 일배치 + `voc_changepoints` 캐시
- LLM Narrative (Claude/GPT, B 모듈 통합, 캐시)
- 자동 confirmed 플래그(ko·en 동조)
- WebSocket: 실시간 spike alert 푸시
- Cohort 비교(출시 D+N 정규화 x축)

---

## 8. 트레이드오프와 한계

- **Sparsity**: 신규 플랫폼·소수 언어는 일 buckets에서 n<5 → 신뢰구간 폭발. 자동으로 `bucket` upscale 제안.
- **Change-point 거짓 양성**: PELT는 분산 변동에도 반응. p-value + min_delta=5pp + n_min=50 게이트 3중.
- **VADER/한국어 sentiment 분포 차이**: VADER는 강한 부정에 편향, 한국어 ko-sentiment는 중립 과다. 절대값 비교 금지, **z-score 변화량**만 신뢰.
- **이벤트 인과관계 착시**: 마커와 spike가 가까워 보여도 인과 아님. UI에 "correlation ≠ causation" 명시 + 사용자가 직접 라벨링 가능.
- **LLM hallucination**: narrative는 항상 수치 인용 형식("X% → Y%, p=Z") 강제, free-form 금지. 캐시 24h.
- **Refresh 지연**: MV는 30분 지연 → 실시간 spike는 별도 streaming 경로.

---

## 9. 검증 기준

1. **숫자 일관성**: 동일 product·기간으로 `/temporal-series`(bucket=week)와 기존 `/sentiment-trend` 가 ±0.5pp 이내 일치.
2. **CP 재현성**: 시드 데이터에 인위적 +15pp 점프 삽입 시, 해당 bucket이 changepoints 배열에 `delta_pp≥10, p<0.05, confirmed=true`로 등장.
3. **응답 SLA**: 90d×week×3 카테고리 응답 p95 < 600ms (MV hit), p95 < 2.0s (cold). LLM narrative p95 < 4s (캐시 hit < 200ms).
