# 전 플랫폼 역사 수집 체크리스트

계획은 [BACKFILL_PLAN.md](BACKFILL_PLAN.md). 여기는 실행 목록이다.
한 항목이 끝날 때마다 실측 수치를 적는다 — "돌렸다"가 아니라 "몇 건 늘었다".

## R1 — Reddit (지시에서 명시된 1순위)

- [x] Arctic Shift 기간 검색 백필 스크립트 (`crawler/scripts/reddit_arctic_backfill.py`)
- [x] 러너 (`scripts/reddit-backfill.sh`) + `logs/reddit-backfill-year.state`
      — **연도를 매번 내리지 않는다.** sub 별 시각 커서를
      `logs/reddit-backfill-state.json` 에 남기고 이어받으며, 그 해의 모든 sub 가
      소진돼야(rc=3) 연도를 내린다. 안 그러면 한 해의 꼬리만 긁고 넘어간다.
- [x] `backfill-runner.sh` 에 등록 (직렬, 매일)
- [x] 회귀 시험 9 종 — 커서 단조감소·상한 시 커서 보존·중복시각 무한루프 방지·
      실패 시 재시도 보존·상태파일 원자성/손상내성·대상목록 실시간과 일치
- [x] 1 연도 부분 실측 — 2023 r/samsung 5 페이지: **500 건 수집 → 492 건 신규 저장**
      (이전 2023 년 reddit_rss 0 건). 딜레이 4 초에서 429 없음.
- [ ] 전체 연도 순회 진행 (크론이 매일 전진 — 경과 관찰)
- [ ] 댓글까지 가져올지 결정 (본문만 vs 본문+댓글 — 비용 대비 가치)
- [ ] r/GooglePixel 추가분이 역사에도 반영되는지 확인

## R2 — 깊은 페이지네이션 (유형 B)

한국 커뮤니티가 우선. 이미 `historical_kr_backfill.py` 가 clien·ppomppu·dcinside
3 개를 덮는다. 나머지를 같은 틀에 넣는다.

- [x] **커서 틀 구축** — `crawler/scripts/deep_page_backfill.py` +
      `scripts/deep-page-backfill.sh` (월/화/수 각 1 site). 기존 kr-backfill 은
      매주 같은 앞 50p 를 다시 긁어 제자리걸음이었다(36분에 5건).
- [x] dcinside · clien · ppomppu 에 `PAGE_START` 도입 (기본값 불변)
- [x] 회귀 시험 13+9 종 — 창 타일링 빈틈 없음·커서 전진·실패 시 보존·바닥 재순회
- [x] 실측 ppomppu p80~83 — 20건 수집 → **3건 신규(2018년 글)**, 커서 p84
- [x] **백필은 번역을 건너뛴다** — 12시간 주기 치유가 메운다. 백필이 번역
      레이트리밋을 유발해 실시간 파이프라인 할당량을 갉아먹던 문제.
- [x] ppomppu 컬럼 고정 인덱스 해소 — review 게시판 발행일이 통째로 NULL 이었다
- [x] theqoo · instiz · dogdrip · ruliweb · bobaedream 추가 (대상 3→8, 월~금 배분)
      SITES 항목의 모듈·클래스·PAGE_START 실재를 시험으로 강제 — 오타 하나면
      그 소스만 조용히 빠진다(BobaedreamCrawler 오타를 그 시험이 잡았다)
- [ ] slrclub — 사이트가 80·443 모두 connection refused. 복구 후 재시도
- [x] 글로벌 포럼 — kaskus · lowyat · donanimhaber 추가 (대상 8→11, 토요일 배분)
      실측 kaskus 2페이지 126건. **불가 2종은 사유를 남겼다**(UNSUPPORTED) —
      resetera(목록 페이지네이션 없음) · gsmarena_forum(기기별 리뷰 구조)
- [x] **역사 없는 소스 27개 전수 분류** (2026-09-15) — 수단별로 갈랐다.
      | 분류 | 수 | 처리 |
      |---|---|---|
      | B 목록 페이지네이션 | 16 | 표준 10종 등록 완료 → 대상 11→**20** |
      | A WP REST | 3 | hipertextual·mobile_review·jagatreview — 기간 필터 검증 필요 |
      | C/D 피드 전용 | 6 | xataka_mx·pikabu·4pda·macrumors·arxiv·zdnet_kr → R4 |
      | E | 2 | fourchan_g(만료 — 역사 없음) · lemmy |
- [ ] 각 소스의 "바닥" 확인 (몇 페이지까지 실제로 존재하나)
- [x] 컬럼 고정 인덱스 전수 점검 — ppomppu·instiz 둘뿐이고 instiz 는 실해 없음
- [x] **발행일 결측률 감시 신설** — 셀렉터 노후화는 수집량 지표로 안 보인다.
      dogdrip 이 넉 달간 88%를 무날짜로 쌓는 동안 실패 로그는 0건이었다.
      최근 72h 결측률 25% warning / 60% critical (23개 중 dogdrip 만 걸림)
- [x] **dogdrip 댓글 셀렉터 복구** — `.comment-bar-author` → `.comment-bar`.
      수집 시 날짜·작성자 12% → 100%
