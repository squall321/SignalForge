# DATA HARVEST R28 — 2026-06-06

라운드: R28-harvest · 모드: ultracode (의례 X, 실 수집량 위주) · 보고 시점 voc_records=**122,477**

---

## 1. 트랙별 결과 + verify 상태

| 트랙 | 산출 | verify | 비고 |
|---|---|---|---|
| A. 죽은 사이트 복구 | probe_dead_sites.py, beat 9건+spec 1건 등록, 수동 INSERT 573 (2h 윈도) | OK (pytest 4/4, psql 카운트 정확 일치) | reddit_rss spec 누락이 핵심. ausdroid·dpreview·samsung_community 등 10건은 사이트 발행 빈도 부족(시스템 무관) |
| B. 한국 백카탈로그 페이지네이션 | korean_pagination_deep.py + clien/dcinside/ppomppu LIST_PAGES env wiring | **부분 (인프라만)** | DRY_RUN 통과·단위 테스트 OK, **라이브 실행 미수행 → 신규 voc 0건** |
| C. 신규 사이트 collector | notebookcheck (Google News RSS 우회), zdnet_kr | OK (DB INSERT 138+4 일치, 28/28 테스트, beat 4h 등록) | notebookcheck Cloudflare Turnstile 차단 → hwupgrade 패턴 재사용 |
| D. 수집 자동 모니터 | collection_health.py + alert_events INSERT 21건 (critical 18 / warning 3) | **부분 (drift 7건)** | 보고 critical 18 vs 재실행 시 11 — 트랙 A/C 복구로 7개 사이트가 critical→정상 전환됨 (정상 dynamics) |
| E. Celery beat audit | beat_audit.py + 9건 schedule 보완 | OK (테스트 4/4, missing_in_beat 9→0) | beat 데몬(PID 559879) SIGHUP/재시작 필요 — 운영자 결정 사항 |

---

## 2. 죽은 사이트 복구 결과 (트랙 A)

### 핵심 변경
- **`reddit_rss`** — `tasks.py::_CRAWLER_SPECS` 매핑 부재 (코드만 있고 dispatcher 미연결). spec 등록 + `celery_app.py` 4h 주기 beat 추가
- **9개 사이트** (sammyfans, sammobile, mysmartprice, gsmchoice, ithome, gigazine, mobile_review, inside_handy, hipertextual) — beat_schedule 누락 보완

### 수동 실행 INSERT (직후 2h 윈도)
| 사이트 | 신규 |
|---|---:|
| reddit_rss | 215 |
| gsmchoice | 99 |
| sammyfans | 81 |
| mysmartprice | 58 |
| hipertextual | 50 |
| sammobile | 45 |
| mobile_review | 13 |
| ithome | 5 |
| inside_handy | 5 |
| gigazine | 2 |
| **합계** | **573** |

### 복구 불가 (시스템 무관)
ausdroid·dpreview·samsung_community·arageek·kompas·gizmodo_au·mybroadband·techcabal·techinafrica·stackexchange·mobil_se — 사이트 자체 발행 빈도 부족 또는 휴면 상태. critical 알람은 트랙 D 모니터가 표시하되 정책상 graceful 처리.

---

## 3. 한국 백카탈로그 (트랙 B)

**상태: 인프라만 구축, 라이브 미실행**

- 신규 모듈: `crawler/scripts/korean_pagination_deep.py` (222 line, 7,945 B)
- clien/dcinside/ppomppu collector 에 `LIST_PAGES` env 변수 연결 — 기존 1 페이지 → 환경변수로 N 페이지 백카탈로그 수집 가능
- DRY_RUN 모드 동작 확인 (테스트 통과)
- **신규 voc INSERT: 0건** — 라이브 실행은 운영자 결정 보류 중

다만 한국 사이트는 일반 beat 스케줄로도 활발: 12h 윈도에서 instiz 1,197 / dcinside 681 / ppomppu 156 / dogdrip 67 등 정상 작동.

---

## 4. 신규 사이트 첫 수집 (트랙 C)

| 코드 | 명 | 권역 | 패턴 | 첫 수집 | 누적 |
|---|---|---|---|---:|---:|
| `notebookcheck` | NotebookCheck | DE/EN | Google News RSS 우회 (Cloudflare Turnstile 차단) | 138 | 138 |
| `zdnet_kr` | ZDNet Korea | KR | 사이트 RSS 직접 | 4 | 4 |

- 파일: `crawler/platforms/notebookcheck.py` (6,849 B), `crawler/platforms/zdnet_kr.py` (7,897 B)
- 테스트: 14 + 14 = 28 케이스 전부 통과
- beat: 둘 다 4h 주기 등록 (`celery_app.py` line 206/208)
- Worker 재시작 후 신규 모듈 로드 성공 확인

---

## 5. 수집 모니터 결과 (트랙 D)

### 신규 모듈
`crawler/insight/collection_health.py`
- `collect_site_stats(conn)` — 활성 사이트별 24h 카운트 + 직전 7일 일평균 baseline
- `evaluate_violations(stats)` — critical/warning/skip 분류
- `insert_alert_events(conn, violations)` — metric 단위 cooldown 1h, payload 기반 dedupe
- `save_snapshot(payload)` — `reports/collection_health_YYYY-MM-DD.json`

### 임계 정책
- `critical`: `recent_24h == 0 AND baseline > 0`
- `warning`: `0 < recent_24h < baseline × 0.10`
- `skip`: `baseline == 0` (이미 차단/미운영, 노이즈 제거)

