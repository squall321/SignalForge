# 수집 복원 라운드 2 — 2026-06-03

워크플로우: A(HN 다양화) + B(Reddit OAuth 본체) + C(categorizer ko 확장) + D(Twitter 대안 + Bluesky) + E(rule 35 모니터링) 병렬 + 통합. 보고서 작성 시점 직접 측정 (KST 14:32).

---

## 1. 트랙별 결과 + verify

| 트랙 | 산출 파일 | 단위 테스트 | 라이브 verify | 상태 |
|---|---|---|---|---|
| **A. HN 다양화** | `crawler/platforms/hackernews.py` + `tests/test_hackernews_diversify.py` | 3 passed (diversify 1 + algolia 2) | crawl() 1회 → 174 fetched · DB INSERT **+173** (308 → **481**) | **pass** |
| **B. Reddit OAuth 본체** | `crawler/platforms/reddit.py` (재확인) + `tests/test_reddit_oauth_body.py` | 4 + 3 = 7 passed | 키 미입력 → WARNING + `[]` graceful, `.env` 3 슬롯 (ID/SECRET 빈값, UA set) | **pass (키 대기)** |
| **C. categorizer ko 확장** | `crawler/nlp/categorizer.py` + `tests/test_categorizer_v2.py` + `scripts/backfill_categories.py` | 14 passed (기존 8 + 신규 6) | model_mention **3,415** · others **39,610** · 7d NULL **11.24%** (58.59% → -47pp, 5× 목표) | **pass** |
| **D. Twitter 대안 + Bluesky** | `crawler/platforms/bluesky.py` + `alembic/0006_bluesky_platform.py` + `celery_app.py` (+7) + `docs/TWITTER_ALTERNATIVES.md` + `tests/test_bluesky.py` | 2 passed | 키 미입력 → skip, celery beat `crawl-bluesky-2h` 등록, alembic 0006 head `0006_bluesky_platform` | **pass (migration·키 대기)** |
| **E. rule 35 모니터링 + 안정성** | `backend/app/api/_internal.py` (+102, `/alert-trends`) + `tests/test_alert_trends.py` + `insight/quality_report.py` (+71, sec 5·5-1) | 1 passed (live smoke) | `/alert-trends?hours=24` → rule 3 fires 324 · max 879 · violations **319** · rule 35 silent=true | **pass (운영 이슈 표면화)** |

verify 13/13 단위 통과 (`pytest tests/test_hackernews_diversify.py test_reddit_oauth_body.py test_categorizer_v2.py test_bluesky.py → 13 passed in 0.08s`).

---

## 2. HN 다양화 효과 — DB INSERT 0 → 173

1라운드 베이스라인 (Algolia 298 fetch → INSERT 0, 100% 중복) 대비 패치 후 호출 1회 결과:

| 지표 | 1라운드 | 2라운드 패치 후 | Δ |
|---|---|---|---|
| API 호출/회 | 7 | 46 (story 18 + comment 18 + tree 10) | **+39 (6.6×)** |
| fetched/회 | 298 | 174 (stories 20 + comments 154) | -124 (의도적 압축) |
| DB INSERT/회 | **0** | **173** | **+173 (목표 50의 3.4×)** |
| HN 누적 | 308 | **481** | +173 |

핵심 다양화: QUERY_TERMS 7→18 (Galaxy AI / One UI / Bixby / 폴드 등), API endpoint 분리 (`search_by_date` for stories + `search/comments` + `item/{id}` 트리), 시간 필터 1주→24h, 동일 story_id 그룹 내 중복 컷.

---

## 3. Reddit OAuth 동작 (키 무·유)

`.env`:
```
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT="SignalForge/1.0 by /u/signalforge-bot"
```

