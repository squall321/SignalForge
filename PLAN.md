# SignalForge — Samsung MobileExperience VOC Intelligence Platform
> 계획서 v1.0 | 2026-05-14

---

## 1. 프로젝트 개요

삼성전자 MobileExperience 전 제품군에 대한 글로벌 시장 반응(VOC)을 자동 수집·분류·분석하고,
LLM이 MCP를 통해 데이터에 "질문"하여 개발 개선 방향을 도출하는 통합 인텔리전스 플랫폼.

### 1.1 핵심 목표

| 목표 | 설명 |
|------|------|
| 자동 수집 | 7개 플랫폼에서 주기적으로 VOC 크롤링 |
| 표준화 | 플랫폼/언어 무관하게 통일된 VOC 포맷으로 정규화 |
| 가시화 | 제품군·국가·시간축별 반응을 대시보드로 시각화 |
| AI 소통 | MCP 서버를 통해 LLM이 VOC 데이터베이스와 직접 대화 |
| 개선 도출 | "이 제품의 가장 큰 불만은 무엇인가?" → 개발 개선 방향 제시 |

### 1.2 대상 제품군

| 코드 | 제품군 | 예시 모델 |
|------|--------|-----------|
| `GS` | Galaxy S 시리즈 | S25, S25+, S25 Ultra |
| `GZ` | Galaxy Z 시리즈 | Z Fold7, Z Flip7 |
| `GA` | Galaxy A/FE 시리즈 | A56, FE25 |
| `GW` | Galaxy Watch | Watch8, Watch Ultra |
| `GB` | Galaxy Buds | Buds3, Buds3 Pro |
| `GR` | Galaxy Ring | Ring2 |

---

## 2. 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                        SIGNALFORGE                              │
│                                                                 │
│  ┌────────────────┐    ┌──────────────────────────────────┐    │
│  │  CRAWLER LAYER │    │         BACKEND API              │    │
│  │                │    │         (FastAPI)                │    │
│  │ ┌────────────┐ │    │                                  │    │
│  │ │  Reddit    │ │    │  /api/v1/voc                     │    │
│  │ │  Twitter   │ │────┼─ /api/v1/products                │    │
│  │ │  Amazon    │ │    │  /api/v1/analytics               │    │
│  │ │  Best Buy  │ │    │  /api/v1/crawl-jobs              │    │
│  │ │  Clien     │ │    │  /ws/realtime                    │    │
│  │ │  ppomppu   │ │    │                                  │    │
│  │ │  XDA       │ │    └──────────┬───────────────────────┘    │
│  │ └────────────┘ │               │                            │
│  │      │         │    ┌──────────▼───────────────────────┐    │
│  │  Celery Beat   │    │       PostgreSQL                  │    │
│  │  (스케줄러)    │    │                                  │    │
│  │      │         │    │  voc_records / products          │    │
│  │  Redis Queue   │    │  platforms / crawl_jobs          │    │
│  └──────┼─────────┘    │  sentiment / categories          │    │
│         │              └──────────────────────────────────┘    │
│  ┌──────▼─────────────────────────────────────────────────┐    │
│  │              NLP PIPELINE                               │    │
│  │   번역(DeepL/LibreTranslate) → 감성분석 → 카테고리분류  │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌──────────────────┐    ┌───────────────────────────────┐     │
│  │   MCP SERVER     │    │     FRONTEND DASHBOARD        │     │
│  │   (Python MCP)   │    │  React + TS + Vite + AntD     │     │
│  │                  │    │                               │     │
│  │  Tools:          │    │  - 제품별 VOC 현황            │     │
│  │  - query_voc     │    │  - 국가별 히트맵              │     │
│  │  - top_issues    │    │  - 감성 트렌드 차트           │     │
│  │  - trend_analysis│    │  - 카테고리 분포              │     │
│  │  - suggest_fix   │    │  - MCP Chat 인터페이스        │     │
│  └──────────────────┘    └───────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 기술 스택

### 3.1 백엔드

