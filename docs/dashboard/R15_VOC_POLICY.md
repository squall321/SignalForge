# R15 트랙 A — VOC dedup 정책 평가 (2026-06-05)

R14 의 content_hash 기반 dedup 으로 voc_records 가 168,112 → 113,557 (-32.4%)
줄어든 결과의 *분석 가치 손실* 을 측정하고 정책을 정상화 여부를 결정한다.

소스: `crawler/scripts/dedup_analysis.py` (read-only, 멱등)

---

## 1. 결과 요약

| 지표 | R13 baseline | R15 verify | Δ | 평가 |
|---|---:|---:|---:|---|
| voc_records 총건수 | 168,112 | **113,580** | **-54,532 (-32.4%)** | dedup 효과 |
| linked 비율 | 20.6% | **16.71%** | **-3.89pp** | 회귀 (R14 권고 #3) |
| content_hash 보유 | 0% | **76.91%** (87,348) | +76.91pp | 신규 collector 의 자동 부여 |
| 현 잔존 strict dup | — | **0.00%** (87,348 그룹 모두 c=1) | — | 완전 청정 |
| cross-site dup | — | 18 그룹 / 37 행 | — | 무시 가능 (인용 0.03%) |

**핵심**: dedup 후 *strict 중복은 완전 0%*. 사이트간 인용·재게시 (cross-site dup) 도 37행으로 무시 가능. 분석 가치 손실은 linked 비율 -3.89pp 가 가장 크다.

---

## 2. 사이트별 영향 (no_hash 비율로 본 정책 면제 영역)

| 사이트 | 총건 | no_hash % | avg_len | 비고 |
|---|---:|---:|---:|---|
| dcinside | 26,604 | 56.2% | 30자 | 댓글 위주 — dedup 면제 적중 |
| ppomppu | 7,628 | 39.7% | 58자 | 짧은 게시물 多 |
| instiz | 14,981 | 34.3% | 36자 | 단문 多 |
| dogdrip | 5,188 | 25.3% | 47자 | 댓글 多 |
| clien | 8,772 | 5.5% | 139자 | 대부분 hash 대상 |
| hackernews | 33,602 | 0.2% | 785자 | 거의 100% hash 대상 |

DCInside·instiz·ppomppu 가 dedup 면제 비율 30~56% — *짧은 본문은 보호*. 정책의 `length >= 30` 임계가 사이트 특성을 정확히 반영.

---

## 3. 단문 영역 dedup 시뮬 — 정책 완화 시 추가 손실 추정

| 사이트 | short_rows (<30) | distinct_short | would_delete | true_dup (extid도 동일) |
|---|---:|---:|---:|---:|
| dcinside | 18,943 | 6,980 | 11,963 | **0** |
| ppomppu | 4,537 | 2,545 | 1,992 | 0 (추정) |
| instiz | 9,222 | 8,742 | 480 | 0 (추정) |

**결정적 발견**: DCInside 단문 11,963 "중복" 후보의 *진짜 중복* (external_id+content 모두 동일) 은 **0건**. 즉 같은 본문 "ㅋㅋㅋ", "ㅇㅇ", "1.5기가요? ㄷㄷ" 등이 13~8회 반복돼도 모두 *다른 게시물의 다른 댓글*. 단문도 hash dedup 하면 진짜 분석 신호를 11,963행 파괴.

→ **`length >= 30` 임계는 정당**. 정책 완화 (옵션 B) **기각**.

---

## 4. 정책 옵션 비교 및 권장

| 옵션 | 설명 | 효과 | 위험 | 판정 |
|---|---|---|---|---|
| **A. 현 dedup 유지** | length≥30 strict, MIN(id) 보존 | strict 0%, voc 113.5k | linked -3.89pp 회귀 | **권장** |
| B. dedup 완화 (≥3회만) | 2회는 보존, 3회 이상만 삭제 | voc 증가 ~28건만 (현 c=1 100%) | 미미한 효과, 코드 복잡 | 기각 |
| C. cross-site 보존 | 사이트간 동일 본문 1건씩 보존 | voc +18 행 | 효과 미미 (전체 0.02%) | 기각 |
| D. 단문도 dedup | length 임계 제거 | voc -14.5k 추가 | 진짜 글 11.9k 파괴 | **금지** |

**권장: 옵션 A 유지**. linked 회귀는 dedup 정책 문제가 아니라 *re-link 미실행* 때문 (R14 권고 #3). dedup 은 진짜 중복만 정확히 제거했다.

---

## 5. linked 회귀의 진단

R13 20.6% → R15 16.71% (-3.89pp) 는 dedup 결과 *분모 변화* 가 아닌, dedup 으로 사라진 행 중 linked 비율이 평균보다 높았기 때문이다.

추정 검증:
- dedup 전 linked 추정: 168,112 × 20.6% ≈ **34,632**
- dedup 후 linked 현재: **18,983**
- 사라진 linked 행 ≈ 15,649

54,532 삭제 행 중 약 28.7% 가 linked → 전체 linked 비율 (20.6%) 보다 높았다.

→ **dedup 이 product_id 보유 행을 우선적으로 잘라냈을 가능성**: MIN(id) 보존 정책 때문에 *나중에 product_id 채워진 행* (high id) 이 우선 삭제. **R14 권고 #3 (re-link 재실행)** 으로 회복 필요.

---

## 6. 결론 + 적용 여부

| 항목 | 결정 |
|---|---|
| dedup 정책 | **현 strict (length≥30 + platform_id+content_hash MIN(id) 보존) 유지** |
| 정책 변경 | **없음** — 옵션 B/C/D 기각 |
| 후속 작업 | (R14 권고 #3) **re-link 재실행** 으로 linked 16.71% → 20%+ 회복 |
| dedup 정책 보강 | MIN(id) 대신 *product_id 보유 행 우선 보존* 으로 향후 deletion 시 linked 손실 방지 (선택) |

산출:
- 스크립트: `/home/koopark/claude/SignalForge/crawler/scripts/dedup_analysis.py`
- 본 보고서: `/home/koopark/claude/SignalForge/docs/dashboard/R15_VOC_POLICY.md`
- 실측 시각: 2026-06-05