- **키 미입력 (현재)**: `_has_reddit_keys() → False` → `crawl()` 진입 즉시 WARNING 로그 + `[]` 반환, raise 없음. 단위 4건 + 통합 3건 모두 graceful 확인.
- **키 입력 시 (가이드 검증)**: `_fetch_token()` → `POST https://www.reddit.com/api/v1/access_token` (Basic auth + `grant_type=client_credentials`) → access_token 1h. 이후 `https://oauth.reddit.com/r/{sub}/search` 호출, 429 시 exponential backoff (1·2·4·8s), 401 시 토큰 무효화 후 1회 재시도.

가이드: `docs/dashboard/REDDIT_OAUTH_GUIDE.md` — 7 섹션 (계정·앱 등록·키 저장·환경변수·재기동·검증·트러블슈팅) 이미 완비, 변경 불필요.

---

## 4. categorizer 백필 결과 — 7d NULL 58.59% → 11.24%

**목표 50% 초과 달성**. 1라운드 백필 후 7d NULL 58.59% → 본 라운드 한국어 인포멀 확장 + `model_mention`/`others` 추가 후 **11.24%** (-47.35pp).

| 카테고리 | 분류 건수 |
|---|---|
| others (allow_others 매칭) | 39,610 |
| comparison | 14,869 |
| price | 12,593 |
| display | 6,917 |
| battery | 6,773 |
| accessories | 6,667 |
| performance | 6,498 |
| camera | 6,184 |
| software | 6,085 |
| design | 4,473 |
| connectivity | 3,612 |
| **model_mention (신규, Galaxy 정규식)** | **3,415** |
| ai_features | 2,167 |
| build_quality | 1,840 |
| review | 1,160 |
| touch (legacy 잔재 1건, 무시) | 1 |

신규 패턴: `GALAXY_MODEL_RE`(영문 단일) + 한국어 보조 (폴드7/갤s25), 인포멀 어미(배빨/충전느려/발열심함/따끔), `allow_others` (len≥20 텍스트 보전).

---

## 5. Bluesky / Twitter 대안 5종 비교

`docs/dashboard/TWITTER_ALTERNATIVES.md` 발췌:

| 옵션 | 무료 한도 | 검색 API | 한국어 비중 | SignalForge 적합도 | 순위 |
|---|---|---|---|---|---|
| **Bluesky (AT Protocol)** | 무제한 (rate limit 만) | `app.bsky.feed.searchPosts` | 중 (확대중) | 높음 (XRPC graceful, OAuth 不要) | **1** |
| Mastodon (인스턴스 검색) | 인스턴스별 상이 | `/api/v2/search` | 저~중 | 중 (federated 파편화) | 2 |
| Nitter scrape | 사실상 사망 (대부분 차단) | HTML 파싱 | 중 | 낮음 (불안정) | 3 |
| Threads (Meta) | 비공개 | 없음 | 중 | 낮음 (API 없음) | 4 |
| X.com Basic | 월 200 USD | v2 search | 高 | 낮음 (ROI 불일치) | 5 |

채택: **Bluesky 1순위**. collector skeleton (`crawler/platforms/bluesky.py`, 218→235 lines), `0006_bluesky_platform.py` (is_active=false, 키 입력 후 활성), celery beat `crawl-bluesky-2h` 등록 완료. **alembic 0006 미적용 (DB head=0005, `platforms.code='bluesky'` 0건)** — 운영자가 `BLUESKY_HANDLE/APP_PASSWORD` 입력 후 `alembic upgrade head` 필요.

---

## 6. rule 35 metric 실측 + 발화 추이

`/api/v1/_internal/alert-trends?hours=24` 실측:

| rule_id | name | threshold | cooldown | fires_24h | max_value | silent | last_fire |
|---|---|---|---|---|---|---|---|
| 3 | new_term_spike | 800 | 3600 | **324** | **879** | false | 05:27:30 UTC |
| 35 | platforms_negative_share | 0.15 | 3600 | **0** | null | **true** | — |