| 구성 요소 | 기술 | 선택 이유 |
|-----------|------|-----------|
| API 서버 | **FastAPI 0.115+** | async 지원, 자동 OpenAPI 문서, Pydantic 검증 |
| 크롤러 | **Playwright (async)** | JS 렌더링 페이지 처리, 봇 감지 우회 |
| 크롤러 보조 | **httpx + BeautifulSoup** | 정적 페이지 고속 처리 |
| 태스크 큐 | **Celery 5 + Redis** | 분산 크롤링, 스케줄링 |
| ORM | **SQLAlchemy 2 (async)** | asyncpg 드라이버와 연동 |
| DB 마이그레이션 | **Alembic** | 스키마 버전 관리 |
| NLP | **langdetect + deep-translator** | 언어 감지 및 번역 |
| 감성 분석 | **VADER + Claude API** | 룰 기반 빠른 처리 + LLM 심층 분석 |
| MCP | **mcp SDK (Python)** | FastMCP 기반 MCP 서버 |

### 3.2 데이터베이스

| 구성 요소 | 기술 | 용도 |
|-----------|------|------|
| 주 DB | **PostgreSQL 16** | VOC 데이터 영구 저장 |
| 캐시/큐 | **Redis 7** | Celery 브로커, API 캐시 |
| 검색 | **PostgreSQL FTS (pg_trgm)** | 키워드 검색 |

### 3.3 프론트엔드

| 구성 요소 | 기술 |
|-----------|------|
| 빌드 | React 18 + TypeScript + Vite |
| UI | **Ant Design 5** |
| 차트 | **Ant Design Charts (G2/G6)** |
| 상태관리 | **Zustand** |
| API 통신 | **TanStack Query v5** |
| 지도 | **React Simple Maps** (국가별 히트맵) |

### 3.4 인프라

| 구성 요소 | 기술 |
|-----------|------|
| 컨테이너 | Docker + Docker Compose |
| 역방향 프록시 | Nginx |
| 환경 관리 | python-dotenv |

---

## 4. 데이터베이스 스키마

### 4.1 ERD 개요

```
products ──< voc_records >── platforms
   │              │
   │         sentiment_scores
   │              │
   └──< product_versions    voc_categories
```

### 4.2 상세 스키마

```sql
-- 제품 마스터
CREATE TABLE products (
    id          SERIAL PRIMARY KEY,
    code        VARCHAR(10) UNIQUE NOT NULL,   -- 'GS25U' (Galaxy S25 Ultra)
    series_code VARCHAR(4)  NOT NULL,          -- 'GS', 'GZ', 'GA', 'GW', 'GB', 'GR'
    name_en     VARCHAR(100) NOT NULL,
    name_ko     VARCHAR(100),
    released_at DATE,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 크롤링 소스 플랫폼
CREATE TABLE platforms (
    id           SERIAL PRIMARY KEY,
    code         VARCHAR(30) UNIQUE NOT NULL,  -- 'reddit', 'twitter', 'amazon', 'clien'
    name         VARCHAR(100) NOT NULL,
    region       VARCHAR(10),                  -- 'KR', 'US', 'GLOBAL'
    base_url     VARCHAR(255),
    is_active    BOOLEAN DEFAULT TRUE
);

-- VOC 핵심 레코드 (표준화 포맷)
CREATE TABLE voc_records (
    id                  BIGSERIAL PRIMARY KEY,
    product_id          INT REFERENCES products(id),
    platform_id         INT REFERENCES platforms(id),
    
    -- 원본 정보
    external_id         VARCHAR(200),          -- 플랫폼 내 원본 ID
    source_url          VARCHAR(1000),
    author_name         VARCHAR(200),
    
    -- 콘텐츠
    content_original    TEXT NOT NULL,
    content_translated  TEXT,                  -- 영어 번역본
    language_detected   VARCHAR(10),           -- 'ko', 'en', 'zh', 'de' ...
    country_code        VARCHAR(5),            -- 'KR', 'US', 'DE', 'CN' ...
    
    -- 감성 분석
    sentiment_score     FLOAT,                 -- -1.0 (부정) ~ 1.0 (긍정)
    sentiment_label     VARCHAR(20),           -- 'positive', 'negative', 'neutral'
    
    -- 이슈 카테고리 (복수 선택)
    categories          VARCHAR(30)[],         -- ['battery', 'camera', 'software', ...]
    
    -- 참여도 지표
    likes_count         INT DEFAULT 0,
    comments_count      INT DEFAULT 0,
    shares_count        INT DEFAULT 0,
    engagement_score    FLOAT,                 -- 정규화된 복합 점수
    
    -- 메타
    published_at        TIMESTAMPTZ,
    collected_at        TIMESTAMPTZ DEFAULT NOW(),
    processed_at        TIMESTAMPTZ,
    
    UNIQUE (platform_id, external_id)
);

-- VOC 카테고리 정의
CREATE TABLE voc_categories (
    code        VARCHAR(30) PRIMARY KEY,
    name_en     VARCHAR(100),
    name_ko     VARCHAR(100),
    keywords    TEXT[]                         -- 자동 분류 키워드 목록
);

-- 크롤링 작업 로그
CREATE TABLE crawl_jobs (
    id              BIGSERIAL PRIMARY KEY,
    platform_id     INT REFERENCES platforms(id),
    product_id      INT REFERENCES products(id),
    status          VARCHAR(20),               -- 'running', 'done', 'failed'
    items_collected INT DEFAULT 0,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    error_message   TEXT
);

-- 성능 인덱스
CREATE INDEX idx_voc_product    ON voc_records(product_id, collected_at DESC);
CREATE INDEX idx_voc_country    ON voc_records(country_code, product_id);
CREATE INDEX idx_voc_sentiment  ON voc_records(sentiment_label, product_id);
CREATE INDEX idx_voc_categories ON voc_records USING GIN(categories);
CREATE INDEX idx_voc_content_fts ON voc_records USING GIN(to_tsvector('english', content_translated));
```

