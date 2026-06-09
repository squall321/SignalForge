# T3. 커뮤니티(사이트)별 분석 — 심층 설계

## 1. 목적 (Why)

같은 제품·이슈라도 **사이트마다 다르게 말한다**. dcinside는 결함을 빨리 잡지만 톤이 신랄하고, 9to5google은 늦지만 분석적이며, weibo는 가격·외관 비중이 압도적이다. 본 트랙은 PM/마케팅이 다음 세 가지 의사결정을 내리도록 한다.

- **"어디서 먼저 터지는가?"** — early-warning 사이트를 식별해 위기대응 lead-time 확보
- **"어디 의견을 신뢰할 것인가?"** — 사이트별 sentiment bias를 보정해 진짜 평균 인식 추정
- **"어디를 더 크롤링해야 하는가?"** — 사이트 health/유효 시그널 ROI 기반의 크롤러 자원 재배분

## 2. 데이터 모델

### 기존 활용
- `voc_records.platform_id, product_id, sentiment_score, categories, published_at, content_original, comments_count, likes_count`
- `platforms(code, name, region)` (60+행, 24개 지역)

### 신규 인덱스 (필수)
```sql
CREATE INDEX CONCURRENTLY idx_voc_platform_published
  ON voc_records (platform_id, published_at DESC);
CREATE INDEX CONCURRENTLY idx_voc_platform_product_sent
  ON voc_records (platform_id, product_id, sentiment_label);
```

### Materialized View: 사이트 health (10분 주기 refresh)
```sql
CREATE MATERIALIZED VIEW mv_platform_health AS
SELECT
  p.id AS platform_id, p.code, p.name, p.region,
  COUNT(*) FILTER (WHERE v.collected_at > NOW() - INTERVAL '24 hours') AS posts_24h,
  COUNT(*) FILTER (WHERE v.collected_at > NOW() - INTERVAL '7 days')   AS posts_7d,
  AVG(LENGTH(v.content_original)) FILTER (WHERE v.collected_at > NOW() - INTERVAL '7 days') AS avg_len_7d,
  AVG(v.comments_count::float / NULLIF(v.likes_count + 1, 0))           AS comment_ratio,
  AVG(v.sentiment_score) FILTER (WHERE v.collected_at > NOW() - INTERVAL '7 days') AS sent_avg_7d,
  STDDEV(v.sentiment_score) FILTER (WHERE v.collected_at > NOW() - INTERVAL '7 days') AS sent_std_7d,
  MAX(v.collected_at) AS last_seen,
  EXTRACT(EPOCH FROM (NOW() - MAX(v.collected_at)))/3600 AS hours_since_last
FROM platforms p LEFT JOIN voc_records v ON v.platform_id = p.id
GROUP BY p.id, p.code, p.name, p.region;
```

`hours_since_last > 48` 이고 `is_active=true`면 **장애 의심**. 알림 큐로 발사.

### Early-signal lag 계산 (사이트 × 카테고리 × 제품)
이슈 정의: `(product_id, category)` 에 대해 어떤 7일창에서 일평균 대비 z-score>2 의 daily spike 발생 → 그 spike 이전 72h 동안 가장 먼저 같은 (product, category) 글이 등장한 platform 추출.

### 사이트 클러스터링 feature vector
사이트당 12차원: 각 category에 대한 `(sentiment_score 평균 − 전역 평균)` 편차. KMeans(k=5~7), scikit-learn, nightly batch로 `platform_clusters(platform_id, cluster_id, vector JSONB)` 적재.

## 3. 시각화 명세

### 3-1. Health Table (메인 상단)
| platform | region | 24h | 7d | avg_len | comment_ratio | sent_avg | last_seen | status |
정렬·필터 가능. status 칩: `active / quiet / stale(>48h) / dead(>7d)`. quiet=노랑, stale=주황, dead=빨강.

### 3-2. Platform × Product Heatmap (핵심)
- X: 제품 48개(그룹: Galaxy / Apple / Google / Wearable)
- Y: 사이트 (region별 grouping, accordion 접힘)
- cell color: sentiment_avg (−1 빨강 → 0 회색 → +1 파랑, diverging)
- cell size: log(count+1) — 빈도 약한 셀은 작은 점으로 표시
- hover: count, sent_avg ± std, top 1 category

