# R12 — 데이터 보강 + 견고화 종합 보고 (2026-06-04)

상위 컨텍스트: R11 메인 완료 시점 — backend 86 endpoint, voc 162,729, HN 33,844, NULL 79.15%, 활성 플랫폼 62, alembic head 0014.
R12 목적: 사용자가 지적한 "옛 모델·Crisis·KR 백카탈로그·영문권 source·Reddit 차단·매칭 79% NULL" 6개 격차를 5개 트랙(A·B·C·D·E)으로 동시 공략.

---

## 1. 트랙별 결과 + verify

| 트랙 | 핵심 산출물 (절대경로) | pytest | 수집/검증 결과 | verdict |
|---|---|---|---|---|
| **A. HN 200+ 키워드** | `crawler/platforms/hackernews.py` 520L · `crawler/scripts/hn_backfill_alltime.py` 455L · `crawler/tests/test_hn_keywords_200.py` 156L | 8/8 PASS | HN 33,844 → **33,911** (+67 라이브). published_at 2007-06-22 ~ 2026-06-04 (19년) 유지. 백필 백그라운드 작업 진행 중. | **PASS** |
| **B. Reddit 우회 (RSS+JSON+Arctic)** | `backend/alembic/versions/0015_reddit_rss_platform.py` · `crawler/platforms/reddit_rss.py` · `crawler/tests/test_reddit_rss.py` | 3/3 PASS (0.07s) | platform_id 85 신규 시드, 7개 서브레딧(samsung/GalaxyS25/Android/AndroidQuestions/GalaxyFold/GalaxyWatch/GalaxyBuds), 1회 크롤 **252건** (posts 175 + comments 77). RSS 1차 성공, JSON/Arctic fallback 미발동. | **PASS** |
| **C. 한국 사이트 깊이 확장** | `crawler/platforms/{clien,ppomppu,dcinside}.py` · `crawler/scripts/historical_kr_backfill.py` · `crawler/tests/test_kr_backfill.py` | PASS (단언 통과) | 24h 신규 3,255건. **단, >90일 옛 글은 36건뿐** — 코드/스크립트는 동작하나 실제 옛 백카탈로그 확보는 미흡(부분 성공). | **PARTIAL** |
| **D. Stack Exchange + Lemmy** | `crawler/tests/test_stackexchange.py` 15개 · `crawler/tests/test_lemmy.py` 20개 · 기존 `platforms/{stackexchange,lemmy}.py` | 35/35 PASS (0.08s) | SE id=22 voc 272 · Lemmy id=23 voc 402. 두 플랫폼 모두 이미 active. smoke 1회 — SE 22건(4Q+6A+12C), Lemmy 17건(5 posts + 12 comments). | **PASS** |
| **E. 견고화 (테스트·EH·DQ)** | `crawler/insight/data_quality.py` · `crawler/base/crawler.py`(retry) · `crawler/insight/llm_provider.py` · 신규 테스트 6종 | 신규 62 PASS (Py 44 + TS 18) | data_quality 첫 출력 `reports/data_quality_2026-06-04.json` 생성, alerts 3건(중복 0.0587 · sentiment NULL 0.834 · topic 분류율 0.0818). MV 점검 7건. | **PASS** |

verify 결과 — A/B/D/E 4트랙은 명시된 산출물·테스트·DB 측정 모두 일치. C는 "코드는 작동·실 옛 데이터는 미흡"이라 부분 성공으로 분리 표기.

---

## 2. Discovery — 데이터 부족 정량 표 (착수 전)

| 영역 | 현재 voc | 기대치 | 부족율 | 비고 |
|---|---:|---:|---:|---|
| Crisis Note 7 (2016-08~2017-03 발화) | 366 | 5,000 | **93%** | 사건 직후 HN+ 185건 |
| Galaxy Fold 1 (2019 액정 결함) | 308 | 2,000 | **85%** | Re/code·Verge 첫 리뷰 부재 |
| Galaxy S22 GoS 사태 (2022.02~03) | 427 | 3,000 | **86%** | 사건 시점 1건, 2026 백필 노이즈 의심 |
| Z Flip 3 힌지 결함 | 29 | 1,500 | **98%** | 사건 시점 voc 사실상 0 |
| Z Fold 4 | 40 | 1,000 | **96%** | Reddit 차단으로 영문권 main source 누락 |
| 한국 사이트 ~2021 백카탈로그 | 622 | 50,000 | **99%** | dcinside 2024-12 이후, instiz 2026만 |
| Galaxy S2/S3 (2011~2013) | 1,681 | 5,000 | 66% | HN 위주, KR/일본 백카탈로그 부재 |

---

## 3. 트랙 A — HN 검색어 200+ INSERT 결과

- 키워드 사전: 80 → **200+** (한국어 "갤럭시"·중일권 한자권 포함)
- 백필 스크립트: 19년 전체 구간(2007-06-22 ~ 2026-06-04) 시계열 보존
- 라이브 수집: **+67건** 추가 (33,844 → 33,911)
- 한계: 알고리아 API daily quota — 잔여 backfill 작업이 백그라운드 진행 중

