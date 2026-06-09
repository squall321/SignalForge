# Workflow Validate Meta — R23 메타-루프 결과

생성: 2026-06-05 (R24 트랙 B — validator 메타-루프 본런)  
대상: 1차 = `docs/dashboard/R23_EXECUTE_2026-06-05.md`,  2차+ = 직전 iter 메타 보고서  
실행: `python -m insight.workflow_validator --rounds R23 --meta --meta-max-iter 3 --backend http://localhost:8000`  
실측 backend: `regression-baseline / coverage-status / topic_eval` (available 3/3)

---

## 1. 트랙 B 본질 — validator blind spot

R22 § 2 자동 식별률 = **0/5**. 원인은 `parse_report` 정규식이 *키워드 직후 ≤ 8자* 거리만 인정하기 때문 — validator 자기 산출 보고서의 표 형식은

```
| voc_total | L38 | 150,000 | 120,423 | -19.72% | ALERT |
            ^^^                                            ← cell 사이 거리 > 8자
```

같은 라인 안에 키워드와 숫자 사이에 `| L38 |` 셀이 끼어 *invisible*. R23 트랙 B 가 *재귀 가능* 한 단위 테스트까지 마련했음에도, 정작 자기 보고서 안의 수치 자체는 *0건* 추출이었다 (R22 자기 적용 결과 자기 모순).

R24 트랙 B 는 이 blind spot 을 신규 `parse_report_meta` (표 셀 단위 파싱) + `validate_meta` (재귀 cap 3) 로 해소한다.

---

## 2. 메타-루프 실행 결과 (cap 3 도달)

| iter | kind | input | claims | alerts | mean \|Δ\|% | output |
|---:|---|---|---:|---:|---:|---|
| 0 | primary (parse_report) | `docs/dashboard/R23_EXECUTE_2026-06-05.md` | 6 | 0 | 0.74 | `reports/workflow_validate_R23_meta_iter0.md` |
| 1 | meta (parse_report_meta) | `reports/workflow_validate_R23_meta_iter0.md` | 6 | 0 | 0.74 | `reports/workflow_validate_R23_meta_iter1.md` |
| 2 | meta (parse_report_meta) | `reports/workflow_validate_R23_meta_iter1.md` | 6 | 0 | 0.74 | `reports/workflow_validate_R23_meta_iter2.md` |

- `cap_reached = True` — iter 0/1/2 모두 정상 실행 후 cap 3 에서 종료
- `self_drift_pct = 0.74%` (마지막 iter mean |Δ|)
- iter 1 ⇄ iter 2 동치 — *고정점 (fixed point)* 도달, 재귀가 발산하지 않음
- alerts = 0 — R23 본 보고서의 6개 metric claim 모두 ±10% 임계 이내

---

## 3. iter 0 (primary parser, R23 본 보고서) — 6 claim

| metric | line | 보고 | 실측 | drift% | 비고 |
|---|---:|---:|---:|---:|---|
| f1_overall | L13 | 0.65 | 0.65 | +0.00% | topic_eval_2026-06-05_r23.json |
| linked | L68 | 19,725 | 20,084 | +1.79% | R23 D 본런 직후 보고 → R24 시점 +359 자연 증가 |
| linked | L107 | 19,721 | 20,084 | +1.81% | § 8 표 동치 |
| products_count | L111 | 389 | 389 | +0.00% | regression-baseline |
| topics_filled | L110 | 104,184 | 104,601 | +0.40% | D 후행 backfill +417 |
| voc_total | L106 | 120,206 | 120,728 | +0.43% | R23 종료 → 본 검증 사이 +522 추가 적재 |

→ 모든 drift |Δ| < 2% — R23 보고가 *시점 정합성* 측면에서 매우 깨끗.

---

## 4. iter 1/2 (메타 parser, iter 0 의 메타 보고서 자체) — 동치 + time-shift

iter 1 은 iter 0 이 생성한 메타 보고서를 *그 자체로 입력* 받아 `parse_report_meta` 로 재파싱. 6개 metric claim 이 동일하게 재구성되고, *time-shift drift = +0.00%* — validator 가 보고서에 기록한 actual (실측) 과 현 시점 live 가 동치. iter 2 도 iter 1 결과를 다시 입력받아 동치 결과 → *고정점 수렴* 확인. 재귀가 발산하지 않음.

| iter | n claims 동치성 | metric 집합 동치성 | drift_pct 동치성 | time-shift |
|---:|---|---|---|---:|
| 0 → 1 | 6 = 6 | OK | OK | n/a (iter 0 은 time-shift 미보고) |
| 1 → 2 | 6 = 6 | OK | OK | +0.00% (고정점) |

---

## 5. 추가: 메타 파서로 *R22 보고서* 재검사 — blind spot 해소 정량

R22 보고서 (`reports/workflow_validate_R22.md`) 에 메타 파서를 적용한 결과 (실측: `python -m insight.workflow_validator --report ../reports/workflow_validate_R22.md --meta --meta-output-dir /tmp/meta_r22`):