### 4.3 VOC 카테고리 표준 코드

| 코드 | 카테고리 | 주요 키워드 |
|------|----------|-------------|
| `battery` | 배터리/충전 | battery life, drain, charging, 배터리, 충전 |
| `camera` | 카메라/촬영 | camera, photo, zoom, night mode, 카메라 |
| `display` | 디스플레이 | screen, display, brightness, AMOLED, 화면 |
| `performance` | 성능/발열 | lag, slow, heating, fps, 발열, 버벅 |
| `software` | 소프트웨어/UI | OneUI, update, bug, crash, 업데이트 |
| `build_quality` | 내구성/품질 | crack, scratch, build, hinge, 힌지, 파손 |
| `price` | 가격/가성비 | price, expensive, value, 가격, 비싸 |
| `design` | 디자인/형태 | design, color, form factor, 디자인 |
| `connectivity` | 연결성 | wifi, bluetooth, 5G, signal, 연결 |
| `ai_features` | AI 기능 | AI, Galaxy AI, Circle to Search, 갤럭시 AI |
| `accessories` | 액세서리/호환 | case, cover, pen, S Pen, 케이스 |
| `comparison` | 경쟁사 비교 | Apple, iPhone, Pixel, vs, 비교 |

---

## 5. 크롤러 설계

### 5.1 플랫폼별 크롤링 전략

| 플랫폼 | 방식 | 수집 주기 | 수집 단위 |
|--------|------|-----------|-----------|
| Reddit | PRAW API + Playwright fallback | 매 1시간 | 신규 포스트/댓글 |
| Twitter(X) | Playwright 스크래핑 | 매 2시간 | 최근 24h 트윗 |
| Amazon | httpx + BS4 | 매 6시간 | 제품별 최신 리뷰 |
| Best Buy | Playwright | 매 6시간 | 제품별 최신 리뷰 |
| Clien | httpx + BS4 | 매 2시간 | 모바일/제품 게시판 |
| ppomppu | httpx + BS4 | 매 2시간 | 휴대폰 게시판 |
| Naver Cafe | Playwright | 매 4시간 | 삼성 공식 카페 |
| XDA | httpx + BS4 | 매 4시간 | 포럼 스레드 |
| 9to5Google | httpx + BS4 | 매 6시간 | 기사 댓글 |

### 5.2 크롤러 공통 아키텍처

```
BaseCrawler (ABC)
├── crawl()           → 플랫폼별 구현
├── parse()           → HTML/JSON → RawVOC
├── normalize()       → RawVOC → StandardVOC
└── save()            → DB 저장 (중복 체크)

RedditCrawler(BaseCrawler)
AmazonCrawler(BaseCrawler)
ClienCrawler(BaseCrawler)
...
```

### 5.3 VOC 처리 파이프라인

```
수집(Raw) → 언어감지 → 번역(비영어) → 감성분석 → 카테고리분류 → DB저장
    │              │           │              │             │
 Playwright    langdetect  deep-translator   VADER      키워드매칭
                                          + Claude API  + Claude API
```

### 5.4 봇 감지 우회 전략

