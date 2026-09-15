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

- [ ] theqoo · instiz · dogdrip · ruliweb · bobaedream · slrclub(사이트 복구 후)
- [ ] 글로벌 포럼 — kaskus · lowyat · resetera · gsmarena_forum · donanimhaber
- [ ] 각 소스의 "바닥" 확인 (몇 페이지까지 실제로 존재하나)

## R3 — 사이트맵/아카이브 (유형 C)

- [ ] 워드프레스 계열 sitemap.xml 지원 여부 조사 (sammobile · sammyfans · phandroid · tecnoblog · hipertextual …)
- [ ] 연도별 아카이브 URL 패턴이 있는 곳 목록화
- [ ] 공통 sitemap 백필 유틸 (소스별 스크립트 중복 방지)

## R4 — Wayback (유형 D)

- [ ] 위 수단이 없는 뉴스 소스를 Wayback 으로 소급 (`wayback_kr_backfill.py` 확장)

## R5 — 불가 판정

- [ ] 수단이 없는 소스를 목록화하고 **사유를 적는다**. 억지로 만들지 않는다.

## 기록

| 라운드 | 날짜 | 대상 | 이전 | 이후 | 비고 |
|---|---|---|---|---|---|
| — | 2026-09-15 | (기준선) | 코퍼스 2026 이전 약 135,000 | — | 88 활성 중 백필 커버 약 15 |
| R1 | 2026-09-15 | reddit_rss 2023 | 0 | **492** | r/samsung 5 페이지분. 커서 2023-12-27 에서 이어받는다 |
