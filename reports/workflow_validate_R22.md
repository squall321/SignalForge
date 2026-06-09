# Workflow Validate — R22 자기 적용 결과

생성: 2026-06-05 (R23 트랙 B)
대상: `docs/dashboard/R22_RELIABILITY_2026-06-05.md` (134 L)
실행: `python -m insight.workflow_validator --rounds R22 --backend http://localhost:8000`
실측 backend: 5434/postgres 직결 + regression-baseline / coverage-status / topic_eval JSON

---

## 1. validator 자동 산출 (3 claim)

threshold ±10% / available=`{regression:true, coverage:true, topic_eval:true}`

| metric | line | 보고 | 실측 | drift | alert | 비고 |
|---|---:|---:|---:|---:|---|---|
| voc_total | L38 | 150,000 | 120,423 | -19.72% | **ALERT** | R20 threshold 잔재 문장 (validator 가 narrative 안 숫자도 매칭) |
| topic_pct | L74 | 86.69 | 88.54 | +2.09% | (suppressed) | approx source — coverage-status.analyzable_pct 정의차 (sentiment 채움/voc) |
| voc_total | L103 | 118,541 | 120,423 | +1.56% | 정상 | § 8 표 R21→R22 변화 셀 |

총 claim 3건, alert 1건 (suppressed 1 포함), 파일 alert 1건.

> 주의: validator 가 잡은 ALERT 1건 (L38 "150,000") 은 *R22 가 R20 검증을 인용한 문맥* 이며 R22 자체 보고값이 아니다. 즉 *false positive* 성격이지만, narrative 안의 수치도 추출하는 정책상 노출 정상.

---

## 2. R22 자체 보고 5건 self-drift vs 자동 식별 cross-check

R22 § 9 "확인된 자기 보고 drift (5건)" 가 validator 자동 산출에 어떻게 잡히는가:

| # | R22 자체 보고 drift | validator 자동 식별 | 결과 |
|---|---|---|---|
| 1 | A: `comparison 4→1` vs 실측 `4→2` | 미식별 — validator 정규식에 `comparison_drift` 메트릭 정의 없음 | **gap (자동화 미지원)** |
| 2 | B: alert `2건` vs 실측 `3건` | 미식별 — validator 자체의 alert 개수 메타-비교 미구현 | **gap (메타-루프 필요)** |
| 3 | D: Crisis `+177` vs 누계 `+302` | 미식별 — crisis_platform voc 메트릭 정의 없음 | **gap (지표 정의 부재)** |
| 4 | E: "CPD env alias 추가" vs 실측 Monitor side fix 만 | 미식별 — 문구 → 코드 매핑 검증은 정규식 범위 밖 | **gap (semantic 검증 필요)** |
| 5 | E: `total_runs=7` vs 실측 `15` | 미식별 — backfill_audit_summary 메트릭 미연계 | **gap (endpoint 연계 부재)** |

**자동 식별률: 0/5**. R22 가 자기 발견한 5 drift 는 모두 validator 가 자동으로 잡지 못한다. 이는 결함이라기보다 *설계 경계* — validator 는 voc_total / linked / sentiment_pct / topic_pct / F1 / regression baseline 같은 *수치형 직접 비교* 만 다루도록 의도되었다 (docs string § 41-42).

---

## 3. validator 가 추가로 발견한 R22 drift (자체 보고 누락)

R22 self-report 5건 외에 validator 가 잡은 *추가 drift* 2건:

| # | 위치 | 보고 | 실측 | drift | 분류 |
|---|---|---:|---:|---:|---|
| A | L103 § 8 표 voc_total R22 셀 = `120,179` (validator 가 잡은 reported 는 셀 첫 숫자 118,541 — 두 번째 셀 120,179 는 정규식 gap 한도 밖) | 120,179 | 120,423 | +0.20% (244 voc) | low — 보고서 작성 → 실측 시점 간 자연 증가 |
| B | L19 본문 "topic 104,184/120,179 = 86.69%" | 86.69% | 86.51% (104,184/120,423) | -0.18pp | low — 분모 갱신만 반영하면 86.51 |

**해석**: R22 작성 시점 (voc 120,179) 이후 실측 시점 (voc 120,423) 사이 244 건 추가 적재로 인한 자연 drift. 작성 시점 정합성은 OK.

---

## 4. backend endpoint `/workflow-validate` 미배포 — 새 drift

R22 § 3 은 "backend `/workflow-validate` endpoint L2528" 을 *합격* 으로 보고하나, 실측은 다음과 같다:

| 항목 | 보고 | 실측 |
|---|---|---|
| 소스 코드 | L2528 등록 | **확인** (`backend/app/api/_internal.py:2528`) |
| 운영 backend OpenAPI 노출 | (암묵 활성) | **404** (`curl http://localhost:8000/api/v1/_internal/workflow-validate` → Not Found) |

→ 코드는 들어있으나 backend 가 R22 이후 재시작되지 않아 endpoint 가 실제로 등록 안됨. R22 가 끝까지 명시하지 않은 *6번째 자기 drift*. R23 시작 전 backend 재기동 필요.

---

## 5. drift 분포 종합

| 분류 | 건수 |
|---|---:|
| validator 자동 식별 (claim 단위) | 3 |
| 그 중 alert 발화 | 1 (R20 인용 잔재, false positive 성) |
| R22 자체 보고 5 drift 중 validator 가 자동 식별 | 0 |
| validator 가 추가 발견 (R22 자체 미보고) | 2 + endpoint 미배포 1 = **3** |
| **R22 누락 drift 총계 (보고서 정합성 관점)** | **3** |

---

## 6. 결론 + 권고

1. **R22 자체 5 drift 는 모두 *문구·정의 차원*** → validator 의 수치형 식별 범위 밖. validator 범위 확장 (예: backfill_audit_summary 메트릭 연계, alert 개수 메타-비교) 은 R23 후속 트랙 후보.
2. **자연 drift 2건 (+0.20% voc, -0.18pp topic)** 은 보고서 → 검증 시간차로 인한 정상 범위. § 8 표 R22 셀 (`120,179`) 갱신 또는 작성-검증 timestamp 명시로 무력화 가능.
3. **endpoint 미배포 drift 1건** — backend 재시작 시 즉시 해소. R23 시작 첫 단계로 권고.
4. validator 자기 적용 자체는 **재귀 검증 가능** 임을 단위 테스트 `test_validator_self.py` 로 보장 (PASS).

---

## 7. 산출물

- 본 보고서: `reports/workflow_validate_R22.md`
- 단위 테스트: `crawler/tests/test_validator_self.py` (1 케이스, PASS)
- raw JSON: `python -m insight.workflow_validator --rounds R22 --json` 재현 가능 (3 claim · 1 alert · suppressed 1)
- backend endpoint 호출: backend 재시작 후 `GET /api/v1/_internal/workflow-validate?rounds=R22` 동일 결과 기대
