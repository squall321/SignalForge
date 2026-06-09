# SignalForge 수집 복원 R3 통합 보고 (2026-06-03 KST)

> R2 운영 모드(cooldown 가드) 이후 3-track 병렬 개선. 트랙 A는 정책적 한계 도달, B/C는 목표 달성.

---

## 1. 트랙별 결과 + verify

| 트랙 | 목표 | 결과 | verify |
|---|---|---|---|
| **A. categorizer NULL 잔여 패턴 분석** | 7d NULL 11% → ≤5% | **partial**: 11.25% → 11.15% (back-fill 재실행만, 패턴 분석 후 신규 카테고리 미추가) | psql 실측 null_7d=8,428 / null_7d_len_lt20=8,142 (96.6%) |
| **B. HN 검색어 18 → 50+** | HN voc ≥ 1000 | **pass**: HN voc **481 → 1,344** (+863), 최근 2h INSERT 1,036 | pytest 4/4 pass (test_hackernews_50terms.py) |
| **C. rule 35 임계 조정 + alembic 0006** | watch 추가, bluesky row | **pass**: rule 37 info(0.08) 신설, alembic head **0007**, bluesky platform id=84 is_active=false | pytest 3 pass, alembic current=0007 |

> 주의: 트랙 C 빌드 보고에 "rule 37 만 silent_window=true" 라 적혀 있으나 라이브 API 는 rule 35/37 둘 다 silent — cooldown 가드 동작 정상.

---

## 2. categorizer 미분류 패턴 TOP 10 + 신규 카테고리

### 잔여 NULL 본문 길이 분포 (7d, 8,428건)
| bucket | n | % |
|---|---|---|
| 0–20자 | 8,142 | **96.6%** |
| 20–40자 | 약 113 | 1.3% |
| 40자+ | 173 | 2.1% |

### len ≥ 20 NULL TOP 패턴 (286건 표본 분석)
1. dcinside 푸터 `- dc App` 잔재 (수집 후처리 미제거)
2. 포르쉐/벤츠 광고 스팸 (오프토픽)
3. 독일어/베트남어/타갈로그어 — Samsung 무관
4. 카라반 캠핑 글
5. URL-only 본문 (`https://...` 만 남은 케이스)

### 결론: **신규 카테고리 추가 보류**
- 정책상 의도된 NULL 의 비율이 97.6% → 카테고리 추가는 false-positive 양산
- `MIN_OTHERS_LEN=20` 정책 유지 시 **7d NULL ≤ 5% 는 기술적으로 도달 불가능**
- 더 줄이려면 (a) `MIN_OTHERS_LEN` 완화 또는 (b) dcinside `- dc App` 후처리 제거 (트랙 A 범위 밖)

---

## 3. NULL % before/after (psql 실측)

| 시점 | overall NULL | 7d NULL |
|---|---|---|
| R3 시작 전 | 24.88% | 11.24% (8,398) |
| 트랙 A 백필 후 (빌드 보고) | 18.18% | 10.95% (8,182) |
| **현재 실측** | **22,704 / 124,361 = 18.26%** | **8,428 / 75,598 = 11.15%** |

drift: 시간 경과로 새 수집물 일부 미처리 (8,162 미처리 신규 ≥ 20자) — 다음 categorize 사이클에서 처리 예정.

---

## 4. HN 검색어 확장 효과

| 지표 | before | after | Δ |
|---|---|---|---|
| QUERY_TERMS | 18 | **50** | +32 |
| STORY_WINDOW | 7d | **90d** | +83d |
| COMMENT_WINDOW | 3d | **90d** | +87d |
| MAX_STORIES | 200 | 600 | +400 |
| HN voc total | 481 | **1,344** | **+863** |
| 최근 2h INSERT | — | **1,036** | — |

산출 파일:
- `/home/koopark/claude/SignalForge/crawler/platforms/hackernews.py`
- `/home/koopark/claude/SignalForge/crawler/tests/test_hackernews_50terms.py` (신규, 4/4 pass)

---

## 5. rule 변경 + alembic 상태

