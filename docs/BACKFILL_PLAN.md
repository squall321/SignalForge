# 전 플랫폼 역사 수집 계획

> 지시(2026-09-15): "천천히, 하지만 확실하게 reddit의 모든 voc 를 가지고 와야해.
> reddit뿐만 아니라 모든 플랫폼."

## 왜 필요한가 — 실측

코퍼스는 2026 년에 쏠려 있다.

| 연도 | 건수 |
|---|---|
| 2026 | 313,279 |
| 2025 | 33,792 |
| 2024 | 19,752 |
| 2023 이전 | 약 82,000 |

그 역사마저 **소수 소스가 만든 것**이다(HN 연도 슬라이싱·wayback·wpnews·youtube).
활성 플랫폼 88 개 중 역사 백필이 닿는 곳은 약 15 개뿐이고, 나머지 **약 73 개는
자기가 등록된 날부터만** 모으고 있다. 실측 — dcinside 67,362 건 중 과거는 3 건,
computerbase·theqoo·pikabu·instiz 등은 과거 0 건.

## 현재 커버리지

| 백필 | 대상 | 주기 |
|---|---|---|
| `hn-backfill.sh` | hackernews (연도 롤링) | 매일 |
| `youtube-backfill.sh` | youtube_comments (연도 롤링) | 매일 |
| `wpnews-backfill.sh` | wpnews (연도 롤링) | 매일 |
| `wayback-backfill.sh` | wayback_news (연도 롤링) | 매일 |
| `kr-backfill.sh` | clien · ppomppu · dcinside (깊은 페이지) | 일요일 |
| `global-backfill.sh` | hackernews · sammobile · sammyfans · notebookcheck · engadget · xataka · gigazine · anandtech · xda | 토요일 |

모두 `scripts/backfill-runner.sh` 가 **직렬로** 호출한다(호스트 swap 0 — 동시 실행
금지). 진행 상태는 `logs/<name>-year.state` 에 연도 하나로 남기고 매 실행 한 해씩
거슬러 올라간다. 바닥(FLOOR)에 닿으면 올해부터 다시 순회한다.

## 원칙

1. **천천히.** 레이트리밋을 유발하면 그 소스를 통째로 잃는다. 실측으로 이미 두 번
   자초했다(kaskus 403 9회 · computerbase 429). 한 번에 한 창(window)씩, 딜레이를
   두고, 하루에 조금씩 전진한다. 총량을 하루에 끝내려 하지 않는다.
2. **재개 가능.** 상태를 파일에 남겨 중단돼도 이어서 한다. 크론이 매일 조금씩
   전진시키는 구조이므로 한 번의 실행이 완주할 필요가 없다.
3. **직렬.** 러너가 하나씩 호출한다. 동시 실행은 OOM 을 부른다.
4. **멱등.** 중복은 `external_id` + `content_hash` 2 단으로 이미 막힌다. 같은 창을
   다시 돌아도 손해가 없다.
5. **불가능한 것은 불가능하다고 적는다.** 단일 RSS 만 제공하는 소스는 과거를
   가져올 수단이 없다. 억지로 만들지 않고 목록에 사유를 남긴다.

## 소스 분류 (수단 기준)

| 유형 | 수단 | 예 |
|---|---|---|
| A. 아카이브 API | 기간 검색 API | reddit(Arctic Shift) · hackernews(Algolia) · youtube |
| B. 깊은 페이지네이션 | 목록 page=N 을 깊게 | dcinside · clien · ppomppu · theqoo · instiz · dogdrip · ruliweb · kaskus · lowyat · 포럼 일반 |
| C. 사이트맵/아카이브 페이지 | sitemap.xml · /archive · 연도별 목록 | 워드프레스 계열 뉴스(sammobile · sammyfans · phandroid …) |
| D. 외부 아카이브 | Wayback Machine | 위 수단이 없는 뉴스 |
| E. 불가 | 최신 N 건만 제공 | 단일 RSS 고정 피드 |

## 진행 (체크리스트는 `docs/BACKFILL_CHECKLIST.md`)

1라운드는 **Reddit** 부터 — 지시에서 명시적으로 먼저 언급됐고, Arctic Shift 로
기간 검색이 가능함을 실측 확인했다(2023-01~2023-04 구간 정상 반환).
