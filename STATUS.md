# SignalForge — 현재 상태 보고서

> 작성일: 2026-05-16  
> 프로젝트: Samsung MobileExperience VOC Intelligence Platform

---

## 1. 전체 진행 현황

```
Phase 1 ████████████████████ 100%  인프라 기초
Phase 2 ████████████████████ 100%  크롤러 (15+ 소스 검증, KST·event-loop 결함 해소)
Phase 3 ███████████████░░░░░  75%  NLP (Korean-native 감성, throttle 안정화, LLM 고도화 잔여)
Phase 4 ██████████████░░░░░░  70%  백엔드 API (Analytics 5종 검증완료, WS/캐싱 잔여)
Phase 5 ████████████████░░░░  80%  MCP 서버
Phase 6 ░░░░░░░░░░░░░░░░░░░░   0%  프론트엔드
Phase 7 ░░░░░░░░░░░░░░░░░░░░   0%  통합 테스트
```

---

## 2. 서비스 기동 상태 (현재)

| 서비스 | 포트 | 상태 | 비고 |
|---|---|---|---|
| PostgreSQL (Apptainer) | 5434 | ✅ 실행 중 | `sf_postgres` instance |
| Redis | 6379 | ✅ 실행 중 | 시스템 서비스 |
| Backend (uvicorn) | 8000 | ✅ 실행 중 | `/health` 200 OK |
| Celery Worker | — | ✅ 실행 중 | — |
| Celery Beat | — | ✅ 실행 중 | — |
| MCP Server | 8002 | ✅ 실행 중 | streamable-http |
| Frontend | — | ❌ 미구현 | Phase 6 예정 |

### 빠른 확인 명령

```bash
bash scripts/status.sh
```

---

## 3. 구축 완료 내역 (Phase 1~2 중심)

### 3.1 인프라 (Phase 1 — 완료)

| 항목 | 내용 |
|---|---|
| **컨테이너 전략** | AIDataHub 패턴 채택: PostgreSQL만 Apptainer, 나머지는 native venv |
| **Apptainer 버전** | 1.3.3 |
| **postgres.sif** | `postgres:16-alpine` 기반, `/home/koopark/claude/SignalForge/apptainer/sif/postgres.sif` |
| **DB 포트** | 5434 (5432/5433/5435 충돌로 변경) |
| **Redis 비밀번호** | `Soseks314!` (시스템 redis.conf) |
| **DB 마이그레이션** | Alembic `0001_initial_schema.py` 완료 |
| **마스터 데이터 시딩** | 제품(12종), 플랫폼(9종), 카테고리(12종) 시딩 완료 |
| **FastAPI 헬스체크** | `GET /health` → 200 OK |

**스크립트 구성:**
- `scripts/_common.sh` — 공통 유틸 (load_env, ensure_dirs, require_port_free 등)
- `scripts/build.sh` — SIF 빌드 (postgres base pull → def 빌드)
- `scripts/up.sh` — 전체 서비스 기동 (postgres → redis → backend → celery → mcp)
- `scripts/down.sh` — 전체 서비스 종료
- `scripts/status.sh` — 상태 확인

### 3.2 크롤러 (Phase 2 — 95% 완료)

2026-05-17 딥 크롤(본문+댓글) 적용 후 실수집 검증 결과 (크롤→NLP→DB E2E):

| 플랫폼 | 파일 | 상태 | 실수집 검증 결과 |
|---|---|---|---|
| Clien | `crawler/platforms/clien.py` | ✅ WORKING (딥) | **877건**(본문+댓글), 평균 173자, 최대 4456 |
| DCInside | `crawler/platforms/dcinside.py` | ✅ WORKING (딥) | **775건**(본문+댓글 AJAX), 평균 50자 |
| Ppomppu | `crawler/platforms/ppomppu.py` | ✅ WORKING (딥) | **563건**(본문+댓글), 평균 83자, 최대 2458 |
| XDA | `crawler/platforms/xda.py` | ✅ WORKING | 77건 (기사 헤드라인, 태깅 0 — S26/legacy라 정상) |
| 9to5Google | `crawler/platforms/nineto5google.py` | ✅ WORKING | 37건 (기사 헤드라인), 2건 태깅 |
| Amazon | `crawler/platforms/amazon.py` | ⚠️ 봇 차단 | 0건 — "Dogs of Amazon" 차단. meta 로직 정상(검증불가). 차단 감지 로깅 추가 |
| BestBuy | `crawler/platforms/bestbuy.py` | ❌ | 코드 완성, **서버 레벨 봇 차단** |
| Reddit | `crawler/platforms/reddit.py` | ❌ | 코드 완성, **자격증명 없음** |
| Naver Cafe | `crawler/platforms/naver_cafe.py` | ❌ | **비공개 카페, 자격증명 필요** |
| Twitter/X | `crawler/platforms/twitter.py` | ❌ | **자격증명 없음** |

