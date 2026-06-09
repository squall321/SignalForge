# API

FastAPI 백엔드 엔드포인트 명세. 진입점: [[backend/app/main.py#app]]

## Products Endpoints

Source: [[backend/app/api/products.py]]

`GET /api/v1/products` — 제품 목록. `?series=GS&is_active=true` 필터 가능.

`GET /api/v1/products/{code}/voc` — 제품별 VOC 목록. 아래 쿼리 파라미터 적용.

`GET /api/v1/products/{code}/stats` — 제품별 통계 요약 (총 건수, 긍정/부정 비율, 평균 감성 점수).

## Analytics Endpoints

Source: [[backend/app/api/analytics.py]]

`GET /api/v1/analytics/sentiment-trend` — 감성 트렌드 시계열. `granularity=day|week|month`

`GET /api/v1/analytics/category-dist` — 카테고리 분포 (도넛 차트용). [[categories]] 참조.

`GET /api/v1/analytics/country-heatmap` — 국가별 VOC 건수 + 감성. 세계 히트맵용.

`GET /api/v1/analytics/top-issues` — 상위 이슈 랭킹 + 부정 VOC 샘플 텍스트 3건.

`GET /api/v1/analytics/compare` — 제품 간 카테고리별 감성 점수 비교 (레이더 차트용).

## Crawl Jobs Endpoints

Source: [[backend/app/api/crawl_jobs.py]]

`GET /api/v1/crawl-jobs` — 작업 이력 최신 N건.

`POST /api/v1/crawl-jobs/trigger` — 수동 크롤링 트리거. `{platform_code, product_code}` 바디.

## WebSocket

Source: [[backend/app/api/websocket.py#manager]]

`WS /ws/realtime` — 신규 VOC 실시간 스트림. 클라이언트가 `ping` 전송 → 서버 `pong` 응답.
신규 VOC 수집 시 `{"type": "new_voc", "data": {...}}` 형태로 브로드캐스트.

## Query Params

공통 필터 파라미터 (VOC 목록 조회 시 사용):

- `?product=GS25U` — 제품 코드
- `?series=GS` — 시리즈 코드
- `?country=KR,US` — 국가 코드 (콤마 구분)
- `?platform=reddit,amazon` — 플랫폼 (콤마 구분)
- `?sentiment=negative` — 감성 필터
- `?category=battery` — 카테고리 필터. [[categories]] 코드 사용.
- `?from=2026-01-01&to=2026-05-15` — 기간 필터
- `?limit=50&offset=0` — 페이지네이션

## Response Caching

Analytics API는 Redis 캐시를 적용해 응답 속도 200ms 이하를 목표로 한다.

> **규칙:** Analytics API는 Redis로 캐싱한다. 목표 응답시간 < 200ms.
> 캐시 키 형식: `analytics:{endpoint}:{product_code}:{period_days}`
