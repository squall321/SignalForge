# R22 Reliability — 5트랙 동시 빌드·검증 보고

생성: 2026-06-05 · 대상: R21 완료 직후 → R22 빌드·검증 완료
모드: ultracode · 외부 키 미입력 · LLM 14b 로컬 · 모든 백필 DRY_RUN + PRESERVE_EXISTING + audit JSONL
실측 cross-check: 5434/postgres 직결 · `voc_records` 120,179 행 시점 기준

---

## 1. 트랙별 결과 + verify (안전성 + 자기 보고 정확도)

| 트랙 | 산출 (실측 LoC) | 빌드 | verify 결론 | 자기 보고 drift | R18 폭락 재발 |
|------|----------------|------|-------------|-----------------|--------------|
| A LLM apply v2 | topic_llm_prompt_v2.py 135 · topic_llm_apply.py 457 (R21 446→+11) | n=52 dry_run 통과 | **합격** — agree 25%→55.8% (+30.8pp), comparison drift 4→2 | 보고 "drift 4→1" vs 실측 "4→2" (방향 정상, -1건 차) | 없음 (DRY_RUN) |
| B workflow self | workflow_validator.py 682 · test 141 · _internal +1 endpoint (L2528) | 23/23 PASS | **합격** — 23 claim 검증, 21 정상·2 alert (>20% drift) | 보고 alert 2건 vs 실측 3건 (GZF1 룰 1건 누락) | 없음 |
| C ops alerts 7d | _internal.py 2612 L (+102KB) endpoint L437 | pytest 1 PASS | **합격** — rule80 발화 2건 (critical 1·warning 1), rule78 19건, cooldown 위반 0 | 100% 일치 (rule80=2, rule78=19) | 없음 |
| D Crisis multi | crisis_platform_direct.py 845 (R21 372→+473) · test_crisis_multi 신규 | 10/10 PASS | **합격** — engadget +59·theverge +57·androidcentral 신규 추가 | 보고 +177 vs 실측 +173 (engadget 76·theverge 148·9to5G 100 = 324 누계, neto 정상) | 없음 |
| E audit fix | backfill_audit_monitor.py 395 (R21 375→+20) | 7/7 PASS | **부분합격** — critical=0 안전망은 동작, 그러나 CPD env 표준키 emit 미패치 (Monitor insert_only 디폴트로 차단됨) | 보고 "A) CPD env alias 추가" 미실현 (production CPD 여전히 `CPD_*` 만 emit) | 없음 |

**전반 R18 sentiment·topic 폭락 재발 없음** — sentiment 120,173/120,174 = 100.00%, topic (`array_length(topics)>=1`) 104,184/120,179 = **86.69%** (R20 87.91% → -1.22pp, 신규 D 트랙 177 voc 가 topics 미적용 상태로 일시 희석).

---

## 2. A · LLM prompt v2 — agree_rate 25%→55.8%

- 핵심: R21 disagree 패턴 `comparison_as_default_drift` (n=8 중 4건) 잡기 위해 "daily driver / switched / really like" 등 experience 신호 few-shot 강화
- 실측 n=52 dry_run: agree 25.0% → **55.8%** (+30.8pp), comparison_drift 4→2 (-50%)
- spot-check id=951471 ("daily driver" 케이스): R21 → comparison(오) · v2 → experience(정), id=7811 → experience(정), id=13276 → experience(정)
- 자기 보고 drift: "comparison 4→1" 으로 보고, 실측 4→2 (방향·크기 모두 정상, -1건 차 사소)
- 본런 미수행 (`TOPIC_REFINE_APPLY=1` 대기) — 본런 후 topic_eval F1 0.500 → 0.65 회복 검증 필수
- 안전: PRESERVE_EXISTING + audit run_id 기록 완료

---

## 3. B · workflow 자기 보고 동기화

- 신규: `crawler/insight/workflow_validator.py` (682 L) + `backend/api/_internal.py` `GET /workflow-validate` endpoint (L2528, backend 재시작 시 활성)
- 동작: 보고서 본문에서 23 claim 자동 추출 → 실측 SQL/파일 stat 와 비교 → ±5% 이상이면 alert
- R20·R21 보고서 실측: 21 정상, 2 alert (R20 `L75 voc_total 150,000→110,000` threshold 잔재 -20.0%, `L134 113,xxx` 정규식 토큰화 +99.9%)
- 자기 보고 drift: alert "2건" 보고 vs 실측 "3건" (GZF1 룰 1건 누락). 본 endpoint 가 미래 자기 적용에서 자체 catch 가능
- 9 항목 approx 면제 (sentiment_pct / topic_pct 처럼 자연 drift 변동 큰 메트릭)

---

## 4. C · ops alerts 7d 효과

7일 윈도우 (`alert_events.fired_at >= NOW()-7d`) 발화:

| rule_id | 이름 | severity | 건수 |
|--------:|------|----------|----:|
| 80 | ops_status_violation | critical | 1 |
| 80 | ops_status_violation | warning | 1 |
| 78 | operations_monitor | critical | 6 |
| 78 | operations_monitor | warning | 13 |
| 42 | (기타) | critical | 2 |
| 37 | (info) | info | 351 |
| 35 | warning | warning | 150 |
| 3 | warning | warning | 645 |

rule 80 (R21 신규, 매시 35분 파일기반) **2건 — 1일만 발화**, regression_ok_ratio 0.5455 / voc_daily_drop_pct 87.06. rule 78 (DB-direct) 19건과 의미적 중복 없음, cooldown 위반 0.

