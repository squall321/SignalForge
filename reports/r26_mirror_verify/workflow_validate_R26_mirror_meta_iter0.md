# Workflow Validate Meta — R26_mirror (iter 0)

생성 (UTC): 2026-06-05T23:34:34+00:00  
backend: `http://localhost:8000`  
available: `{'regression': True, 'coverage': True, 'topic_eval': True, 'crisis': True}`  
threshold: ±10%  
메타 재귀 cap: 5 iteration (현 iter=0)

## 1. 메타 파서 산출 (validator 자기 보고서의 표 셀 직접 파싱)

### `docs/dashboard/R20_STABILIZE_2026-06-05.md` — claim 16건, alert 4건

| metric | line | 보고 (reported) | 실측 (live) | drift% | alert | note |
|---|---:|---:|---:|---:|---|---|
| GN7 | L76 | 387 | 529 | +26.84% | **ALERT** |  |
| GZF1 | L76 | 281 | 381 | +26.25% | **ALERT** |  |
| voc_total | L75 | 150000 | 121230 | -19.18% | **ALERT** |  |
| voc_total | L134 | 113 | 121230 | +99.91% | **ALERT** |  |
| GB3 | L76 | 210 | 215 | +2.33% |  |  |
| GS22 | L76 | 218 | 230 | +5.22% |  |  |
| GS25 | L76 | 847 | 872 | +2.87% |  |  |
| hn_total | L76 | 34253 | 34670 | +1.20% |  |  |
| linked | L50 | 19392 | 20098 | +3.51% |  |  |
| products_count | L76 | 389 | 389 | +0.00% |  |  |
| sentiment_pct | L137 | 100 | 88.62 | -11.38% |  | approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| sentiment_pct | L145 | 100 | 88.62 | -11.38% |  | approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L138 | 56 | 88.62 | +36.81% |  | approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L145 | 87.97 | 88.62 | +0.73% |  | approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topics_filled | L76 | 104184 | 104601 | +0.40% |  |  |
| voc_total | L76 | 117958 | 121230 | +2.70% |  |  |

### `docs/dashboard/R21_DEEPEN_2026-06-05.md` — claim 7건, alert 0건

| metric | line | 보고 (reported) | 실측 (live) | drift% | alert | note |
|---|---:|---:|---:|---:|---|---|
| linked | L87 | 19439 | 20098 | +3.28% |  |  |
| sentiment_pct | L18 | 100 | 88.62 | -11.38% |  | approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| sentiment_pct | L116 | 100 | 88.62 | -11.38% |  | approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L18 | 87.89 | 88.62 | +0.82% |  | approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L109 | 87.89 | 88.62 | +0.82% |  | approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L116 | 87.89 | 88.62 | +0.82% |  | approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| voc_total | L86 | 118517 | 121230 | +2.24% |  |  |

### `docs/dashboard/R22_RELIABILITY_2026-06-05.md` — claim 3건, alert 1건

| metric | line | 보고 (reported) | 실측 (live) | drift% | alert | note |
|---|---:|---:|---:|---:|---|---|
| voc_total | L38 | 150000 | 121230 | -19.18% | **ALERT** |  |
| topic_pct | L74 | 86.69 | 88.62 | +2.18% |  | approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| voc_total | L103 | 118541 | 121230 | +2.22% |  |  |

### `docs/dashboard/R23_EXECUTE_2026-06-05.md` — claim 6건, alert 0건

| metric | line | 보고 (reported) | 실측 (live) | drift% | alert | note |
|---|---:|---:|---:|---:|---|---|
| f1_overall | L13 | 0.65 | 0.65 | +0.00% |  |  |
| linked | L68 | 19725 | 20098 | +1.86% |  |  |
| linked | L107 | 19721 | 20098 | +1.88% |  |  |
| products_count | L111 | 389 | 389 | +0.00% |  |  |
| topics_filled | L110 | 104184 | 104601 | +0.40% |  |  |
| voc_total | L106 | 120206 | 121230 | +0.84% |  |  |

### `docs/dashboard/R24_EXTEND_2026-06-05.md` — claim 6건, alert 0건

| metric | line | 보고 (reported) | 실측 (live) | drift% | alert | note |
|---|---:|---:|---:|---:|---|---|
| crisis_delta_geon | L40 | 0 | missing | — |  | context=crisis_delta; no live crisis baseline (cross-check skipped); no live measurement |
| linked | L32 | 20084 | 20098 | +0.07% |  |  |
| linked | L74 | 20084 | 20098 | +0.07% |  |  |
| products_count | L32 | 389 | 389 | +0.00% |  |  |
| products_count | L76 | 389 | 389 | +0.00% |  |  |
| topics_filled | L75 | 104601 | 104601 | +0.00% |  |  |