- **User-Agent 로테이션**: 실제 브라우저 UA 풀 사용
- **요청 간격**: 플랫폼별 1~5초 랜덤 딜레이
- **Playwright Stealth**: `playwright-stealth` 플러그인
- **프록시**: 선택적 프록시 풀 지원 (환경변수로 설정)
- **쿠키 세션 유지**: 플랫폼 로그인 세션 캐싱

---

## 6. FastAPI 백엔드 API 설계

### 6.1 엔드포인트 목록

```
GET  /api/v1/products                    제품 목록
GET  /api/v1/products/{code}/voc         제품별 VOC 목록 (필터/페이지네이션)
GET  /api/v1/products/{code}/stats       제품별 통계 요약

GET  /api/v1/analytics/sentiment-trend   감성 트렌드 시계열
GET  /api/v1/analytics/category-dist     카테고리 분포
GET  /api/v1/analytics/country-heatmap   국가별 VOC 건수 히트맵
GET  /api/v1/analytics/top-issues        상위 이슈 랭킹
GET  /api/v1/analytics/compare           제품 간 비교

GET  /api/v1/platforms                   크롤링 소스 목록
POST /api/v1/crawl-jobs/trigger          수동 크롤링 트리거
GET  /api/v1/crawl-jobs                  크롤링 작업 이력

WS   /ws/realtime                        실시간 신규 VOC 스트림
```

### 6.2 공통 쿼리 파라미터

```
?product=GS25U           제품 코드 필터
?series=GS               시리즈 코드 필터
?country=KR,US           국가 코드 (다중)
?platform=reddit,amazon  플랫폼 (다중)
?sentiment=negative      감성 필터
?category=battery        카테고리 필터
?from=2026-01-01         기간 시작
?to=2026-05-14           기간 종료
?limit=50&offset=0       페이지네이션
?lang=ko                 원문 언어 필터
```

---

## 7. MCP 서버 설계

### 7.1 MCP Tools 목록

```python
@mcp.tool()
async def query_voc(
    product_code: str,
    country: str = None,
    category: str = None,
    sentiment: str = None,
    limit: int = 20
) -> list[VOCRecord]:
    """특정 제품의 VOC를 조건별로 조회"""

@mcp.tool()
async def get_top_issues(
    product_code: str,
    period_days: int = 30,
    top_n: int = 10
) -> list[IssueRanking]:
    """지난 N일간 가장 많이 언급된 이슈 TOP N"""

@mcp.tool()
async def analyze_sentiment_trend(
    product_code: str,
    period_days: int = 90,
    granularity: str = "week"
) -> SentimentTrend:
    """감성 점수 시계열 트렌드"""

@mcp.tool()
async def compare_products(
    product_codes: list[str],
    category: str = None
) -> ComparisonResult:
    """제품 간 VOC 비교"""

@mcp.tool()
async def get_country_breakdown(
    product_code: str,
    period_days: int = 30
) -> list[CountryVOC]:
    """국가별 VOC 건수 및 감성 분포"""

@mcp.tool()
async def get_voc_summary(
    product_code: str,
    period_days: int = 7
) -> str:
    """주간 VOC 요약 텍스트 (LLM이 분석 기반으로 요약)"""

@mcp.tool()
async def search_voc(
    keyword: str,
    product_code: str = None,
    limit: int = 30
) -> list[VOCRecord]:
    """키워드로 VOC 전문 검색"""
```

### 7.2 MCP 사용 예시 (Claude Desktop / API)

```
사용자: "갤럭시 Z Fold7의 힌지 관련 불만이 많아?"

Claude → MCP Tool 호출:
  query_voc(product_code="GZF7", category="build_quality", sentiment="negative")
  get_top_issues(product_code="GZF7", period_days=30)

결과 분석 후 응답:
  "지난 30일간 Z Fold7의 부정 VOC 중 38%가 힌지 관련입니다.
   주요 불만: 힌지 소음(42%), 주름(31%), 내구성 우려(27%)
   개선 제안: ..."
```

---

## 8. 프론트엔드 대시보드 설계

### 8.1 페이지 구성

```
/                       메인 대시보드 (전체 요약)
/products               제품군 목록
/products/:code         제품 상세 VOC 뷰
/analytics/sentiment    감성 트렌드 분석
/analytics/heatmap      국가별 히트맵
/analytics/compare      제품 비교
/voc                    VOC 전체 목록 (검색/필터)
/crawl-jobs             크롤링 현황 모니터링
/mcp-chat               MCP 기반 AI 대화 인터페이스
```