- [ ] dogdrip 옛 행 소급 복구 진행 중 (매일 150 URL, NULL 88% → 65.1%)

## R3 — 사이트맵/아카이브 (유형 C)

- [x] **sitemap 이 아니라 WP REST 가 답이었다.** wpnews 가 이미 `after`/`before`
      로 2016년까지 13,799건(과거 12,596건)을 모으고 있었다 — 대상 매체가
      3개뿐이었을 뿐이다. 후보를 실측해 되는 것만 넣었다(3→6).
      | 매체 | 결과 |
      |---|---|
      | Hipertextual · TechCabal · MySmartPrice | 2022년 기사 정상 → **채택** |
      | MobileSyrup | 200 인데 기간 필터 무시, 최신만 반환 → 제외 |
      | SamsungFans · Ausdroid · PhoneArena · XatakaMX | 403 → 제외 |
      | Tecnoblog | 검색 0건 → 제외 |
- [x] **기간 필터 무시 감지** — MobileSyrup 처럼 정상처럼 보이며 최신만 주는
      경우를 걸러낸다. 못 걸러내면 과거를 긁는 줄 알고 헛돈다.
- [x] **연 단위 → 월 단위 슬라이싱** — 크롤러 상한이 사이트·쿼리당 3페이지라
      기사가 많은 해는 연 창에서 다 못 긁는다. 실측 2022 상반기 184→1,212건.
      다만 시기에 따라 다르다(2019-12 는 2건 — 그 달은 실제로 더 없다).
- [x] 백필 셸 규약 시험 — 문법·set -u·DB 정의 순서·전역 락·시작/종료 로그·
      러너 등록(49 케이스). 작업 중 DB 정의를 지워 매 실행 즉시 죽던 것을 계기로.
- [ ] 403 매체(PhoneArena·XatakaMX 등)에 다른 수단이 있는지 — sitemap / Wayback
- [x] **A 묶음 3종 완료** — hipertextual·mobile_review·jagatreview 에 기간 창을
      직접 얹어 **자기 플랫폼 코드로** 역사를 쌓는다(wpnews 로 모으면 플랫폼별
      분석에서 그 소스는 여전히 역사가 없다). 공용 모듈 `base/wp_window.py` 로
      빼서 11개 wp-json 크롤러가 갈라지지 않게 했다.
      실측 hipertextual 2022 상반기 62건 — 이전 과거 0건.
- [ ] arageek · techcabal · mysmartprice · mobilesyrup 등 나머지 wp-json 검토

## R4 — 피드 전용 6종 (유형 C/D)

분류에서 '피드 전용'으로 묶였지만 **실제로는 각자 다른 수단이 있었다.**
코드만 보고 Wayback 으로 넘기지 않고 하나씩 실측했다.

- [x] **arxiv** — arXiv API 가 `submittedDate:[YYYYMMDDHHMM TO ...]` 범위를
      지원한다. 실측 2022 상반기 50건(이전 과거 0). 겸사겸사 **API URL 이 http 라
      301 로 죽던 것**도 발견해 https 로 고쳤다.
- [x] **xataka_mx** — 태그 목록이 `/tag/<tag>/record/<offset>` 로 페이지네이션된다
      (20건 단위, `rel=next` 로 명시). `/pagina/N` 같은 흔한 패턴은 404 라
      추측했으면 틀렸을 것이다. 첫 페이지만 보던 것을 여러 페이지로 열었다.
- [x] **pikabu** — 검색이 `?page=N` 으로 깊이 들어간다(페이지 안 링크에 page=100
      까지). 실측 p8~12 에서 79건·연도 [2025,2026]. 저장은 2건뿐이었지만
      (나머지는 보유 중이거나 MX 필터) **경로가 열렸다** — 이전엔 구조적으로
      과거에 닿을 수 없었다.
- [x] **zdnet_kr — 역사 조사 중에 '현재 수집이 죽어 있는 것'을 발견했다.**
      `zdnet.co.kr/search.html?word=` 가 404 이고, 이 크롤러는 검색 단일 경로라
      소스가 통째로 멈춰 있었다(마지막 수집 2026-09-11). 사이트 검색 폼에서 새
      경로를 직접 읽어 교체 → `search.zdnet.co.kr/?kwd=`. 복구 확인 33건.
      깊이 수집은 **불가** — `&page=N` 을 붙여도 응답이 바이트 단위로 동일하다.
- [ ] 4pda — 탐색 시 403. 차단 우회 수단이 먼저 필요하다 (Playwright 계열 검토)
- [ ] macrumors — RSS 피드 3종만 쓴다. 페이지네이션 없음. `rel=next` 가 있는
      `/guide/samsung/N/` 은 제품 가이드라 VOC(사용자 목소리)로는 성격이 다르다.
      Wayback 이나 포럼 경로를 봐야 한다.
