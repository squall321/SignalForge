# Data Model

SignalForge의 핵심 데이터 스키마. 마이그레이션 파일: [[backend/alembic/versions/0001_initial_schema.py]]

## Tables

5개 테이블로 구성. `products`·`platforms`가 마스터 데이터이고 `voc_records`가 핵심 트랜잭션 테이블.

### products

Galaxy 제품 마스터 테이블. `code`가 비즈니스 키 역할을 한다.

- `code` — 고유 식별자 (예: `GS25U`, `GZF7`). 모든 API 파라미터에서 이 값을 사용.
- `series_code` — 시리즈 분류 (`GS`, `GZ`, `GA`, `GW`, `GB`, `GR`). 자세한 내용: [[products]]
- `is_active` — 비활성 제품은 크롤링 및 API에서 제외.

Source: [[backend/app/models/product.py#Product]]

### platforms

크롤링 소스 플랫폼 마스터.

- `code` — 고유 식별자 (예: `reddit`, `amazon_us`, `clien`)
- `region` — `KR` | `US` | `GLOBAL`

Source: [[backend/app/models/platform.py#Platform]]

### voc_records

VOC의 핵심 레코드. 모든 분석의 기반이 되는 표준화된 포맷.

**중복 방지:** `(platform_id, external_id)` 복합 유니크 제약으로 동일 VOC 재수집을 막는다.

**NLP 컬럼:**

- `language_detected` — ISO 639-1 코드 (예: `ko`, `en`, `de`)
- `content_translated` — 영어 번역본. FTS 인덱스가 이 콼럼에 걸림.
- `sentiment_score` — `-1.0` (매우 부정) ~ `1.0` (매우 꺍정). VADER가 계산.
- `sentiment_label` — `positive` | `negative` | `neutral`
- `categories` — PostgreSQL `TEXT[]` 배열. [[categories]] 코드 목록 참조.
- `engagement_score` — 좋아요·댓글·공유를 log 스케일로 정규화한 0~100 점수.

**인덱스 전략:**

- `idx_voc_product` — `(product_id, collected_at)` → 제품별 최신 VOC 조회
- `idx_voc_categories` — GIN 인덱스 → `ANY(categories)` 빠른 검색
- `idx_voc_content_fts` — GIN FTS 인덱스 → 키워드 전문 검색

Source: [[backend/app/models/voc.py#VocRecord]]

### voc_categories

카테고리 코드 마스터. `keywords` 배열로 자동 분류에 사용. 자세한 내용: [[categories]]

Source: [[backend/app/models/voc.py#VocCategory]]

### crawl_jobs

크롤링 작업 이력. 상태: `pending` → `running` → `done` | `failed`

Source: [[backend/app/models/crawl_job.py#CrawlJob]]

## Key Constraints

코드 전반에서 지켜야 할 데이터 정합성 규칙.

> **비즈니스 규칙:** `voc_records.categories`는 NULL이 가능하다. NLP 처리 전에는 NULL이고, 처리 후에 배열로 채워진다. `processed_at`이 NULL이면 미처리 상태.
