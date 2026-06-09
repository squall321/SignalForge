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
