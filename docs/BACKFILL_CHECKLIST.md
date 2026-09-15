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
- [ ] 403 매체(PhoneArena·XatakaMX 등)에 다른 수단이 있는지 — sitemap / Wayback
- [ ] 나머지 wp-json 보유 크롤러 검토 (arageek · jagatreview · mobile_review · techinafrica)

## R4 — Wayback (유형 D)

- [ ] 위 수단이 없는 뉴스 소스를 Wayback 으로 소급 (`wayback_kr_backfill.py` 확장)

## R5 — 불가 판정

- [ ] 수단이 없는 소스를 목록화하고 **사유를 적는다**. 억지로 만들지 않는다.

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