### 8.2 주요 컴포넌트

| 컴포넌트 | 차트 유형 | 라이브러리 |
|----------|-----------|------------|
| SentimentTrendChart | 영역 차트 (시계열) | AntD Charts Line |
| CategoryPieChart | 도넛 차트 | AntD Charts Pie |
| CountryHeatMap | 세계 지도 히트맵 | react-simple-maps |
| VOCTable | 가상화 테이블 | AntD Table |
| ProductCompareChart | 레이더 차트 | AntD Charts Radar |
| RealTimeVOCFeed | 실시간 피드 | WebSocket + AntD List |
| MCPChatPanel | 채팅 인터페이스 | 커스텀 컴포넌트 |

### 8.3 대시보드 레이아웃 (메인)

```
┌─────────────────────────────────────────────────────┐
│ [Logo] SignalForge         [제품선택▼] [기간▼] [새로고침]  │
├──────────┬──────────┬──────────┬────────────────────┤
│ 총 VOC   │ 긍정률   │ 부정률   │ 오늘 수집           │
│ 284,521  │ 42.3%    │ 31.7%    │ +1,284              │
├──────────┴──────────┴──────────┴────────────────────┤
│                감성 트렌드 (90일)                    │
│  ████████████████████████████████████████           │
├──────────────────────┬──────────────────────────────┤
│   카테고리 분포      │      국가별 히트맵             │
│   ●battery 28%       │   [세계 지도]                 │
│   ●camera  21%       │                              │
│   ●software 18%      │                              │
├──────────────────────┴──────────────────────────────┤
│              최신 VOC 피드 (실시간)                  │
│  [Reddit] GS25U | ★★★☆☆ | "camera is amazing but.." │
│  [Amazon] GZF7  | ★★☆☆☆ | "hinge creak after..."   │
└─────────────────────────────────────────────────────┘
```

---

## 9. 디렉토리 구조

```
SignalForge/
├── docker-compose.yml
├── .env.example
│
├── backend/                          # FastAPI 백엔드
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic/                      # DB 마이그레이션
│   │   └── versions/
│   ├── app/
│   │   ├── main.py                   # FastAPI 앱 진입점
│   │   ├── config.py                 # 설정 (env 로딩)
│   │   ├── database.py               # DB 연결 (async SQLAlchemy)
│   │   ├── models/                   # SQLAlchemy 모델
│   │   │   ├── product.py
│   │   │   ├── voc.py
│   │   │   ├── platform.py
│   │   │   └── crawl_job.py
│   │   ├── schemas/                  # Pydantic 스키마
│   │   │   ├── voc.py
│   │   │   └── analytics.py
│   │   ├── api/                      # 라우터
│   │   │   ├── products.py
│   │   │   ├── analytics.py
│   │   │   ├── crawl_jobs.py
│   │   │   └── websocket.py
│   │   ├── services/                 # 비즈니스 로직
│   │   │   ├── voc_service.py
│   │   │   ├── analytics_service.py
│   │   │   └── nlp_service.py
│   │   └── core/
│   │       ├── security.py
│   │       └── exceptions.py
│   │
├── crawler/                          # 크롤러 (Celery Worker)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── celery_app.py                 # Celery 앱 설정
│   ├── tasks.py                      # 크롤링 태스크
│   ├── base/
│   │   ├── crawler.py                # BaseCrawler
│   │   └── normalizer.py             # VOC 정규화
│   ├── platforms/
│   │   ├── reddit.py
│   │   ├── twitter.py
│   │   ├── amazon.py
│   │   ├── bestbuy.py
│   │   ├── clien.py
│   │   ├── ppomppu.py
│   │   ├── naver_cafe.py
│   │   └── xda.py
│   └── nlp/
│       ├── detector.py               # 언어 감지
│       ├── translator.py             # 번역
│       ├── sentiment.py              # 감성 분석
│       └── categorizer.py            # 카테고리 분류
│
├── mcp-server/                       # MCP 서버
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── server.py                     # FastMCP 서버
│   └── tools/
│       ├── query.py
│       ├── analytics.py
│       └── search.py
│
└── frontend/                         # React 대시보드
    ├── Dockerfile
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── components/
        │   ├── charts/
        │   ├── voc/
        │   └── layout/
        ├── pages/
        │   ├── Dashboard.tsx
        │   ├── ProductDetail.tsx
        │   ├── Analytics.tsx
        │   ├── VOCList.tsx
        │   └── MCPChat.tsx
        ├── stores/
        │   └── filterStore.ts        # Zustand 필터 상태
        ├── hooks/
        │   └── useVOC.ts             # TanStack Query 훅
        ├── services/
        │   └── api.ts                # API 클라이언트
        └── types/
            └── index.ts
```