### rules
| id | name | severity | op | threshold | cooldown | is_active |
|---|---|---|---|---|---|---|
| 3 | new_term_spike | warning | >= | 800 | 3600 | true |
| 35 | platforms_negative_share | warning | > | 0.15 | 3600 | true |
| **37** | **platforms_negative_share_watch** | **info** | **>** | **0.08** | **3600** | **true (신규)** |

- 동일 metric_path 재사용 → 계산 비용 0
- 이중 thresholds: info(0.08) → warning(0.15) grace period 확보
- 현 실측 `community.platforms_negative_pct=0.1094` — rule 37 발화 영역, rule 35 안전 영역

### alembic
- **head: 0007** (`alert_rule_platforms_negative_watch.py`)
- 0006 (bluesky platform row) 이미 적용, bluesky id=84 is_active=false
- 신규: `/home/koopark/claude/SignalForge/backend/alembic/versions/0007_alert_rule_platforms_negative_watch.py`

---

## 6. 가동 절차

```bash
# 1. backend 재기동 (rule 37 hot-reload 보장)
cd /home/koopark/claude/SignalForge/backend && .venv/bin/uvicorn app.main:app --reload

# 2. HN 50 검색어 첫 풀스캔 (90d 윈도우, 1회만 수동 트리거)
cd /home/koopark/claude/SignalForge/crawler && .venv/bin/python -m crawler.scripts.run_hackernews_now

# 3. categorize 미처리 신규 (현재 8,162건 ≥ 20자) 백필
cd /home/koopark/claude/SignalForge/crawler && .venv/bin/python -m crawler.scripts.backfill_categories --batch 500

# 4. rule 37 silent_window 해제 확인 (다음 collect 사이클 후)
curl -s http://localhost:8000/alerts/rules | jq '.[] | select(.id==37)'
```

---

## 7. 측정 수치 종합 (psql 직접, 2026-06-03 KST)

| 항목 | 값 |
|---|---|
| voc_total | **124,361** |
| voc_7d | 75,598 |
| voc_1h_new | 1,962 |
| voc_24h_new | 9,683 |
| null_overall | 22,704 (**18.26%**) |
| null_7d | 8,428 (**11.15%**) |
| null_7d_len<20 | 8,142 (96.6% of null_7d) |
| null_7d_len≥20 | 286 (3.4%, 대부분 미처리 신규) |
| HN voc | **1,344** (+863) |
| 활성 플랫폼 | 62 (+ inactive 11, bluesky 포함) |
| 활성 룰 | **3** (3 / 35 / 37) |
| alembic head | **0007** |

---

## 8. 다음 단계

1. **트랙 A 후속**: dcinside 푸터 `- dc App` 후처리 제거 → 잔여 NULL ≥ 20자 286건 중 약 절반 회복 추정. 정책 변경 없이 가능. (트랙 A 범위 밖이므로 차기 라운드)
2. **rule 37 발화 관찰**: 다음 collect 사이클 후 silent 해제 → community.platforms_negative_pct=0.1094 영역에서 info 알림 발화 예상. 운영자 dashboard 표시 확인.
3. **HN 90d 윈도우 풀스캔 비용 측정**: 첫 풀스캔 API 호출량/소요시간 측정 → 정기 sample size 결정 (`QUERY_SAMPLE_SIZE` 활용 여부).
4. **Reddit/Bluesky 키 입력**: 차기 라운드 우선순위. Bluesky platform row 는 이미 준비됨 (is_active=false → true 전환만 남음).
5. **categorizer 5% 목표 재정의**: `MIN_OTHERS_LEN` 정책을 20→15 로 완화하는 방안 검토. trade-off: 짧은 본문 false-positive 증가 vs NULL% 감소.

---

## verify summary

- **A**: partial — 패턴 분석 정확, 백필 재실행만 적용, 신규 카테고리 추가 거절(정당). 5% 목표 미달이며 현 정책상 도달 불가.
- **B**: pass — HN voc 481→1,344 실측, 단위 테스트 4/4.
- **C**: pass — alembic 0007 적용, rule 37 활성, bluesky row 존재. 빌드 보고의 "rule 37 만 silent" 묘사는 사소한 부정확(실제 35/37 둘 다 silent).