**누적 DB 적재: 114,580건+** (2026-06-01). 활성 60종, 24개 지역. 11차 마지막 종료.
신규 사이트 미래 후보 풀은 NEXT_SITES.md, 대시보드 마스터 플랜은 docs/dashboard/MASTER_PLAN.md.

**리포팅 스위트 (7트랙 51/51 PASS, 가동 중)**:
- A 정기 리포트 (daily/weekly Markdown) — Celery beat 09 KST / 월 10 KST
- B LLM 인사이트 — ANTHROPIC + OPENAI 양 vendor 지원, OpenAI-호환 오픈소스 LLM
  서버도 OPENAI_BASE_URL/OPENAI_MODEL 로 사용 가능. **키 없으면 동작 안 함**(fallback 거부).
- C MCP 11 tool · D Slack/Discord 알림 · E CSV gzip export · F 9 endpoint · G health critical 자동 검출

**대시보드 P1 MVP (5/5 통과)**:
React+Vite+AntD · Backend /dashboard/overview · mv_voc_daily 78ms refresh ·
Nginx+Basic Auth+GH Actions · Locust p95 15ms (SLA 200ms의 7.5%) · URL 공유 diff 0.
P2 (지식그래프+시계열), P3 (커뮤니티+국가), P4 (실시간+LLM Narrative) 로드맵 확정.

**Drive-Sync 자동 백업**: ApptainerImages:SignalForge/db-dumps/ 일일 04:30 UTC,
sha256 검증, retain 5개. 최신 22MB Drive 업로드 완료. Celery beat 주기 자동
수집 정상 가동. 13년치 게시 이력 커버, 24h 신규 ≈ 13,000건+.

**활성 사이트 46종 — 14개 지역 커버**
(KR/US/JP/AU/IN/ES/BR/TR/FR/MX/**RU**/**AE(AR)**/**IT**/CA*/GLOBAL).
7차 신규 5종 등록 첫 5분 결과:
mobile_review 150건(**2,993자**, 🇷🇺 러시아 최상위급) ·
donanimhaber 138건(467자, 🇹🇷 재시도 성공) ·
arageek 118건(**2,715자**, 🇸🇦 아랍어 RTL 첫 진입) ·
hwupgrade 87건(🇮🇹 재시도, Google News RSS 우회) ·
mobilesyrup 0(🇨🇦 다음 4h 주기 자동).
재시도 잔여: computerbase/4pda/91mobiles — 정밀 분석 대상.

**활성 사이트 26종** (수집 검증됨, 행수순):
dcinside(47,749) · ppomppu(12,454) · clien(9,265) · **dogdrip**(4,119) ·
**instiz**(2,603) · **androidcentral**(1,403) · reddit(1,380) · mlbpark(695) ·
**quasarzone**(629) · **slrclub**(408) · fmkorea(359) · lemmy(352) ·
samsung_community(317) · hackernews(308) · gsmarena(304) · ruliweb(302) ·
**danawa**(234) · stackexchange(180) · **phonearena**(154) · xda(77) ·
theqoo(69) · 9to5google(65) · **theverge**(46) · **engadget**(15) ·
bobaedream(15) · **macrumors**(7).

**2주간 +10종 신규 추가**: 1차 5종 (danawa/instiz/slrclub/phonearena/androidcentral),
2차 5종 (quasarzone/dogdrip/theverge/engadget/macrumors). 비활성 4종(twitter/
amazon_de/jp/kr/bestbuy/naver_cafe — 자격증명 또는 봇차단).

#### 2026-05-29 신규 커뮤니티 5종 추가 (Phase 2 폭 확장)