| 라인 | metric | 보고 | 실측 (R24 live) | drift% | alert | time-shift |
|---:|---|---:|---:|---:|---|---:|
| L16 | voc_total | 150,000 | 120,728 | -19.51% | **ALERT** | +0.25% |
| L17 | topic_pct | 86.69 | 88.57 | +2.12% | (suppressed) | +0.03% |
| L18 | voc_total | 118,541 | 120,728 | +1.81% | — | +0.25% |

→ primary parser **0건** 이었던 R22 자기 보고서가 메타 파서로는 **3건** 추출됨. L16 alert 는 R22 § 1 에서 이미 "R20 인용 잔재 false positive" 로 식별된 그 항목 — *재현 가능* 함이 확인. time-shift +0.25% = R22 작성 시 voc_total=120,423 → 현 시점 120,728 = +305 voc 자연 증가.

---

## 6. 메타 drift 종합표

| 측면 | R22 primary | R22 meta | R23 primary | R23 meta iter1 | R23 meta iter2 |
|---|---:|---:|---:|---:|---:|
| 추출 claim | 3 | 3 | 6 | 6 | 6 |
| alerts | 1 (false+) | 1 (false+) | 0 | 0 | 0 |
| mean \|Δ\|% | 7.79 | 7.81 | 0.74 | 0.74 | 0.74 |
| 자기 식별 가능? | △ (자기 보고서 invisible) | **OK** | OK | OK | OK |
| 고정점 수렴? | n/a | n/a | n/a | **OK** | **OK** |

---

## 7. 재귀 cap 3 동작 검증

- 의도 cap = `max_iter=3` (CLI `--meta-max-iter 3`).
- 실측: iter 0 → 1 → 2 모두 실행 후 cap 도달 (`cap_reached=True`).
- *연쇄 자연 종료* 도 가능 — 직전 iter 가 0 claim 생성 시 다음 iter 입력이 비어 종료. R22 보고서를 seed 로 했을 때 iter 0 (primary parser) 가 0 claim → cap 미도달 자연 종료. R23 보고서 seed 시는 iter 0 = 6 claim → 모든 iter 실행 후 cap.

---

## 8. R23 권고 B 응답

R23 권고 2 (트랙 B 메타-루프) 요구:

| 요구 | 응답 |
|---|---|
| validator 가 자기 산출 보고서 입력 | `parse_report_meta` 추가, `validate_meta` 가 iter 1+ 부터 자기 메타 보고서 입력 |
| 재귀 cap 3 iteration | `--meta-max-iter 3` (기본값). `cap_reached=True` 로 명시. |
| 메타-수준 drift 측정 | `mean_abs_drift_pct` (iteration 단위) + `time-shift drift` (claim 단위, validator 의 자기 측정 시점 ↔ 현 시점) |
| 결과 `reports/workflow_validate_R23_meta.md` | 본 보고서 + `_iter0/1/2.md` 3종 |
| 단위 테스트 `test_validator_meta.py` 1 케이스 | PASS (`pytest tests/test_validator_meta.py -v` → 1 passed) |

---

## 9. 산출물

- 본 보고서: `reports/workflow_validate_R23_meta.md`
- iter 별 상세: `reports/workflow_validate_R23_meta_iter0.md` · `_iter1.md` · `_iter2.md`
- 신규 코드: `crawler/insight/workflow_validator.py` (`parse_report_meta`, `validate_meta`, `_parse_md_table_row`, `_normalize_metric_name`, `_build_meta_report`, CLI `--meta`/`--meta-max-iter`/`--meta-output-dir`)
- 단위 테스트: `crawler/tests/test_validator_meta.py` (1 케이스, PASS)
- 회귀 합: 기존 `test_validator_self.py` + `test_workflow_validator.py` 3 케이스 PASS (총 4/4)
- CLI 재현: `python -m insight.workflow_validator --rounds R23 --meta --meta-max-iter 3 --backend http://localhost:8000 [--json]`

---

## 10. 한계 + 후속

- 메타 파서는 *알려진 metric 풀* (`_META_KNOWN_METRICS`, 현 17 종) 안의 표 셀만 본다. 새 metric 추가 시 풀 갱신 필요.
- semantic drift (예: "alert 2건 vs 실측 3건" — R22 자체 5 drift 중 4 건) 은 여전히 *수치 비교 범위 밖* — semantic 해석은 별도 LLM 라우터 또는 정의 사전 필요. R23 결론 ("validator 범위는 수치형 직접 비교") 변동 없음.
- time-shift drift 가 0% 인 것은 *iter 1 의 입력 = iter 0 의 출력* 이라 *같은 timestamp* 에 생성된 결과 — 실측 시간차 검증을 위해서는 days 단위 cron 으로 메타 보고서를 일정 시점에 고정 후 후일 비교가 필요. R24+ 운영 후보.