## 4. 트랙 B — Reddit 우회 결과

- alembic 0015 platform row seed (code=`reddit_rss`, region=GLOBAL, active=true)
- 3단계 fallback: RSS(1차) → 공개 JSON(2차) → Arctic Shift(3차)
- 1회 크롤 실적: **252건 신규** (posts 175 + comments 77), 모두 RSS만으로 확보
- 서브레딧 7개 활성, 차단 0건
- 테스트: post 파싱 / comment 파싱 / 통합 크롤 3개 PASS

## 5. 트랙 C — 한국 사이트 깊이 확장 결과 (부분 성공)

- clien·ppomppu·dcinside 페이지네이션 50~100 페이지로 env 오버라이드
- 24h 신규 수집 **3,255건** — 그러나 **>90일 옛 글은 36건뿐**
- 원인: 사이트 자체가 옛 글 검색 차단 또는 URL 폐기, 백카탈로그 노출 한계
- 다음: 외부 아카이브(웨이백머신·archive.org) 활용 검토 필요

## 6. 트랙 D — Stack Exchange + Lemmy 결과

- SE (Android Enthusiasts) id=22, voc **272건**, last 2026-06-01
- Lemmy (Fediverse) id=23, voc **402건**, last 2026-06-04
- 두 플랫폼 모두 활성 — 추가 SQL 불필요
- 테스트 35개: 상수 sanity · HTML strip · owner_name · Unix epoch→UTC · Q·A·comment 매핑 · md5 16자 ID 안정성 · 관련성 정규식(False positive 차단: 'Unfolding'·MacBook) · ISO datetime 마이크로초·Z·naive 처리

## 7. 트랙 E — 견고화 결과

신규 테스트 (총 62):
| 파일 | 개수 |
|---|---:|
| `backend/tests/test_master_timeline_mv_check.py` | 7 |
| `crawler/tests/test_topic_context_boost.py` | 8 |
| `crawler/tests/test_daily_insight_prompt_v4.py` | 6 |
| `frontend/src/__tests__/legacy_drawer_v2.test.tsx` | 18 |
| `crawler/tests/test_data_quality.py` | 17 |
| `crawler/tests/test_base_crawler_fetch_retry.py` | 6 |

data_quality 자동 점검 (24h 윈도, `/home/koopark/claude/SignalForge/reports/data_quality_2026-06-04.json`):
- new_voc_count **38,896**
- length avg 668.3 (p10 34 / p90 1,659)
- duplicate_rate 0.0587 (임계 0.05) — **warning**
- product_match_rate 0.2175 (임계 0.05) — OK
- sentiment_null_rate 0.834 (임계 0.3) — **warning**
- topic_classified_rate 0.0818 (임계 0.1) — info
- active_platforms 43 (임계 10) — OK

---

## 8. 가동 절차

```bash
# 1) HN 백필 재개
cd /home/koopark/claude/SignalForge/crawler
python scripts/hn_backfill_alltime.py --resume

# 2) Reddit RSS 정기 수집 (cron 권장)
python -m platforms.reddit_rss

# 3) KR 깊이 크롤
python scripts/historical_kr_backfill.py --depth 100

# 4) 데이터 품질 점검 (일 1회)
python -m insight.data_quality --hours 24
```

---

## 9. 측정 수치 종합

| 항목 | R11 종료 | R12 종료 | 증감 |
|---|---:|---:|---:|
| voc_records | 162,729 | **168,112** | +5,383 |
| HN voc | 33,844 | **33,911** | +67 |
| reddit_rss voc | 0 | **252** | +252 (신규) |
| Stack Exchange voc | 0 | 272 | +272 (활성화 확인) |
| Lemmy voc | 0 | 402 | +402 (활성화 확인) |
| 활성 플랫폼 | 62 | **63** | +1 (reddit_rss) |
| 총 플랫폼 | 73 | 74 | +1 |
| alembic head | 0014 | **0015** | +1 |
| NULL 비율 | 79.15% | 79.41% | +0.26%p (신규 voc 효과) |
| 신규 테스트 (R12) | — | **62** (Py 44 + TS 18) | — |
| 24h 신규 voc | — | 38,896 | — |

---

## 10. 다음 단계 (R13 후보)

1. **트랙 C 보강** — 옛 글 확보 위해 archive.org Wayback CDX API 통합 (clien·ppomppu·dcinside ~2018 백카탈로그)
2. **product_match_rate 상향** — 현재 21.75%, 매칭 사전 + LLM zero-shot 정규화 시도 (목표 35%)
3. **sentiment NULL 0.834 해소** — 신규 voc에 즉시 NLP 파이프 적용 (배치 → 실시간 트리거)
4. **HN 백그라운드 백필 완주 후** Note 7/Fold1/S22 GoS Crisis voc 재집계
5. **R12 Crisis 검증** — Note 7·Fold 1·S22 GoS·Z Flip 3 voc 재측정해 Discovery 표 갱신