워크플로우 5 에이전트 병렬 개발 → 4/5 즉시 작동:

| 사이트 | 첫 수집 | 평균 본문 | 특징 |
|---|---|---|---|
| **danawa** (다나와 사용기) | 161건 | **535자** | 구매 후기 풍부 — 의사결정 신호 강함 |
| **instiz** (인스티즈) | 894건 | 35자 | 댓글 다수, K-POP 사이트라 후처리 product 매핑 |
| **slrclub** (SLRClub) | 343건 | 58자 | 카메라/IT, discuss 보드는 로그인 필요로 제외 |
| **phonearena** (영문) | 150건 | 175자 | Cloudflare 차단으로 RSS 우회, 댓글 0 |
| androidcentral (영문) | 0 (timeout) | — | XenForo 포럼, 첫 fetch 장시간 — 다음 주기에서 검증 |

스케줄: 한국 활성 2h, androidcentral 4h, phonearena 6h.

**제품 마스터 48종** (Galaxy 2026 S26/Z8/Buds4 + 2025 라인업 + 구세대 S22~S24/Z5~Z6
/Watch6~7/Buds2 + 경쟁사 iPhone14~16PM/Pixel8~9P). 시드 41→48종 보강.

#### 2026-05-29 안정화 (서비스 복구 + 결함 일괄 수리)

5/28 외부 SIGTERM 으로 전 서비스 다운(OOM 아님). `scripts/up.sh` 로 복구하고
crontab `@reboot` 등록으로 부팅 자동 기동 보장. 이어서 워크플로우 기반 다각도
진단으로 결함 4종 일괄 수리:

| 결함 | 영향 | 수리 |
|---|---|---|
| **translator.py event-loop 바인딩 버그** | Celery worker 매 task 새 loop → "fetch 1232 → save 0" 침묵 실패. 데이터 2/3가 NLP 미처리 | `asyncio.Lock/Semaphore` → `threading.Lock` + 단순 time throttle (loop-agnostic). 3-loop 단위검증 통과 |
| **product_match 별칭 부족** | 태깅률 8.96% (5,990/64,192) | 60+ 한국어 변형(`25울모`/`갤25울`/`26플` 등) 추가, products 시드 +7종(S26/Z8/Buds4). reprocess Phase C 재가동 → **20.21% (13,423건, 2.26배)** |
| **KST→UTC 변환 누락** | 한국 9 사이트(ppomppu/dcinside/clien/bobaedream/fmkorea/mlbpark/theqoo/naver_cafe/ruliweb) 에서 published_at +9h 미래 8,152건 | 워크플로우 7 사이트 일괄 패치 + 누락 3 사이트 메인 보강. DB 보정 -9h UPDATE 8,152행. 미래 잔여 180건 (영문 사이트 미세 drift) |
| **source_url "중복" 의심** | (오류 진단) | dedup 깨짐 아님 — 댓글 모델 정상(thread+N comments 동일 URL). 메트릭 정의만 `external_id 중복(=0)` 으로 교체 |

reprocess 다중 패스: Phase A 한국어 감성 55,358행 재계산(오프라인 즉시),
Phase B 번역 백필 10,522/10,705(98.3%) 복구, Phase C 3회 재태깅 누적 +7,433건.

#### 딥 크롤 (본문+댓글) — 2026-05-17 적용

기존 커뮤니티 크롤러는 게시판 **목록의 제목 한 줄(평균 21~29자)** 만 수집해
실제 VOC(본문·댓글)가 통째로 누락되던 구조적 한계 → 사이트별 에이전트 3개 병렬로
상세 페이지 + 댓글 파서 추가:

- **Clien**: 서버 렌더 HTML — `.post_content`(본문) + `.comment_row`/`.comment_view`(댓글)
- **Ppomppu**: EUC-KR 디코딩 + `lxml` 파서(이중 class 속성 때문에 html.parser 실패),
  `td.board-contents`(본문) + `div[id^=commentContent_]`(댓글)
- **DCInside**: 본문은 `.write_div`, **댓글은 AJAX**(`POST /board/comment/`,
  `e_s_n_o`/`_GALLTYPE_` 토큰 상세페이지 hidden input에서 추출, mgallery=`M`)
