# T4. 전세계 국가별 분석 — 심층 설계

## 1. 목적 (Why)
60+ 플랫폼이 24+ 국가를 커버하는 SignalForge의 핵심 차별점은 **글로벌 다지역 VOC**이다. 단일 국가 분석으로는 보이지 않는 의사결정이 다음 3가지 있다.

- **지역별 출시 전략 차등화**: Z Fold7이 한국에서 sentiment +0.42인데 인도에서 -0.18이면 가격/AS 정책을 분리해야 한다.
- **확산 곡선 추적**: 신제품 언급이 어느 국가에서 먼저 폭발하고 어느 순서로 퍼지는지를 시간축 지도 애니메이션으로 본다. PM은 "다음 캠페인 우선 투입 국가"를 결정.
- **블라인드스팟 인식**: 미커버 국가(회색)는 데이터 부재 자체가 시그널 — 크롤러 우선순위 큐 후보.

## 2. 데이터 모델
필요한 컬럼: `country_code` (ISO 3166-1 alpha-2, voc_records 기존), `sentiment_score`, `published_at`, `product_id`, `platform_id`, `categories`, `language_detected`. 신규 메타 테이블 2개:

```sql
-- 국가 마스터 (대륙/권역 그룹핑)
CREATE TABLE country_meta (
  country_code CHAR(2) PRIMARY KEY,
  name_ko TEXT, name_en TEXT,
  continent TEXT,                -- Asia/Europe/...
  market_tier TEXT,              -- T1(KR/US/JP) / T2(EU5/IN/...) / T3
  iso3 CHAR(3),                  -- choropleth 매칭용
  population BIGINT,             -- 정규화 분모
  covered BOOLEAN DEFAULT FALSE  -- 크롤러 커버리지
);

-- Materialized View: 일자×국가×제품 집계
CREATE MATERIALIZED VIEW mv_country_daily AS
SELECT
  date_trunc('day', published_at)::date AS day,
  country_code, product_id,
  COUNT(*)                       AS voc_count,
  AVG(sentiment_score)::numeric(4,3) AS sent_avg,
  STDDEV(sentiment_score)::numeric(4,3) AS sent_std,
  SUM(likes_count + comments_count) AS engagement
FROM voc_records
WHERE country_code IS NOT NULL AND published_at >= NOW() - INTERVAL '180 days'
GROUP BY 1,2,3;

CREATE INDEX idx_mvcd_day_country ON mv_country_daily(day, country_code);
CREATE INDEX idx_mvcd_product ON mv_country_daily(product_id, day);
-- REFRESH MATERIALIZED VIEW CONCURRENTLY mv_country_daily; (Celery beat 1h)
```

문화/언어 noise 정규화: **z-score 정규화** — 국가별 sentiment 평균/표준편차로 표준화한 `sent_z = (sent_avg - μ_country) / σ_country` 를 별도 컬럼으로 노출. 일본 평균이 구조적으로 낮은(절제 표현) 효과를 흡수.

## 3. 시각화 명세
**차트 1: 세계 Choropleth** — d3-geo + TopoJSON. 색상은 sentiment(-1~+1)을 빨강→회색→파랑 발산 스케일(diverging), 채도는 voc_count log 스케일. 미커버 국가는 hatched pattern(빗금). 호버 시 툴팁: 국가명/건수/sent_avg/sent_z/Top3 사이트.

**차트 2: 동일 제품 국가별 horizontal bar** — Z Fold7 선택 시 국가별 sent_avg를 막대로, 막대 끝에 voc_count 라벨, 95% CI 오차막대.

**차트 3: 확산 애니메이션** — 시간 슬라이더(일 단위) + 지도 위 버블 크기=voc_count, 색=sentiment. 재생 버튼으로 D+0~D+30 재생.

```
 ┌─ Country Choropleth ──────────────────────────────────┐
 │           ╭────╮          ╭──╮                         │
 │      ╭─╮  │ US │  ╭─╮     │EU│  ╭─KR─╮  ╭JP╮          │
 │      │CA│ │ ██ │  │..│    │██│  │ ██ │  │██│   ★ tip   │
 │      ╰─╯  ╰────╯  ╰──╯    ╰──╯  ╰────╯  ╰──╯          │
 │  Legend:  -1 ████░░░░░ +1   ▒▒ 미커버   ● 건수=원크기 │
 └────────────────────────────────────────────────────────┘
 [▶ 2026-04-01 ──●───────── 2026-05-31]  Product: [Z Fold7▼]
```

라이브러리 비교 결론:
| 라이브러리 | choropleth | 줌/팬 | 번들 | 채택 |
|---|---|---|---|---|
| Mapbox GL | ◎ 벡터타일 | ◎ | 토큰필요/유료 | △ |
| Leaflet | ○ plugin | ○ | 38KB | △ |
| react-simple-maps (d3-geo) | ◎ | ○ | 22KB+TopoJSON | **○ 채택** |

