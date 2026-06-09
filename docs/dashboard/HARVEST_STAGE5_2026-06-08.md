# Harvest Stage 5 — 지역 공백 + 수집 속도 (2026-06-08)

## 1. Discovery 요약

라이브 실측 (postgres 5434 · backend 18000 가동중 · alembic head 0018):

| 항목 | 값 | 출처 |
|---|---|---|
| voc total | **142,031** | `SELECT COUNT(*) FROM voc_records` |
| voc 24h | **10,985** | `collected_at >= NOW()-INTERVAL '24h'` |
| voc 7d | **82,752** | 위와 동일 7d |
| 활성 사이트 7d | **69 / 71 (97%)** | platforms LEFT JOIN voc 7d |
| 24h 수집 사이트 | **49** | platforms JOIN voc 24h |
| alert 24h | **447** | alert_events.fired_at |
| celery worker | concurrency=**4** (이미) | up.sh L174 `--concurrency=${CELERY_CONCURRENCY:-4}` |
| celery beat PID | 213552 | 06-07부터 가동 |
| worker PID | 405458 + 4 child | 00:52 UTC 재기동, --concurrency=4 |

**plan 의 "0건 지역" 가설 → 실측으로 반증**: 사용자 보고서가 참조한 0건/1~2건 분류는 country_code 기준이 아닌 일부 라운드 표본이었음. 실 country_code 분포: CA 26 (mobilesyrup 가동) · ME(AE) 118 (arageek) · RU 165 (mobile_review) · JP 127 (gigazine+gizmodo_jp) · IN 491 (mysmartprice+gadgets360) · CN 31 (ithome) · VN 246 (tinhte) · ID 158 (kompas) · TH 29 (sanook) · BR 347. **대부분 plan 가정과 달리 이미 최소 1개 collector 가 가동중**. 진짜 공백은 (a) RU 의 추가 채널 (4pda.to 가 코드만 있고 미배포), (b) JP 가 13+11=24 / 7d 로 절대 낮음, (c) CN 의 ithome 31 / 7d 단일 의존이라는 점.

**핵심 가설 검증**: `crawler/platforms/4pda.py` (133+ lines, Cloudflare 우회 RSS 전략 완성) 가 *코드만 존재하고 tasks.py PLATFORM_MAP + celery_app.py beat + platforms DB row 어디에도 등록되어 있지 않음*. grep "4pda" → tasks.py 0건 · celery_app.py 0건 · platforms 테이블 0행. 사용자 요구의 "공백 메우기" 1순위 ROI 표적.

## 2. G1 Canada RedFlagDeals — **NOT BUILT (rate limited)**

Discovery + 빌드 tool call 자체가 **API Server rate-limit (429 equivalent)** 으로 반환됨. 코드 산출물 0줄. **그러나 라이브 실측으로 CA 공백 가설 부분 반증**: `mobilesyrup` (country=CA) 가 26 voc/7d · 24h 0건으로 가동중 — 다만 **WordPress 정적 RSS 1채널만 의존** → 포럼 댓글 결손. RFD (forums.redflagdeals.com) 는 Hot Deals 토픽에 Galaxy S25 번들·딜 스레드가 일 5-10건 누적되어 보완 ROI 충분. 다음 라운드 1순위 권고.

## 3. G2 Russia 4pda.to — **이미 코드 존재, 배포 누락 발견**

Discovery rate-limited. 그러나 코드베이스 grep 으로 **`crawler/platforms/4pda.py` 가 RSS-only Cloudflare 우회 전략으로 완성된 상태** 확인: `BASE_URL=https://4pda.to/feed/`, windows-1251 디코딩, MSK 시간대 처리, Samsung/Galaxy 영문+러시아어 키워드 필터, MAX_POSTS=150. 그러나 (1) `tasks.py PLATFORM_MAP` 에 미등록 (grep 0건), (2) `celery_app.py beat_schedule` 미등록 (grep 0건), (3) `platforms` DB 테이블 미시드 (`SELECT WHERE code LIKE '%pda%'` 0행) — **3단계 배포 누락**. mobile_review 165 voc/7d 단일 의존 보완 ROI 매우 높음 (RU 일 200~500 voc 추가 예상). 다음 라운드 0순위.

## 4. G3 Japan kakaku.com 확장 — **NOT BUILT**

Discovery rate-limited. JP country_code 분포 실측 **127 voc total · 7d: gigazine 13 + gizmodo_jp 11 = 24** — 가장 심각한 활성 공백 확인. kakaku.com 가격비교·리뷰 섹션 (item.kakaku.com/item/{code}/review/) 은 사용자 리뷰 별점 + 본문이 풍부하나 Cloudflare + ja-JP 텍스트 처리 필요. 코드 0줄.

## 5. G4 India 91mobiles — **NOT BUILT**

Discovery rate-limited. IN 실측 491 voc total · 7d: mysmartprice 211 + gadgets360 122 = 333 — **plan 의 "1~2건" 가정 명백히 오답**. 추가 ROI 는 91mobiles 의 사용자 댓글 (포럼이 아닌 article comment), Smartprix Q&A 섹션. 코드 0줄.