- 공통 계약: 글 1개 = 본문 1행 + 댓글 N행, 삭제/디시콘/<5자 댓글 스킵
- 수집 깊이: `LIST_PAGES=15`(목록 15페이지) × `MAX_POSTS=100`(상세 100글/소스)
- **댓글 external_id 멱등화**: 순번 `#cN` → 사이트 안정 ID 사용
  (clien `data-comment-sn`, ppomppu `commentContent_<cid>`, dcinside JSON `no`).
  Clien 2회 연속 실행 검증: RUN1 +223 / RUN2 +0 → 주기 재크롤 중복 폭증 방지 확인
- 대량 수확 결과: 세션 베이스라인 571 → **2329건** (clien+419/ppomppu+337 등)

**주기 자동화 (Celery beat):** `crawl-dcinside-2h` 추가(누락분 보완), beat 재시작.
현재 clien/ppomppu/dcinside 2h · xda 4h · 9to5google 6h 주기로 멱등 누적.
신규 글이 시간 경과에 따라 자동 축적됨 (단기 반복은 사이클2 +0으로 무의미 확인).
`crawler/rotate_collect.py` — 수동 대량 수확용 사이트 로테이션 러너 추가.

#### product_code 자동 태깅 (해결됨)

- 신규 모듈 `crawler/base/product_match.py`: DB 시드 12개 제품 코드와 1:1 정합하는
  `PRODUCT_PATTERNS` + `infer_product_code()`. 구체 변형(Ultra/Plus/Pro/FE) 우선 평가.
- `BaseCrawler.normalize()`: product_code 우선순위 = `meta["product_code"]`(Amazon)
  → `self.product_code`(특정 제품 job) → `infer_product_code(본문)`(커뮤니티 추론).
- `BaseCrawler.save()`: 기존엔 crawler 전역 `self.product_code` 로 product_id 를
  **한 번만** 조회 → 커뮤니티/Amazon 전체 크롤 시 전부 NULL 저장되던 버그.
  → VOC별 캐시 기반 `_resolve_product_id()` 로 수정.
- 태깅률(커뮤니티 15~21%)이 낮은 것은 정상: 목록 제목이 모델명을 명시하지 않거나
  비-Galaxy 잡담이 많아 NULL 이 올바른 결과. 모델명이 있는 글은 정확히 태깅됨.

### 3.3 백엔드 API (Phase 4 — 70% 완료)

- `GET /health`, `/api/v1/products`, `/api/v1/platforms` 동작 확인
- **Analytics API 5종 실데이터 검증 완료 (2026-05-17, 전부 HTTP 200):**
  - `sentiment-trend` — 월/주/일 감성 시계열 (GZF7 실증)
  - `category-dist` — 카테고리 분포 (Z Fold7: comparison 21%, build_quality 13%)
  - `country-heatmap` — 국가별 건수/감성/긍정률
  - `top-issues` — 이슈 랭킹 + 부정 샘플 텍스트(번역본)
  - `compare` — 제품 간 8개 카테고리 점수 (레이더 차트용)
- **버그 수정**: `compare`의 `ROUND((expr) * 100::numeric, 1)` →
  `100`에만 캐스트돼 식 전체가 double로 평가 → `round(double, int) 없음` 500 에러.
  식 전체를 `(((...) * 100))::numeric` 로 캐스트하여 해결. 백엔드 재시작 후 검증.
- **잔여(미완)**: WebSocket `/ws/realtime` — 엔드포인트·ConnectionManager는
  구현됐으나 크롤러(별도 프로세스)가 저장 시 이벤트를 push하는 경로 없음
  (Redis pub/sub 브리지 필요, Phase 6 프론트 연동 시점에 작업 권장).
  Redis 캐싱 레이어 — 미구현(현 2329~만 건 규모에선 집계 쿼리 충분히 빠름, 후순위).

### 3.4 MCP 서버 (Phase 5 — 80% 완료)

- `mcp-server/server.py`: FastMCP `streamable-http` 모드, port 8002
- Tools: `query_voc`, `get_top_issues`, `analyze_sentiment_trend`, `compare_products`, `get_country_breakdown`, `search_voc` 구현됨
- `mcp-server/tools/query.py`, `analytics.py` 완성

---

## 4. 현재 문제점 및 미해결 사항

### 🔴 크롤러 — 수집 불가 플랫폼