---

## 10. 개발 단계 (Phase Plan)

### Phase 1 — 인프라 기초 (3일)

| 작업 | 상세 | 완료 기준 |
|------|------|-----------|
| 1-1 | Docker Compose 구성 (PostgreSQL, Redis, FastAPI, Celery, Nginx) | `docker compose up` 정상 |
| 1-2 | DB 스키마 생성 + Alembic 마이그레이션 셋업 | 테이블 생성 확인 |
| 1-3 | 마스터 데이터 시딩 (제품, 플랫폼, 카테고리) | 시드 데이터 DB 입력 |
| 1-4 | FastAPI 스켈레톤 + 헬스체크 | `/health` 200 응답 |
| 1-5 | `.env.example` 및 환경변수 정의 | 문서화 완료 |

### Phase 2 — 크롤러 개발 (7일)

| 작업 | 상세 | 완료 기준 |
|------|------|-----------|
| 2-1 | BaseCrawler 추상 클래스 + VOC 정규화 포맷 | 단위 테스트 통과 |
| 2-2 | RedditCrawler (PRAW + Playwright) | 50건 이상 수집 확인 |
| 2-3 | AmazonCrawler + BestBuyCrawler | 리뷰 10건 이상 수집 |
| 2-4 | ClienCrawler + ppomppuCrawler | 한국어 VOC 수집 확인 |
| 2-5 | XDACrawler + 9to5GoogleCrawler | 수집 확인 |
| 2-6 | TwitterCrawler (Playwright 기반) | 트윗 수집 확인 |
| 2-7 | Celery Beat 스케줄러 설정 | 주기적 자동 실행 확인 |

### Phase 3 — NLP 파이프라인 (4일)

| 작업 | 상세 | 완료 기준 |
|------|------|-----------|
| 3-1 | 언어 감지 (langdetect) | 정확도 95%+ |
| 3-2 | 번역 (deep-translator / DeepL) | 한/중/독 → 영어 변환 |
| 3-3 | 감성 분석 (VADER + Claude API) | 점수 및 라벨 DB 저장 |
| 3-4 | 카테고리 분류 (키워드 매칭 + LLM) | 복수 카테고리 태깅 |
| 3-5 | 참여도 점수 계산 | engagement_score 정규화 |

### Phase 4 — 백엔드 API 완성 (4일)

| 작업 | 상세 | 완료 기준 |
|------|------|-----------|
| 4-1 | VOC CRUD + 필터링 API | Swagger 테스트 통과 |
| 4-2 | Analytics API (트렌드, 분포, 히트맵) | 집계 쿼리 응답 |
| 4-3 | WebSocket 실시간 스트림 | 신규 VOC 즉시 전송 |
| 4-4 | Redis 캐싱 레이어 | 집계 API 응답속도 <200ms |
| 4-5 | 크롤링 작업 수동 트리거 API | `/crawl-jobs/trigger` 작동 |

### Phase 5 — MCP 서버 (3일)

| 작업 | 상세 | 완료 기준 |
|------|------|-----------|
| 5-1 | FastMCP 서버 기본 구조 | MCP 클라이언트 연결 |
| 5-2 | query_voc, get_top_issues 도구 | Claude에서 호출 확인 |
| 5-3 | analyze_sentiment_trend, compare_products | 분석 결과 반환 |
| 5-4 | search_voc (FTS 기반) | 키워드 검색 정상 작동 |
| 5-5 | Claude Desktop MCP 설정 | 실제 대화 테스트 |

### Phase 6 — 프론트엔드 (7일)

| 작업 | 상세 | 완료 기준 |
|------|------|-----------|
| 6-1 | Vite + React + TS + AntD 프로젝트 셋업 | 빌드 성공 |
| 6-2 | 레이아웃 (Sider, Header, Content) | 반응형 구조 |
| 6-3 | 메인 대시보드 (KPI 카드 + 트렌드 차트) | 실데이터 렌더링 |
| 6-4 | 제품 상세 페이지 (VOC 목록 + 필터) | 필터링 동작 |
| 6-5 | 국가별 히트맵 | 지도 렌더링 |
| 6-6 | 제품 비교 레이더 차트 | 비교 동작 |
| 6-7 | 실시간 VOC 피드 (WebSocket) | 신규 VOC 즉시 표시 |
| 6-8 | MCP Chat 인터페이스 | 질의응답 UI |