```
              S25 S25+ S25U  iP16 iP16P  Pix9 Pix9P  ...
[KR ▼]
 dcinside     ██  ▓▓  ░░    ▒▒  ▓▓    ··   ··
 clien        ██  ██  ██    ▓▓  ▓▓    ··   ··
 ppomppu      ██  ▒▒  ░░    ··  ··    ··   ··
[US ▼]
 reddit_anr   ██  ██  ██    ▒▒  ▓▓    ██   ██
 9to5google   ··  ··  ··    ··  ··    ██   ██
[CN ▼]
 weibo        ██  ██  ▓▓    ▒▒  ░░    ··   ··
 ██ = sent>0.3  ▓▓ = 0~0.3  ▒▒ = -0.3~0  ░░ = <-0.3  ·· = n<5
```

### 3-3. Sentiment Dispersion Per Product (드릴다운)
제품 1개 선택 시 → boxplot(사이트별), x축=사이트(중앙값 정렬), y축=sentiment. 빨간 outlier point가 표시. 핵심 인사이트: "Galaxy S25 — dcinside median −0.4 vs reddit_android +0.3" 같은 극단 분리.

### 3-4. Early-signal Lag Chart
이벤트 1건 선택 → 가로 timeline. 사이트 dot이 시간순으로 좌→우. 가장 왼쪽 사이트가 "first mover". hover로 첫 글 link.

### 3-5. Cluster Scatter
PCA 2D + KMeans 색. 가까운 점=비슷한 sentiment 패턴. 예: 한국 갤럭시 커뮤니티 클러스터 vs 글로벌 애플 클러스터.

## 4. API Endpoint 설계

모든 endpoint `/api/v1/community/` prefix.

### GET /platforms/health
응답:
```json
{
  "as_of": "2026-05-31T12:00:00Z",
  "platforms": [
    {
      "platform_id": 12, "code": "dcinside", "name": "디시인사이드",
      "region": "KR", "posts_24h": 412, "posts_7d": 2810,
      "avg_len_7d": 187.2, "comment_ratio": 0.73,
      "sent_avg_7d": -0.12, "sent_std_7d": 0.41,
      "hours_since_last": 0.4, "status": "active"
    }
  ]
}
```

### GET /platforms/product-matrix?since=7d&products=1,2,3
```json
{
  "products": [{"id":1,"name":"Galaxy S25"}],
  "platforms": [{"id":12,"code":"dcinside","region":"KR"}],
  "cells": [
    {"platform_id":12,"product_id":1,"count":420,"sent_avg":-0.18,"sent_std":0.39,"top_category":"battery"}
  ]
}
```

### GET /platforms/dispersion?product_id=1&since=30d
박스플롯용 5수치(min/q1/median/q3/max) + outlier 샘플 5건.

### GET /platforms/early-signal?product_id=1&category=battery
```json
{
  "event": {"product_id":1,"category":"battery","spike_at":"2026-05-28T03:00Z","z":3.4},
  "timeline": [
    {"platform_code":"dcinside","first_post_at":"2026-05-27T08:11Z","lag_hours":-18.8,"sample_url":"..."},
    {"platform_code":"reddit_android","first_post_at":"2026-05-27T22:30Z","lag_hours":-4.5,"sample_url":"..."}
  ]
}
```

### GET /platforms/clusters?k=6
PCA 2D + cluster_id + centroid 카테고리 프로파일.

### GET /platforms/anomalies (장애 의심 자동 목록)
`hours_since_last>48 AND is_active=true` 필터.

## 5. 프론트 컴포넌트 구조

```
pages/CommunityAnalysis.tsx
├─ <FilterBar/>            (기간, 제품, 카테고리, region multi-select)
├─ <Tabs>
│   ├─ "Health"
│   │   ├─ <HealthTable/>        (AntD Table + status Tag)
│   │   └─ <AnomalyAlert/>       (장애 의심 사이트 카드)
│   ├─ "Matrix"
│   │   ├─ <PlatformProductHeatmap/>   (ECharts heatmap + group fold)
│   │   └─ <CellDrawer/>               (hover/click → 상세)
│   ├─ "Dispersion"
│   │   ├─ <ProductPicker/>
│   │   └─ <SentimentBoxplot/>         (ECharts boxplot)
│   ├─ "Early Signal"
│   │   ├─ <EventList/>                (z-score 정렬)
│   │   └─ <LagTimeline/>              (수평 timeline)
│   └─ "Clusters"
│       ├─ <ClusterScatter/>           (ECharts scatter)
│       └─ <ClusterProfile/>           (radar chart per cluster)
└─ <ExportBar/>            (CSV/PNG)
```