| 문제 | 원인 | 해결 방안 |
|---|---|---|
| **BestBuy 0건** | 네트워크 인터셉트까지 시도했으나 서버 레벨 차단 | 공식 API 또는 서드파티 데이터 구매 고려 |
| **Reddit 0건** | PRAW 사용 시 `CLIENT_ID`, `CLIENT_SECRET` 필요 | Reddit API 앱 등록 후 `.env`에 입력 필요 |
| **Twitter/X 0건** | Playwright 로그인 필요 | `TWITTER_USERNAME`, `TWITTER_PASSWORD` 입력 필요 |
| **Naver Cafe 0건** | clubid=28543326 (삼성모바일) 비공개 카페 | `NAVER_ID`, `NAVER_PASSWORD` 로그인 필요 |

### 🟢 크롤러 — 제품 코드 자동 태깅 (2026-05-16 해결)

상세는 §3.2 "product_code 자동 태깅" 참조. `product_match.py` 신규 모듈 +
`normalize()`/`save()` 수정으로 해결. 실수집에서 GS25/GZF7/GW8/GB3P 등 정상 태깅 확인.

### 🟢 NLP 파이프라인 (2026-05-16 정정 — 이미 연결됨)

기존 STATUS의 "미연결" 기술은 **오류였음**. `BaseCrawler.run()`(crawler/base/crawler.py)
이 이미 `from nlp.pipeline import process_voc_list` 를 호출하여 crawl→정규화→NLP→저장
순으로 동작 중. 실수집 검증에서 언어감지/번역/감성/카테고리/참여도 전부 정상 채워짐:
- 예: "S25 Ultra 배터리가 너무 빨리 닳아요 실망" → lang=ko, 번역="…drains too quickly,
  disappointing", sentiment=-0.46 negative, categories=['battery'], engagement=26.98
- NLP 전 모듈이 **오프라인 라이브러리**(VADER/langdetect/deep-translator/키워드) 기반
  → `ANTHROPIC_API_KEY` 없이도 전체 동작.

#### 데이터 품질 정리 (2026-05-17)

대량 백필 중 발견된 품질 결함과 조치:

- **결함**: 데이터의 94%가 한국어. 무료 Google 번역이 동시성 폭주로
  레이트리밋('too many requests') → 한국어 ~3900건 미번역. VADER는
  영어 전용이라 미번역 한국어의 sentiment 가 전부 무의미한 neutral 로 채워짐.
- **근본 수정** `nlp/translator.py`: 전역 Semaphore(동시 3) + 최소 간격(0.25s)
  + 레이트리밋 시 지수 백오프 재시도(4회). 원인: `pipeline.py` 가 한 글의
  댓글 전부를 `asyncio.gather` 로 동시 번역 → 순간 수십 호출. 이후 수집·재처리
  전반에 적용.
- **근본 해결 — 한국어 네이티브 감성** `nlp/sentiment_ko.py`:
  데이터 94%가 한국어인데 영어 전용 VADER가 못 읽어 감성이 무의미하던
  핵심 결함을 해소. 제품 VOC 특화 한/긍부정 사전(substring 스캔 +
  부정어 윈도우 반전 + 강조어 증폭 + tanh 정규화). **번역 의존 없음**
  → 오프라인·레이트리밋 무관. `sentiment.analyze(text, lang)` 디스패처가
  ko는 원문 직접 채점, 그 외는 VADER. 단위테스트 18케이스 통과.
  - 복합어 오탐 가드: "색상**별로**(by color)" ≠ "별로(meh)" 등
    위험어(별로/갓/굿…)는 앞이 한글이면 접미사로 보고 무시.
- **정리 스크립트** `nlp/reprocess.py` 2-페이즈:
  - Phase A(빠름·오프라인): 전체 한국어 행 sentiment 를 원문 기반으로
    재계산. **15,987건 2초 처리** (레이트리밋 무관). pipeline.py 도
    ko는 원문 채점하도록 연결.
  - Phase B(느림·레이트리밋): 미번역 비영어 행 번역 백필 →
    content_translated/categories 보강. 백그라운드 진행.