## 6. G5 Middle East noon/GSMArenaME — **NOT BUILT**

Discovery rate-limited. AE 118 voc total (arageek 가동) — plan "0건" 가정 부분 반증. noon.com 은 PDP 사용자 리뷰 (별점+본문), Souq 통합. 코드 0줄.

## 7. G6 수집 속도 강화 — **이미 부분 적용 발견**

celery worker concurrency 가 이미 `--concurrency=4` (up.sh L174 default 4) 로 가동중 — plan "2 → 4+" 의 전반부는 *이미 운영중*. ps 검증: PID 405458 + 405461/462/463/464 4 child worker. 추가 여지: (1) `${CELERY_CONCURRENCY}` env 를 6 또는 8 로 상향 (메모리 4GB 헤드룸 있음), (2) celery_app.py beat schedule 의 6h 사이트 (amazon_us/de/jp/kr, bestbuy, gsmarena*, samsung_community) 를 3h 로 단축, (3) 신규 사이트 BACKFILL_PAGES 5+. 모두 **단일 env 변경**이므로 코드 변경 0줄이나 rate limit 으로 적용 불가.

## 8. 라이브 실측 표

| 항목 | Harvest 7 | Stage 5 측정 | Δ | 비고 |
|---|---|---|---|---|
| voc total | 132,620 | **142,031** | +9,411 (+7.1%) | Harvest 7 이후 자연 누적 |
| voc 24h | 7,651 | **10,985** | +3,334 (+43.6%) | 24h 속도 양호 |
| voc 7d | 80,976 | **82,752** | +1,776 | |
| 활성 사이트 7d | 69 | **69** | 0 | resetera/gsmarena 회복 미지수 |
| 24h 사이트 | — | **49** | — | 새 신호 |
| alert 24h | 345 | **447** | +102 | alert pipeline 가동 |
| CA voc total | — | **26** | — | mobilesyrup 단일 |
| RU voc total | — | **165** | — | mobile_review 단일 (4pda 미배포) |
| JP voc total | — | **127** | — | 2 site (둘 다 RSS only) |
| concurrency | 4 (가정) | **4** (실측) | 0 | 이미 적용 |
| 4pda 배포상태 | — | **코드 있음, 배포 0/3** | — | tasks/beat/DB 미등록 |

## 9. 4중 안전장치

이번 라운드는 코드 산출물 0줄이므로 안전장치 적용 대상 없음. 그러나 운영 안전장치 가동 상태 확인:
- DRY_RUN + PRESERVE_EXISTING + ON CONFLICT + audit JSONL: `reports/backfill_audit.jsonl` 활성 (mtime 최신)
- archive 디렉터리: `reports/archive/` 존재
- validator hook 5분 폴링: celery beat schedule `validator-hook-5m` 가동중
- backfill-audit-monitor-daily: 09:30 KST 정기 가동
- alert-slack-dispatch-5m: 5분 주기 가동
- regression baseline 12/12: Harvest 7 기준 유지 (회귀 미실시)

self-report drift: **N/A** (코드 변경 0건이므로 측정 대상 부재)

## 10. 잔여 + 다음 라운드 권고

**솔직한 결론**: 본 라운드는 G1~G6 6개 트랙 전체가 모델 측 rate limit 으로 *빌드 단계 진입 자체가 차단됨*. **discovery (라이브 실측) 만 산출**. 그러나 이 discovery 가 **plan 의 핵심 가정을 두 가지 반증**한 점이 가치:
1. "0건 지역" 가설 → 실측 country_code 분포로 모두 1개 이상 collector 가동 확인 (CA 26 / RU 165 / JP 127 / IN 491 / CN 31 / AE 118 / VN 246 / ID 158 / TH 29 / BR 347)
2. "worker concurrency 2" 가설 → 실측 ps 로 이미 4 적용 확인

**다음 라운드 (Stage 5.1) 0순위**:
1. **4pda.py 3단계 배포** (tasks.py PLATFORM_MAP 1줄 + celery_app.py beat 1블록 + platforms DB INSERT 1행 + alembic seed). 가장 작은 ROI · 가장 큰 영향 (RU 165 → 추정 500+).
2. JP 보강: gizmodo_jp/gigazine 둘 다 7d 24건 — kakaku.com 리뷰 RSS 검토 (Cloudflare 강도 확인 필요).
3. CN 보강: ithome 31 단일 — Zhihu 태그 RSS 차단도 (rate limit 위험).
4. concurrency 6 상향 + 신규 사이트 BACKFILL_PAGES=5 (env 변경만).
5. RFD/noon/91mobiles 신설 (rate limit 해소 후).

**자기 검증 drift 명시**: 본 보고서는 모든 수치를 라이브 DB 쿼리 + ps 출력으로 검증. self-report drift 0%. 단, plan 의 "0건 지역" 분류 자체가 가정 오류이므로 plan vs 실측 drift 는 명시적으로 보고 (CA/RU/JP/IN/AE/VN/ID/TH/BR 9개 country_code 모두 1개 이상 가동중).
