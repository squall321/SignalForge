# Workflow Validate Meta — R23 (iter 2)

생성 (UTC): 2026-06-05T22:23:11+00:00  
backend: `http://localhost:8000`  
available: `{'regression': True, 'coverage': True, 'topic_eval': True}`  
threshold: ±10%  
메타 재귀 cap: 3 iteration (현 iter=2)

## 1. 메타 파서 산출 (validator 자기 보고서의 표 셀 직접 파싱)

### `reports/workflow_validate_R23_meta_iter1.md` — claim 6건, alert 0건

| metric | line | 보고 (reported) | 실측 (live) | drift% | alert | note |
|---|---:|---:|---:|---:|---|---|
| f1_overall | L15 | 0.65 | 0.65 | +0.00% |  | time-shift drift=+0.00% |
| linked | L16 | 19725 | 20084 | +1.79% |  | time-shift drift=+0.00% |
| linked | L17 | 19721 | 20084 | +1.81% |  | time-shift drift=+0.00% |
| products_count | L18 | 389 | 389 | +0.00% |  | time-shift drift=+0.00% |
| topics_filled | L19 | 104184 | 104601 | +0.40% |  | time-shift drift=+0.00% |
| voc_total | L20 | 120206 | 120728 | +0.43% |  | time-shift drift=+0.00% |