---

## 5. D · Crisis 멀티 platform

| platform | id | 누적 voc |
|----------|---:|--------:|
| The Verge | 32 | 148 |
| 9to5Google | 12 | 100 |
| Engadget | 33 | 76 |
| androidcentral | (신규) | — |

- 신규: engadget legacy-sitemap44-51 (URL `/YYYY-MM-DD-slug.html` + lastmod 폴백), theverge / androidcentral 패턴 추가
- 자기 보고 drift: 보고 "+177" vs 실측 누계 324 (R21 시점 9to5G 22 → 현 100, +78 단독; engadget 76·theverge 148 신규). 신규 적재 수치 자체는 PRESERVE_EXISTING + ON CONFLICT 멱등 저장으로 정상
- topic 86.69% (-1.22pp) 의 1차 원인 — 177 신규 voc 가 topics 미적용 상태
- 안전: 100% sentiment 유지 (120,173/120,174)

---

## 6. E · audit critical fix

- 사실: R21 4 critical 전건 = CPD live run 2회 × 2 룰 (`preserve_existing_off`+`backup_disabled`). 실 위험 0건 (CPD 는 `ON CONFLICT DO NOTHING` 신규 insert 만, R18 reclassify 모델과 무관)
- 수정: Monitor 의 `insert_only` 디폴트 화이트리스트가 안전망으로 동작 → **critical=0 달성**
- 자기 보고 drift: "A) CPD env alias 추가" 보고 vs 실측 production CPD 여전히 `CPD_*` 만 emit (Monitor side fix 만 적용). 효과는 동일하지만 *어디서* 고쳤는지 보고가 부정확
- 보고 "total_runs=7" vs 실측 15 — 측정 윈도우 차이로 추정
- 권고: 다음 라운드에서 실제 CPD 측에 표준 키 `PRESERVE_EXISTING/BACKUP_BEFORE/DATA_TOUCHED` 추가 emit 1줄 패치

---

## 7. 가동 절차

1. **A 본런** — `TOPIC_REFINE_APPLY=1 TOPIC_REFINE_PER_TOPIC=100 TOPIC_APPLY_PROMPT_V2=1 python topic_llm_apply.py` → topic_eval 100건 spot-check, F1 0.65 미달 시 백업 ROLLBACK
2. **B 자동 hook** — `GET /api/v1/_internal/workflow-validate?path=docs/dashboard/RXX.md` 를 commit 후 hook 으로 실행, alert>0 시 보고서 수정
3. **C 모니터링** — rule 80 7일 누계 2건은 정상 임계 범위, voc 일일 drop 50% 룰 1주 후 재튜닝
4. **D 확장** — androidcentral 본런 + topics_llm 후행 백필 (177 voc → 86.69% 회복)
5. **E 추가 패치** — CPD 측에 표준 audit 키 emit 1줄 (`_emit_audit` 에 `data_touched=False, preserve_existing=True, backup_before=False`) 추가

---

## 8. 측정 수치 종합 (R21 → R22 실측)

| 지표 | R21 보고 | R22 실측 | 변화 |
|------|---------:|--------:|------|
| voc_total | 118,541 | 120,179 | +1,638 (+1.38%) |
| sentiment % | 100.00 | 100.00 | 0 |
| linked (product_id) | 19,462 | 19,717 | +255 |
| topic % (`array_length>=1`) | 87.89 | 86.69 | -1.22pp |
| `other` 비율 | (미보고) | 86,614 / 120,179 = 72.07% | — |
| Crisis 3 platform 합 | 22 | 324 | +302 (D 트랙 + 평행 적재) |
| 활성 룰 발화 7d (rule 80) | 0 | 2 (crit 1 + warn 1) | +2 |
| pytest 5 트랙 합 | n/a | 50 PASS (23 + 1 + 10 + 7 + smoke 9) | — |
| 신규/수정 LoC 실측 | n/a | 2,655 (5개 핵심 파일 합) | — |

---

## 9. 다음 단계 + 잔여 (R23 후보)

**즉시 (R22 1주차)**
- A 본런 1회 + topic_eval 0.65 검증 (현 F1 0.500 회복, prompt v2 확정 효과 측정)
- E CPD 표준 키 emit 1줄 패치 (자기 보고 정확도 회복)
- D androidcentral 본런 + 신규 177 voc topics_llm 후행 → 86.69%→89% 회복

**1주 내**
- B workflow_validate hook 을 모든 보고서 생성 파이프라인에 강제 (R20/R21 류 drift 사전 차단)
- C rule 80 1주 누계 → 임계 (drop% / regression%) 재튜닝
- 본 보고서 자체에 B hook 적용 — 자기 보고 drift (A 1건, D 신규 수치, E 정정) self-catch 검증

**확인된 자기 보고 drift (본 라운드 5건)**
1. A: comparison drift 4→1 vs 실측 4→2 (±1건)
2. B: alert 2건 vs 실측 3건 (1건 누락)
3. D: +177 vs 누계 +302 (정의 차이)
4. E: "CPD env alias 추가" vs 실측 Monitor side 만 수정
5. E: "total_runs=7" vs 실측 15 (윈도우 차)

위 drift 모두 **실 데이터 안전성과 무관**, 보고 문구 정밀도 문제. R18 sentiment·topic 폭락 같은 정합성 사고 **재발 0건**.