상태: Zustand `communityFilters` 전역. URL query-sync (공유 가능).

## 6. 인터랙션 흐름

1. 진입 → Health 탭 default. 빨간 status 칩 클릭 → AnomalyAlert로 점프.
2. Matrix 탭 → 셀 클릭 → CellDrawer 열림: 해당 platform×product VOC 목록 top 20, 링크 외부 이동.
3. Dispersion 탭 → 박스플롯 outlier 점 클릭 → VOC 원문 모달.
4. Early Signal → 이벤트 클릭 → LagTimeline → first mover 사이트 클릭 → Health 탭 해당 행 하이라이트(cross-link).
5. Clusters → 점 클릭 → 같은 cluster 사이트 list + Matrix 탭으로 필터 전송.

## 7. 단계적 구현

### Phase 1 — MVP (1주)
- `mv_platform_health` + `/health`, `/product-matrix` 2개 endpoint
- HealthTable, PlatformProductHeatmap 2개 컴포넌트
- 인덱스 2개 추가
- 검증: 60개 사이트가 모두 한 화면에서 비교됨

### Phase 2 — 강화 (1~2주)
- Dispersion boxplot + 원문 모달
- AnomalyAlert + Celery beat로 매시간 stale 점검
- region/언어/플랫폼 타입 그룹 집계 토글
- URL query-sync, CSV export

### Phase 3 — 고도화 (2~3주)
- Early-signal lag 분석 batch (nightly)
- KMeans 클러스터링 + PCA scatter + radar
- 사이트 bias 보정 모델: 글로벌 평균 대비 사이트별 sentiment offset 추정 → "보정된 sentiment" 옵션 토글
- Slack 알림 (장애 + early signal 이벤트)

## 8. 트레이드오프와 한계

- **표본 불균형**: dcinside·reddit는 천 단위, 일부 사이트는 두 자릿수. 매트릭스 cell의 sent_avg를 그대로 비교하면 노이즈가 큼 → 최소 n=5 미만은 점(··) 처리하고 신뢰구간 표기.
- **published_at 누락**: 일부 크롤러가 collected_at만 채움. early-signal lag에서 collected_at은 크롤 주기에 종속되므로 lag가 왜곡된다. 사이트별 크롤 주기 메타데이터(`platforms.crawl_interval_min`)를 추가해 보정해야 함.
- **언어 편향**: 한국어 sentiment 모델과 영어/중국어 모델의 calibration이 다르면 dispersion 차이가 모델 차이일 수 있음. 우선 단일 다국어 모델로 통일하고, 사이트별 offset 보정으로 완화.
- **클러스터 해석성**: KMeans 결과는 k 선택에 민감. silhouette 자동 + 사용자가 k 조절 가능한 UI 슬라이더 노출.
- **성능**: 매트릭스 셀이 60×48=2880개. heatmap 자체는 가볍지만 hover detail이 매 셀 API 호출이면 부담 → matrix endpoint 한 번에 full payload 반환 + 클라 캐시.

## 9. 검증 기준

1. **포괄성**: 활성 60개 사이트 중 95% 이상이 Health 표에 status≠"dead"로 표시되고, 24h posts 합계가 다른 모니터링 페이지의 24h 신규 13k와 ±5% 일치한다.
2. **분산 발견**: 임의 인기 제품 5개에서 Dispersion 탭이 사이트 간 median sentiment 차이 ≥0.4 인 쌍을 최소 1쌍 자동 강조한다 (수동 검수로도 같은 결론).
3. **Early-signal 재현**: 과거 알려진 이슈 3건(예: Galaxy S24 발열, iPhone 16 Pro 디스플레이 결함)에 대해 first-mover로 식별된 사이트가 실제 외부 보도 timeline보다 ≥6시간 앞선다.