이유: SVG 기반이라 AntD 테마/툴팁 통합 쉽고, 데이터셋이 국가 단위(245개 폴리곤)라 벡터타일 불필요.

## 4. API Endpoint
```
GET /api/analytics/country/choropleth?product_id=&date_from=&date_to=&metric=sentiment
GET /api/analytics/country/{code}/drilldown?date_from=&date_to=
GET /api/analytics/country/diffusion?product_id=&date_from=&granularity=day
GET /api/analytics/country/product-compare?product_id=&countries=KR,US,IN,JP
```

응답 예 (`/choropleth`):
```json
{
  "metric": "sentiment",
  "range": ["2026-05-01","2026-05-31"],
  "items": [
    {"iso2":"KR","iso3":"KOR","voc_count":4210,"sent_avg":0.41,"sent_z":1.8,"covered":true},
    {"iso2":"US","iso3":"USA","voc_count":3870,"sent_avg":0.12,"sent_z":0.3,"covered":true},
    {"iso2":"IN","iso3":"IND","voc_count":1205,"sent_avg":-0.18,"sent_z":-1.1,"covered":true},
    {"iso2":"BR","iso3":"BRA","voc_count":0,"sent_avg":null,"sent_z":null,"covered":false}
  ],
  "totals": {"countries_covered":24,"voc_total":18432}
}
```

`/diffusion` 응답: `[{"day":"2026-05-12","points":[{"iso2":"KR","voc":820,"sent":0.38},...]}, ...]` — 프론트는 일자별 프레임 재생.

## 5. 프론트 컴포넌트 구조
```
pages/CountryAnalysis/
  index.tsx                    # 레이아웃, useFilters() context
  components/
    FilterBar.tsx              # 기간/제품/메트릭/대륙 토글
    WorldChoropleth.tsx        # react-simple-maps + d3-scale
    LegendBar.tsx              # diverging scale
    CountryDrillPanel.tsx      # 우측 슬라이드 패널
      ├─ TopSitesList
      ├─ TopProductsList
      └─ TopCategoriesPie
    ProductCountryCompare.tsx  # horizontal bar (CI 포함)
    DiffusionPlayer.tsx        # 슬라이더+▶ 버튼
    CoverageGapsCard.tsx       # 미커버 국가 후보 리스트
hooks/
  useCountryChoropleth.ts
  useCountryDrilldown.ts
```

## 6. 인터랙션 흐름
1) 사용자가 FilterBar에서 기간/제품 선택 → choropleth 리로드.
2) 국가 폴리곤 클릭 → 우측 Drill 패널 슬라이드인 (TopSites/Products/Categories).
3) Drill의 사이트 클릭 → T2 커뮤니티 분석 페이지로 라우팅(`/community/:platform_id`).
4) DiffusionPlayer ▶ 클릭 → 일자별 30프레임 재생, 일시정지 시 해당 일자 스냅샷 유지.
5) CoverageGapsCard "검토 후보" 클릭 → 크롤러 우선순위 큐 등록 API 호출(`POST /crawler/queue`).

## 7. 단계적 구현
**MVP (1주)**: country_meta 시드(24+ 커버국가), mv_country_daily 생성, `/choropleth`·`/{code}/drilldown` 2개 endpoint, WorldChoropleth + Drill 패널.

**강화 (2주)**: z-score 정규화, ProductCountryCompare(95% CI), 대륙 그룹 토글, CoverageGapsCard, 미커버 hatched 패턴.

**고도화 (3주)**: DiffusionPlayer 애니메이션, 출시일 기준 D+N 정규화 축, 언어×문화 보정 모델(언어별 sentiment bias 회귀), 크롤러 우선순위 자동 추천.

## 8. 트레이드오프와 한계
- **country_code 정확도**: 플랫폼이 IP/도메인 추정 — VPN/디아스포라 트래픽 noise. 향후 `country_confidence` 컬럼 도입 필요.
- **sentiment cross-lingual 편향**: 모델이 영어 학습 편중이면 일본어/힌디어 sentiment underestimate. z-score 정규화로 1차 완화하나 근본 해결 아님.
- **소국 표본 부족**: voc_count<50인 국가는 sent_avg 분산 큼 → CI 표시 + 클릭 시 "표본 부족" 배지.
- **애니메이션 비용**: 30프레임×245폴리곤 재렌더 무거움 — canvas fallback 또는 변경 셀만 patch.
- **TopoJSON 정치적 경계**: 분쟁 지역(Kashmir, Crimea) 표시 정책 사내 합의 필요.

## 9. 검증 기준
1. **정확성**: `/choropleth` 응답 voc_total = 같은 기간 `SELECT COUNT(*) FROM voc_records WHERE country_code IS NOT NULL` 와 ±0.5% 일치.
2. **응답성**: 24개국 P95 응답 <600ms (mv_country_daily 인덱스 활용), Drill API P95 <800ms.
3. **유의성 검증**: 동일 제품의 KR vs US sent_avg 차이가 t-test p<0.05 일 때만 "유의한 격차" 배지 표시 — false positive 인사이트 차단.