- 효과: VADER 시절 가짜 positive 인플레이션(≈7천) 제거 →
  neutral 76.5% / pos 13.2% / neg 10.3% (한국 커뮤니티 질문·정보·거래글
  비중상 타당). 부정 표본 검증: "갤럭시북3 3연속 불량 짜증주의" -0.99 등.
- **잔여 한계**: 사전 기반이라 문맥/반어는 한계. 정밀 감성은
  `ANTHROPIC_API_KEY` LLM 감성이 상위 해법 — 단 현재도 무의미 neutral 이
  아닌 **유의미 신호**라 분석 사용 가능. 권고 사항으로만 기록.

### 🟡 환경변수 미입력 항목

| 변수 | 용도 | 현재 값 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API (감성분석, 카테고리) | 미입력 |
| `REDDIT_CLIENT_ID` | Reddit PRAW | 미입력 |
| `REDDIT_CLIENT_SECRET` | Reddit PRAW | 미입력 |
| `NAVER_ID` / `NAVER_PASSWORD` | Naver Cafe | 미입력 |
| `TWITTER_USERNAME` / `TWITTER_PASSWORD` | Twitter | 미입력 |
| `DEEPL_API_KEY` | 번역 (선택) | 미입력 |

### 🟢 해결된 이슈

| 이슈 | 해결 내용 |
|---|---|
| Apptainer build 프록시 오류 | `BUILD_PROXY_HTTPS=off` 설정, 직접 인터넷 연결 |
| postgres.def `From:` 경로 오류 | `build.sh`에서 `sed`로 절대경로 치환 |
| FastMCP `version=`/`description=` 파라미터 오류 | `mcp>=1.0.0` API 변경 반영 → `instructions=` 사용 |
| MCP stdio 모드 오류 | `mcp.run(transport="streamable-http")`로 변경 |
| MCP 포트 충돌 (8001) | `MCP_PORT=8002`로 변경 |
| Naver Cafe clubid 플레이스홀더 | `11223344` → 실제 삼성모바일 카페 `28543326`으로 수정 |

---

## 5. 환경 정보

```
서버:      smarttwincluster
프로젝트:  /home/koopark/claude/SignalForge
Python:    시스템 Python (각 venv별 분리)
Apptainer: 1.3.3

포트 현황:
  8000 — backend (uvicorn)
  8001 — 다른 프로세스 사용 중
  8002 — MCP server (streamable-http)
  6379 — Redis
  5434 — PostgreSQL (Apptainer)
  8080 — 사내 프록시
```

### .env 주요 설정

```ini
APP_NAME=sf
POSTGRES_PORT=5434
REDIS_PASSWORD=Soseks314!
MCP_PORT=8002
BUILD_PROXY_HTTPS=off
BUILD_PROXY_HTTP=off
```

---

## 6. 디렉토리 구조 (현재 실제)