### Phase 7 — 통합 테스트 및 안정화 (3일)

| 작업 | 상세 | 완료 기준 |
|------|------|-----------|
| 7-1 | 전체 E2E 플로우 검증 | 수집→분석→대시보드 확인 |
| 7-2 | 크롤러 안정성 테스트 | 24h 무중단 수집 |
| 7-3 | API 부하 테스트 | 100 RPS 이상 처리 |
| 7-4 | MCP 대화 품질 검증 | 개선 제안 품질 확인 |
| 7-5 | 보안 점검 (인증, Rate Limiting) | 무인증 접근 차단 |

**총 예상 기간: 31일 (영업일 기준)**

---

## 11. 환경변수 설계 (.env.example)

```bash
# Database
DATABASE_URL=postgresql+asyncpg://signalforge:password@postgres:5432/signalforge

# Redis
REDIS_URL=redis://redis:6379/0

# Claude API (NLP + MCP)
ANTHROPIC_API_KEY=sk-ant-...

# Translation (선택: DeepL API 또는 무료 LibreTranslate)
DEEPL_API_KEY=
LIBRE_TRANSLATE_URL=http://libretranslate:5000

# Reddit API (PRAW)
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=SignalForge/1.0

# Twitter (Playwright 기반이면 계정 필요)
TWITTER_USERNAME=
TWITTER_PASSWORD=

# Amazon (국가별 도메인)
AMAZON_REGIONS=US,DE,JP,KR

# Proxy (선택)
PROXY_URL=

# Frontend
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000

# Security
API_KEY=your-internal-api-key
CORS_ORIGINS=http://localhost:5173
```

---

## 12. Docker Compose 구성

```yaml
services:
  postgres:       # PostgreSQL 16
  redis:          # Redis 7
  backend:        # FastAPI (port 8000)
  celery-worker:  # Playwright 크롤러 워커
  celery-beat:    # 스케줄러
  mcp-server:     # MCP 서버 (stdio or HTTP)
  frontend:       # Vite 빌드 → Nginx 서빙 (port 3000)
  nginx:          # 역방향 프록시 (port 80)
```

---

## 13. 리스크 및 대응

| 리스크 | 확률 | 영향 | 대응 방안 |
|--------|------|------|-----------|
| 플랫폼 봇 감지/차단 | 높음 | 중간 | Stealth 플러그인, 딜레이 조정, 프록시 |
| Twitter API 제한 | 중간 | 중간 | Playwright 스크래핑으로 대체 |
| 번역 비용 과다 | 낮음 | 낮음 | 언어 우선순위 설정, 배치 처리 |
| 감성 분석 부정확 | 중간 | 중간 | VADER + Claude API 앙상블 |
| DB 성능 저하 (대용량) | 낮음 | 높음 | 인덱스 최적화, 파티셔닝 (월별) |
| 법적 이슈 (스크래핑) | 낮음 | 높음 | robots.txt 준수, 공개 데이터만 수집 |

---

## 14. 성공 지표 (KPI)

| 지표 | 목표 |
|------|------|
| 일 수집 VOC 건수 | 1,000건 이상 |
| 감성 분석 정확도 | 85% 이상 (수동 검증 기준) |
| 카테고리 분류 정확도 | 80% 이상 |
| API 평균 응답시간 | 200ms 이하 |
| 대시보드 로딩 | 2초 이하 |
| MCP 질의응답 | 10초 이하 |
| 크롤러 업타임 | 99% 이상 (24/7) |

---

## 15. 다음 단계

1. `Phase 1` 시작: `docker-compose.yml` 및 DB 스키마 작성
2. 우선 `RedditCrawler` + `AmazonCrawler` 로 MVP 빠르게 검증
3. NLP 파이프라인은 VADER 먼저, Claude API는 고품질 분석용으로 점진적 적용
4. 프론트엔드는 API 완성 후 병렬 개발

---

*SignalForge — "시장의 목소리를 데이터로, 데이터를 개선의 방향으로"*
