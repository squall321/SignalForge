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
기본 330초)이 끝나면 **요청을 보내지 않고 508 을 즉시 돌려준다**.
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

주의: transport 안의 응답은 아직 읽기 전이라 `resp.content` 가
`ResponseNotRead` 로 터진다. 그래서 `aread()` 로 본문을 확정한 뒤 판정하고,
`content-encoding`/`content-length` 를 뗀 새 응답을 돌려준다(안 떼면 httpx 가
한 번 더 압축을 풀려다 `DecodingError`).

Source: [[crawler/base/crawler.py#_looks_walled]]