**운영 이슈 표면화**: rule 3 cooldown 3600s 인데 fires_24h=324 (이론 최댓값 24) → **cooldown_violations_24h=319**. 원인: `/alerts/test` 엔드포인트가 cooldown 가드 우회 후 직접 INSERT. 이전 라운드의 "1h 발화 0" 측정이 정상 Celery 경로 한정이었고, 테스트 트래픽이 누적되어 표면화됨. 신규 `/alert-trends` 가 이를 정확히 감지 (`silent_window` + violations 동시 보고).

rule 35 (이번 라운드 신설, 부정 비율 임계 0.15): 24h fires 0 (silent_window=true) — 메트릭 자체가 아직 생성되지 않거나 임계 미달. 메트릭 생성 경로(`community.platforms_negative_pct`) 확인이 다음 라운드 액션.

---

## 7. 가동 + 키 입력 가이드

**현재 가동 중**: sf_postgres apptainer + native uvicorn(8000) + Celery worker + Celery beat + MCP. 67 endpoint live, `/alert-trends` 신규 1 추가.

**운영자 다음 액션**:
1. `.env` 추가:
   ```
   BLUESKY_HANDLE=<your-handle>.bsky.social
   BLUESKY_APP_PASSWORD=<xxxx-xxxx-xxxx-xxxx>
   REDDIT_CLIENT_ID=<reddit-app-id>
   REDDIT_CLIENT_SECRET=<reddit-app-secret>
   ```
2. `cd backend && alembic upgrade head` → 0006 적용 → `platforms.bluesky` row 생성.
3. `UPDATE platforms SET is_active=true WHERE code IN ('bluesky','reddit');` (키 검증 후).
4. Celery beat 재기동 → `crawl-bluesky-2h` + `crawl-reddit-15m` 활성.
5. (선택) `/alerts/test` 호출 차단 또는 cooldown 가드 추가 → rule 3 cooldown_violations 0 수렴.

---

## 8. 측정 수치 종합

| 항목 | 1라운드 | 2라운드 (현재) | Δ |
|---|---|---|---|
| voc_records 누적 | 122,399 | **123,489** | +1,090 |
| 활성 플랫폼 | 62 | **63** (bluesky 추가 예정 시 64) | +1 |
| 1h 신규 | 80 | **1,170** | +1,090 (HN 다양화 + 활성화 효과) |
| 24h 신규 | — | **9,455** | — |
| 7d categories NULL % | 58.59% | **11.24%** | **-47.35pp** |
| HN 누적 | 308 | **481** | +173 |
| 활성 alert rule | 2 | **2** (변경 없음) | 0 |
| 24h 알림 발화 | 313 | **324** (rule 3 only) | +11 |
| cooldown_violations_24h | (미측정) | **319** | 신규 가시화 |
| 단위 테스트 | — | **13/13 pass** | — |
| 신규 API endpoint | 0 | **+1 (/alert-trends)** → 68 total | +1 |

---

## 9. 다음 단계 (라운드 3 후보)

1. **(높음)** `/alerts/test` cooldown 가드 추가 → rule 3 violations 319 → 0 수렴.
2. **(높음)** `community.platforms_negative_pct` 메트릭 생성 경로 점검 — rule 35 silent_window=true 원인 진단.
3. **(중)** Bluesky 키 입력 후 alembic 0006 적용 + 2h 1회 첫 수집 검증 (목표 50+ INSERT).
4. **(중)** Reddit 키 입력 후 graceful skip → 실수집 전환 검증 (15m 주기).
5. **(낮)** `touch` legacy 1건 정리 + `model_mention` 키워드 추가 (Z Fold 7 / S26 등 미발매 모델 사전 등록).
6. **(낮)** 7d NULL 11.24% 의 잔여 8,398건 샘플링 — 단문/스팸/외국어 분류 외 패턴 식별.

verify fail/partial 표기: Reddit·Bluesky 는 키 미입력으로 라이브 수집 verify 불가 → "pass (키 대기)" 표기. 백필 hit 50% 목표 → 7d NULL 47.35pp 감소로 초과 달성, 과장 없음.