```
SignalForge/
├── .env                          # 환경변수
├── .env.example
├── PLAN.md                       # 원본 기획서
├── STATUS.md                     # ← 이 파일
│
├── apptainer/
│   ├── postgres.def              # Apptainer 정의 파일
│   └── sif/
│       ├── postgres-base.sif     # postgres:16-alpine base
│       └── postgres.sif          # 빌드 완료된 운영용 SIF
│
├── scripts/
│   ├── _common.sh                # 공통 유틸 함수
│   ├── build.sh                  # SIF 빌드
│   ├── up.sh                     # 전체 기동
│   ├── down.sh                   # 전체 종료
│   ├── status.sh                 # 상태 확인
│   └── db.sh                     # DB 유틸
│
├── backend/
│   ├── requirements.txt
│   ├── .env                      # backend 전용 env
│   ├── alembic/
│   │   └── versions/
│   │       └── 0001_initial_schema.py  ✅ 마이그레이션 완료
│   └── app/
│       ├── main.py               ✅ FastAPI + /health
│       ├── config.py
│       ├── database.py           ✅ asyncpg 연결
│       ├── models/               ✅ product, voc, platform, crawl_job
│       ├── schemas/              ✅ voc, analytics
│       ├── api/                  ⚠️ products/analytics 부분 완성
│       ├── services/             ⚠️ voc_service, analytics_service 스켈레톤
│       └── seeds/
│           └── seed_master.py    ✅ 제품/플랫폼/카테고리 시딩
│
├── crawler/
│   ├── requirements.txt
│   ├── celery_app.py             ✅ Celery 앱 설정
│   ├── tasks.py                  ✅ 크롤링 태스크 등록
│   ├── base/
│   │   ├── crawler.py            ✅ BaseCrawler ABC (normalize/save 태깅 수정)
│   │   └── product_match.py      ✅ 신규: product_code 추론 (DB 시드 정합)
│   ├── platforms/
│   │   ├── amazon.py             ⚠️ 봇 차단 (차단 감지 로깅 추가)
│   │   ├── bestbuy.py            ❌ 봇 차단
│   │   ├── clien.py              ✅ WORKING (33건/7태깅)
│   │   ├── ppomppu.py            ✅ WORKING (14건/1태깅)
│   │   ├── dcinside.py           ✅ 신규 WORKING (95건/15태깅)
│   │   ├── xda.py                ✅ WORKING (77건)
│   │   ├── nineto5google.py      ✅ WORKING (35건/2태깅)
│   │   ├── reddit.py             ❌ 자격증명 필요
│   │   ├── naver_cafe.py         ❌ 자격증명 필요 (clubid=28543326)
│   │   └── twitter.py            ❌ 자격증명 필요
│   └── nlp/
│       ├── pipeline.py           ✅ run()에 연결됨, E2E 동작
│       ├── detector.py           ✅ langdetect, 동작
│       ├── translator.py         ✅ deep-translator, 동작
│       ├── sentiment.py          ✅ VADER+기술어휘, 동작
│       └── categorizer.py        ✅ 키워드 분류, 동작
│
├── mcp-server/
│   ├── requirements.txt
│   ├── server.py                 ✅ FastMCP streamable-http, port 8002
│   ├── db.py                     ✅ DB 연결
│   └── tools/
│       ├── query.py              ✅ query_voc, get_top_issues 등
│       └── analytics.py         ✅ sentiment_trend, compare 등
│
├── nginx/
│   └── nginx.conf                ⚠️ 작성됨, 미기동
│
└── data/
    └── postgres/pgdata/          ✅ PostgreSQL 데이터 디렉토리
```

---

## 7. 다음 작업 우선순위

### ✅ 2026-05-16 세션 완료

| 작업 | 결과 |
|---|---|
| 커뮤니티 product_code 자동 태깅 | ✅ `product_match.py` + normalize/save 수정, 실증 |
| NLP 파이프라인 연결 | ✅ 확인 결과 이미 연결됨 (STATUS 오류 정정), E2E 동작 검증 |
| Amazon 실수집 테스트 | ✅ 봇 차단 확인 + 차단 감지 로깅 추가 |
| **DCInside 크롤러 신규 추가** | ✅ 갤럭시/스마트폰 갤러리, 95건 수집·15건 태깅 |
| 6개 소스 E2E 실수집 검증 | ✅ 누적 254건 DB 적재 |

### 단기 (다음 세션)

| 순위 | 작업 |
|---|---|
| 1 | `ANTHROPIC_API_KEY` 입력 후 Claude 기반 감성분석/카테고리 분류 고도화 |
| 2 | `REDDIT_CLIENT_ID/SECRET` 입력 후 Reddit 크롤러 검증 |
| 3 | Amazon 봇 차단 우회 (스텔스/프록시/공식 API 검토) |
| 4 | 카테고리 분류 한국어 키워드 보강 (커뮤니티 단문 대응) |
| 5 | Analytics API 완성 (sentiment-trend, category-dist, top-issues) |
| 6 | Phase 6 — Frontend 구축 시작 |

---

## 8. 자격증명 체크리스트 (입력 필요)

```bash
# .env 파일에 아래 항목 입력 필요
ANTHROPIC_API_KEY=sk-ant-...        # 필수 (NLP 품질에 직결)
REDDIT_CLIENT_ID=                   # Reddit 앱 https://www.reddit.com/prefs/apps
REDDIT_CLIENT_SECRET=
NAVER_ID=                           # 삼성모바일 카페 접근용
NAVER_PASSWORD=
TWITTER_USERNAME=                   # Twitter 계정
TWITTER_PASSWORD=
DEEPL_API_KEY=                      # 번역 품질 향상 (선택)
```

---

*SignalForge — "시장의 목소리를 데이터로, 데이터를 개선의 방향으로"*
