# Crawler

플랫폼별 VOC 크롤링 전략. 모든 크롤러는 [[voc-pipeline]]을 따른다.

## BaseCrawler

추상 기반 클래스. 서브클래스는 `crawl()` 메서드만 구현하면 된다.

```text
BaseCrawler
├── crawl()    ← 필수 구현 (플랫폼별)
├── parse()    ← 선택 오버라이드
├── normalize() ← RawVOC → StandardVOC 변환
├── save()     ← DB 저장 (중복 방지 포함)
└── run()      ← 전체 파이프라인 실행 + 상태 업데이트
```

Source: [[crawler/base/crawler.py#BaseCrawler]]

## Platform Strategy

9개 플랫폼별 크롤링 방식과 주기. 전략 선택 기준은 JS 렌더링 여부와 API 가용성.

| platform_code | 방식 | 주기 | 비고 |
| --- | --- | --- | --- |
| `reddit` | PRAW API | 1시간 | 서브레딧 + 댓글 수집 |
| `twitter` | Playwright | 2시간 | 로그인 세션 필요 |
| `amazon_us` / `amazon_de` / `amazon_jp` / `amazon_kr` | httpx + BS4 | 6시간 | ASIN 또는 검색 기반 |
| `bestbuy` | Playwright | 6시간 | SKU 검색 후 리뷰 페이지 |
| `clien` | httpx + BS4 | 2시간 | 모바일 게시판, 필터링 포함 |
| `ppomppu` | httpx + BS4 | 2시간 | 휴대폰 게시판 |
| `xda` | httpx + BS4 | 4시간 | 포럼 스레드 |
| `9to5google` | httpx + BS4 | 6시간 | 기사 요약 + 다음단계 댓글 확장 가능 |
| `naver_cafe` | Playwright | 4시간 | 삼성 공식 카페, 로그인 선택사항 |

Source: [[crawler/celery_app.py#app]] — beat_schedule에서 주기 확인

## Reddit Crawler

- PRAW `subreddit.search(keyword, sort="new", time_filter="week")` 사용
- 대상 서브레딧: `galaxys25`, `Samsung`, `Android` 등 9개
- 포스트 본문 + 상위 5개 댓글을 각각 별도 `RawVOC`로 수집
- `external_id` 형식: `post_{id}` | `comment_{id}`

Source: [[crawler/platforms/reddit.py#RedditCrawler]]

## Amazon Crawler

- ASIN이 있으면 직접 리뷰 페이지, 없으면 검색 후 첫 번째 제품의 ASIN을 추쳐 수집.
- `amazon_us` / `amazon_de` / `amazon_jp` / `amazon_kr` 4개 플랫폼 코드를 각각 인스턴스화하는 단일 클래스 사용.
- 병렬 평점(rating)은 `meta["rating"]`에 저장되지만 DB에는 저장 안 함 — 향후 `voc_records` 확장 시 활용 가능.

Source: [[crawler/platforms/amazon.py#AmazonCrawler]]

## Korean Platform Crawlers

Clien과 ppomppu는 한국어 VOC 수집. Galaxy 관련 키워드 필터링 적용 후 저장.

- `ClienCrawler` — `socduser`(사용기) + `cm_galaxy`(걤럭시) 게시판
- `PpomppuCrawler` — `phone`(휴대폰) + `review`(리뷰) 게시판
- `NaverCafeCrawler` — 삼성 공식 카페, Playwright 기반, 로그인 선택사항
- 수집된 VOC는 [[nlp#Translation]]에서 영어로 번역됨

Source: [[crawler/platforms/clien.py#ClienCrawler]]
Source: [[crawler/platforms/ppomppu.py#PpomppuCrawler]]
Source: [[crawler/platforms/naver_cafe.py#NaverCafeCrawler]]

## Bot Detection Bypass

- User-Agent 풀 5개 중 랜덤 선택
- 플랫폼별 1~5초 랜덤 딜레이 (`MIN_DELAY`, `MAX_DELAY` 클래스 변수로 조정)
- Playwright 사용 시 `playwright-stealth` 플러그인 적용 권장

Source: [[crawler/base/crawler.py#USER_AGENTS]]

## Celery Task

`crawl_platform(platform_code, product_code, job_id)` 태스크가 진입점.
실패 시 `max_retries=3`, 5분 간격 재시도.

Source: [[crawler/tasks.py#crawl_platform]]

## 시간 예산 (Time Budget)

celery soft time limit 이 600초다. 이걸 넘기면 `SoftTimeLimitExceeded` 로 죽고
**그때까지 긁은 것이 통째로 버려진다**. 그래서 두 겹으로 막는다.

### 1. 수집 예산 — HTTP 길목에서 끊는다

크롤러마다 루프에 `budget_exceeded()` 를 손으로 넣는 방식은 실패했다.
AST 감사(2026-09-15) 결과 96개 중 88개는 호출이 아예 없었고, 있는 8개도
최내곽이 아닌 바깥 루프에 둬서 안쪽 루프 한 바퀴가 통째로 돈 뒤에야 발화했다
(appstore·telepolis·mobile_review 에서 같은 실수를 세 번 반복했다).

지금은 `_BudgetTransport` 가 모든 httpx 요청을 지난다. 예산(`CRAWL_BUDGET_SEC`,
기본 330초)이 끝나면 **요청을 보내지 않고 200 + 빈 JSON(`{}`)을 즉시 돌려준다**.
에러 상태코드를 쓰면 안 된다 — 크롤러 60개가 `raise_for_status()` 를 부르고
거기서 예외가 `crawl()` 밖으로 새면 모은 것을 통째로 잃는다(초판이 508 을
쓰다가 ppomppu 에서 `HTTPStatusError` 를 냈다). 빈 JSON 본문은 `resp.text`
(HTML 파서가 0건) 와 `resp.json()`(빈 dict) 양쪽을 모두 안전하게 만든다.
호출자는 이미 "200 아니면 건너뛴다"이므로 남은 반복이 네트워크 없이 소진되고,
`crawl()` 은 그때까지 모은 것을 정상 반환한다. 예외를 던지면 수집분이 전량
유실되므로 던지지 않는다. `_random_delay()` 도 예산 초과 시 즉시 반환한다.

**새 크롤러는 반드시 `self._make_httpx_client()` 로 클라이언트를 만들어야 한다.**
직접 `httpx.AsyncClient(...)` 를 만든다면 `transport=self._budget_transport()`
를 넘겨야 하며, `test_budget_transport.py` 가 AST 로 이를 강제한다.

### 2. 번역 마감 — 건당 추정은 불가능하다

NLP 비용을 "최악 0.667s/건"으로 잡았다가 틀렸다. 실측 telepolis 는 150건 NLP 에
426초(2.84s/건)를 썼다 — Google 레이트리밋에 걸리면 백오프가 최대 30초씩 붙어
건당 비용에 상한이 없다. 청크 사이에서만 확인하면 청크 하나가 통째로 넘긴다.

그래서 건수가 아니라 시각으로 끊는다. `run()` 이 `_started + RUN_BUDGET_SEC`
(기본 430초)를 마감으로 넘기고, `translate_to_english` 가 마감을 넘기면 번역을
건너뛰고 원문을 남긴다. 원문은 저장되므로 6시간 주기 `translate_backlog` 이
나중에 메운다 — 통째로 죽는 것보다 낫다.

Source: [[crawler/base/crawler.py#_BudgetTransport]]
Source: [[crawler/nlp/translator.py#translate_to_english]]

## 차단벽 감지 (Bot Wall Detection)

"실패 기록 없이 0건"은 원인을 감춘다. 0건은 "할 말이 없었다"와 "막혔다"를
구분하지 못하는데 둘 다 `done` 으로 기록됐다. androidcentral 은 그렇게 35일간
"정상인데 조용함"으로 묻혔다 — 실제로는 stile 챌린지 벽(HTTP 200 인데 내용은
챌린지 페이지)이었다.

`_BudgetTransport` 가 모든 응답의 벽 서명(stile·Cloudflare·PerimeterX·Imperva)
을 센다. **아무것도 못 긁었는데** 요청 절반 이상이 벽이면 `status="blocked"` 로
남긴다. 한 건이라도 긁혔으면 `done` 이다(상세 페이지 일부 403 은 차단이 아니다).
404 도 차단이 아니다 — 사라진 게시판은 대응이 다르다.

**429 는 벽이 아니다.** 레이트리밋은 일시적이고 기존 백오프·재시도가 다루는
정상 운영 상황이라 `_throttle_hits` 로만 세고 로그에 남긴다. `blocked` 는
"구조적으로 막혔다"는 뜻이어야 한다(403·챌린지). 429 를 벽으로 치면 건강한
소스에 오경보가 난다 — computerbase 는 30일 574건을 수집하는 정상 소스인데
검증하느라 짧은 시간에 반복 호출하자 429 를 내놨고 그게 차단으로 기록됐다.
같은 이유로 **본문이 20KB 를 넘으면 상태코드와 무관하게 거절이 아니다**
(computerbase 는 429 와 함께 32KB 짜리 정상 HTML 을 돌려주기도 한다).

주의: transport 안의 응답은 아직 읽기 전이라 `resp.content` 가
`ResponseNotRead` 로 터진다. 그래서 `aread()` 로 본문을 확정한 뒤 판정하고,
`content-encoding`/`content-length` 를 뗀 새 응답을 돌려준다(안 떼면 httpx 가
한 번 더 압축을 풀려다 `DecodingError`).

Source: [[crawler/base/crawler.py#_looks_walled]]

## 0건 소스 진단 결과 (2026-09-15)

헬스 체크가 "수집 0건"으로 지목한 27종을 전부 직접 돌려 분류했다.
**대부분은 고장이 아니었다.**

| 분류 | 소스 | 실체 |
|---|---|---|
| 정상 (긁는데 신규가 없음) | arageek 70 · danawa 20 · dpreview 8 · engadget 25 · gizmodo_au 61 · hackerone 9 · **ifixit 700** · mobile_review 150 · resetera 18 · sanook 16 · stackexchange 41 · techcabal 150 · techinafrica 85 | 전부 중복. 경보가 잘못이었다 |
| 구조적 차단 | androidcentral (stile 챌린지 18/36) · quora (Cloudflare) | 재시도로 안 뚫린다 |
| Playwright 챌린지 실패 | fmkorea | **감지 밖** — Playwright 경로는 `_BudgetTransport` 를 지나지 않아 벽으로 잡히지 않는다 |
| 자격증명 없음 | bluesky · reddit · twitter | `.env` 에 키가 비어 있다 |
| 사이트 도달 불가 | slrclub | DNS 는 되는데 80·443 모두 connection refused |
| 일시적 | computerbase (429) · iphoneincanada · ithome (RSS 빈 응답) | 다음 주기에 회복 |

교훈 — **"수집 0건"은 진단명이 아니다.** ifixit 는 700건을 긁고도 0건으로
경보됐고, 그 소음 속에서 정말로 막혀 있던 androidcentral 이 35일간 묻혔다.
그래서 `items_fetched` 를 따로 기록해 "못 긁었다"와 "긁었는데 신규가 없다"를
가른다(alembic 0043).

Playwright 경로 — 봇 챌린지를 Playwright 로 푸는 크롤러(fmkorea 등)는 httpx 를
지나지 않아 `_BudgetTransport` 의 자동 감지가 닿지 않는다. 그런 크롤러는 실패를
아는 지점에서 `self.report_blocked(사유)` 를 불러 직접 알린다. 안 부르면 그
소스만 조용히 0건으로 남아 원인이 묻힌다 —
`test_playwright_crawlers_declare_blocks` 가 이걸 강제한다.