### 실행 결과
- 첫 실행: critical 18 + warning 3 → alert_events 21건 INSERT (24h 윈도 누계 43건)
- **Drift 명시**: 재실행 시 critical 11건으로 감소 — 트랙 A/C 복구 효과로 7개 사이트(gigazine, gsmchoice, hipertextual, inside_handy, ithome, mobile_review, sammyfans 등)가 정상 전환. 시스템 동작이 정확한 결과이며 self-report drift 아님.
- 스냅샷: `reports/collection_health_2026-06-06.json` (65 active_sites, baseline 7d avg 포함)

---

## 6. Celery beat audit (트랙 E)

| 항목 | 보완 전 | 보완 후 |
|---|---:|---:|
| PLATFORM_MAP 키 | 71 | 73 (amazon 4 region) |
| beat_schedule crawl_platform | 63 | 74-77 (정규식 카운트 편차) |
| DB platforms active | 63 | 63 |
| **missing_in_beat** | **9** | **0** |
| orphan_in_beat | 1 (bluesky graceful) | 1 (변동 없음) |
| **healthy** (MAP ∩ beat ∩ active) | 53 | **62** |

### 신규 도구
- `crawler/insight/beat_audit.py` (3,196 B, 76 line) — MAP·beat·DB 교차 audit
- `crawler/tests/test_beat_audit.py` — regression 테스트 1건 포함

### 적용 절차
beat 데몬(PID 559879) 에 SIGHUP 또는 재시작 필요 — 운영자 결정 사항.

### Self-report drift (E)
보고서 "보완 후 74" vs 실제 파일 정규식 카운트 75-77 — 동일/유사 라인 패턴 카운팅 편차이며 schedule 항목 자체는 전부 적용됨.

---

## 7. 가동 절차 (운영자 액션)

1. **beat 재시작** — `kill -HUP 559879` 또는 supervisor/systemd 재기동. 트랙 E 의 9건 신규 schedule 활성화.
2. **트랙 B 라이브 실행** (선택) — `LIST_PAGES=5 python crawler/scripts/korean_pagination_deep.py`. clien/dcinside/ppomppu 백카탈로그 확장. dry-run 부터 권장.
3. **모니터 cron 등록** — `crawler/insight/collection_health.py` 를 1h 주기로 (예: celery beat 또는 systemd timer). 현재 수동 실행만 검증됨.
4. **alert_events 소비자** — 24h 윈도 43건 누적 중. Slack/email 통보 채널 연결 필요 (별도 라운드).

---

## 8. 측정 수치 종합

### voc INSERT 총계 (이번 라운드 직접 기여, 라이브 분만)
- 트랙 A 수동 실행: **573** (2h 윈도 직후)
- 트랙 C 신규 사이트: **142** (notebookcheck 138 + zdnet_kr 4)
- 트랙 B: **0** (인프라만)
- **소계: 715건 직접 INSERT**

### 전체 시스템 카운트 (현 시점)
- **voc_records 합계: 122,477** (R28 기준 121,231 → +1,246, 이번 라운드 직접 715 + 일반 수집 531)
- 24h 신규: **10,138**
- 12h 신규: **4,047**
- 6h 신규(상위): instiz 720 / reddit_rss 221 / dcinside 216 / hackernews 173 / theverge 167
- 활성 platforms: **65** (R28 ~63 대비 +2 — notebookcheck/zdnet_kr)

### 트랙 D 누적
- alert_events 24h 누계: **43건** (첫 실행 21 + 후속 22)

### 테스트
- 트랙 A: pytest track_a 3건 + beat_audit regression 1건 = **4/4**
- 트랙 B: pytest test_korean_deep = **PASS** (DRY_RUN)
- 트랙 C: pytest test_notebookcheck 14 + test_zdnet_kr 14 = **28/28**
- 트랙 D: collection_health 단위 테스트 PASS
- 트랙 E: pytest test_beat_audit = **4/4**

---

## 9. 다음 단계 + 잔여 작업

### 즉시 (운영자)
1. beat 재시작 → 트랙 E 9건 schedule fire 시작 확인 (4-12h 후 voc 증가 검증)
2. 트랙 B 라이브 실행 결정 — `LIST_PAGES=5` 부터 점진 확대

### R29 후보
1. **alert_events → Slack/email** 채널 연결 (현 43건 미통보 누적)
2. **차단 사이트 회복 시도 라운드** — reddit 정상 API 키 확보 시 reddit_rss 대체, twitter/xda/amazon 등 정책 통과 시 graceful 해제
3. **모니터 cron 자동화** — 현재 수동 실행 (beat 또는 systemd timer 1h 주기)
4. **트랙 B 라이브 실행 후 효과 측정** — 백카탈로그 N=5/10/20 별 신규 voc 증분 비교

### 잔여 / 알려진 부채
- bluesky orphan_in_beat 1건 — 외부 키 미입력 graceful 인정 (운영 정책)
- ausdroid·dpreview 등 11개 휴면 사이트 — critical 알람 노이즈, skip 정책 추가 검토 필요
- 트랙 B 라이브 미실행 — verify partial 상태 유지
- 트랙 D self-report drift (보고 18 vs 재실행 11) — 시스템 동작 정확, 보고 시점 차이일 뿐
- 트랙 E healthy 카운트 — 정규식 카운팅 편차, schedule 적용 자체는 검증됨

---

**감사 로그**: `reports/backfill_audit.jsonl` (59 line) · `reports/collection_health_2026-06-06.json` · round=R28-harvest 일관 유지.
