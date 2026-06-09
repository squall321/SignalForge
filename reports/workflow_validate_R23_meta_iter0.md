# Workflow Validate Meta — R23 (iter 0)

생성 (UTC): 2026-06-05T22:23:11+00:00  
backend: `http://localhost:8000`  
available: `{'regression': True, 'coverage': True, 'topic_eval': True}`  
threshold: ±10%  
메타 재귀 cap: 3 iteration (현 iter=0)

## 1. 메타 파서 산출 (validator 자기 보고서의 표 셀 직접 파싱)

### `docs/dashboard/R23_EXECUTE_2026-06-05.md` — claim 6건, alert 0건

| metric | line | 보고 (reported) | 실측 (live) | drift% | alert | note |
|---|---:|---:|---:|---:|---|---|
| f1_overall | L13 | 0.65 | 0.65 | +0.00% |  |  |
| linked | L68 | 19725 | 20084 | +1.79% |  |  |
| linked | L107 | 19721 | 20084 | +1.81% |  |  |
| products_count | L111 | 389 | 389 | +0.00% |  |  |
| topics_filled | L110 | 104184 | 104601 | +0.40% |  |  |
| voc_total | L106 | 120206 | 120728 | +0.43% |  |  |