- [x] **lemmy** — Lemmy API 가 `page` 를 지원한다(실측 page 3 이 다른 게시물).
      페이지네이션 도입 + 페이지 간 중복 차단. 실측 raw=60.
      > 이 작업 중 **내가 만든 버그 2건**을 시험이 잡았다 — 파싱 함수를 분리하며
      > `query` 인자를 빠뜨려 NameError 가 호출부 try 에 먹혀 raw=0 으로
      > 위장됐고(원래 2,285건 소스), 중복 차단은 삽입 조건이 시그니처의
      > seen_ids 를 보고 '이미 있음'으로 판단해 실제로는 안 들어갔다.

> **교훈** — '피드 전용' 6종 중 실제로 피드만 있는 건 절반뿐이었다.
> 코드만 보고 Wayback 으로 넘겼으면 arxiv·xataka_mx·pikabu 세 개를 놓쳤다.
> 페이지네이션 패턴은 **추측하지 말고 페이지 안의 rel=next/링크를 읽는다**
> (`/pagina/N` 을 짐작했다가 404 를 만났다).

## R4 요약

'피드 전용' 6종 중 **4종에 실제 수단이 있었다**(arxiv·xataka_mx·pikabu·lemmy).
코드에 `LIST_PAGES` 가 없다고 수단이 없는 게 아니다. 남은 2종 —
  · 4pda — 403 차단. 우회 수단이 먼저 필요하다.
  · macrumors — RSS 3종뿐. 포럼/Wayback 검토 필요.

덤으로 **zdnet_kr 이 404 로 멈춰 있던 것**을 발견해 복구했다(역사가 아니라
현재 수집이 죽어 있었다).

## R5 — 불가 판정

- [x] **잔여 공백 재측정(2026-09-16)** — 27개 → **11개**. 그중 4개는 이미 불가
      판정이라 실제 남은 건 7개였고, 재확인 결과 **그것도 대부분 불가**였다.
      `LIST_PAGES` 상수만 보고 '가능'으로 분류한 게 원인이다 — ithome·tweakers·
      mobil_se 는 **실제 page 루프가 없다**. 코드 주석이 진실을 말하고 있었다
      ("RSS 단일 페이지", "contract 준수용 상한"). 불가 목록 6→12종.
      등록된 20종이 전부 실제 루프를 갖는지도 시험으로 강제한다.
- [x] 수단이 없는 소스를 목록화하고 사유를 적었다 (`deep_page_backfill.UNSUPPORTED`,
      시험으로 강제). 특히 —
      - `fourchan_g` 4chan 은 스레드가 만료돼 사라진다. **역사가 존재하지 않는다.**
      - `sweclockers` LIST_PAGES 가 의례적 상한이었다(주석에 적혀 있었다). 분류 정정.
      - `fmkorea` Playwright 챌린지가 먼저 풀려야 깊이가 의미 있다.
- [x] **lemmy** — Lemmy API 가 `page` 를 지원한다(실측 page 3 이 다른 게시물).
      페이지네이션 도입 + 페이지 간 중복 차단. 실측 raw=60.
      > 이 작업 중 **내가 만든 버그 2건**을 시험이 잡았다 — 파싱 함수를 분리하며
      > `query` 인자를 빠뜨려 NameError 가 호출부 try 에 먹혀 raw=0 으로
      > 위장됐고(원래 2,285건 소스), 중복 차단은 삽입 조건이 시그니처의
      > seen_ids 를 보고 '이미 있음'으로 판단해 실제로는 안 들어갔다. 필요

## 기록

| 라운드 | 날짜 | 대상 | 이전 | 이후 | 비고 |
|---|---|---|---|---|---|
| — | 2026-09-15 | (기준선) | 코퍼스 2026 이전 약 135,000 | — | 88 활성 중 백필 커버 약 15 |
| R1 | 2026-09-15 | reddit_rss 2023 | 0 | **492** | r/samsung 5 페이지분. 커서 2023-12-27 에서 이어받는다 |
| R2 | 2026-09-15 | ppomppu p80~83 | — | **3** (2018년) | 커서 틀 구축. 20건 중 3건 신규 = 그 구간은 거의 보유 중 |
| R2 | 2026-09-15 | dogdrip 셀렉터 | 날짜 12% | **100%** | 수집 시점. 넉 달간 88%를 버리고 있었다 |
| R2 | 2026-09-15 | dogdrip 소급복구 | NULL 9,211 | **6,791** | 150 URL 로 2,326건. 남은 667 URL, 매일 150씩 |
| R2 | 2026-09-15 | 글로벌 포럼 | 대상 8 | **11** | kaskus 실측 126건. 불가 2종 사유 기록 |
| R3 | 2026-09-15 | WP 매체 | 3 | **6** | 실측으로 가려 채택. 무시형 1종 차단 |
| R3 | 2026-09-15 | wpnews 2022 상반기 | 184 | **1,212** | 새 매체 기여는 9건 — 나머지는 기존 3매체의 미수집분 |
| R3 | 2026-09-15 | wpnews 창 단위 | 연 | **월** | 고volume 구간에서만 이득. 2019-12 는 +2 |
| R2 | 2026-09-15 | 깊이 백필 대상 | 11 | **20** | 전수 분류 기반. p40 실측 phonearena 150·tecnoblog 29 |
