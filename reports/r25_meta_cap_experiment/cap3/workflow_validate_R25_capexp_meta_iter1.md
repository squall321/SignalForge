# Workflow Validate Meta — R25_capexp (iter 1)

생성 (UTC): 2026-06-05T23:13:18+00:00  
backend: `http://localhost:8000`  
available: `{'regression': True, 'coverage': True, 'topic_eval': True, 'crisis': False}`  
threshold: ±10%  
메타 재귀 cap: 3 iteration (현 iter=1)

## 1. 메타 파서 산출 (validator 자기 보고서의 표 셀 직접 파싱)

### `reports/r25_meta_cap_experiment/cap3/workflow_validate_R25_capexp_meta_iter0.md` — claim 37건, alert 5건

| metric | line | 보고 (reported) | 실측 (live) | drift% | alert | note |
|---|---:|---:|---:|---:|---|---|
| GN7 | L15 | 387 | 529 | +26.84% | **ALERT** | time-shift drift=+0.00% |
| GZF1 | L16 | 281 | 381 | +26.25% | **ALERT** | time-shift drift=+0.00% |
| voc_total | L17 | 150000 | 120928 | -19.38% | **ALERT** | time-shift drift=+0.00% |
| voc_total | L18 | 113 | 120928 | +99.91% | **ALERT** | time-shift drift=+0.00% |
| voc_total | L48 | 150000 | 120928 | -19.38% | **ALERT** | time-shift drift=+0.00% |
| GB3 | L19 | 210 | 215 | +2.33% |  | time-shift drift=+0.00% |
| GS22 | L20 | 218 | 230 | +5.22% |  | time-shift drift=+0.00% |
| GS25 | L21 | 847 | 872 | +2.87% |  | time-shift drift=+0.00% |
| f1_overall | L56 | 0.65 | 0.65 | +0.00% |  | time-shift drift=+0.00% |
| hn_total | L22 | 34253 | 34670 | +1.20% |  | time-shift drift=+0.00% |
| linked | L23 | 19392 | 20098 | +3.51% |  | time-shift drift=+0.00% |
| linked | L36 | 19439 | 20098 | +3.28% |  | time-shift drift=+0.00% |
| linked | L57 | 19725 | 20098 | +1.86% |  | time-shift drift=+0.00% |
| linked | L58 | 19721 | 20098 | +1.88% |  | time-shift drift=+0.00% |
| linked | L68 | 20084 | 20098 | +0.07% |  | time-shift drift=+0.00% |
| linked | L69 | 20084 | 20098 | +0.07% |  | time-shift drift=+0.00% |
| products_count | L24 | 389 | 389 | +0.00% |  | time-shift drift=+0.00% |
| products_count | L59 | 389 | 389 | +0.00% |  | time-shift drift=+0.00% |
| products_count | L70 | 389 | 389 | +0.00% |  | time-shift drift=+0.00% |
| products_count | L71 | 389 | 389 | +0.00% |  | time-shift drift=+0.00% |
| sentiment_pct | L25 | 100 | 88.59 | -11.41% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| sentiment_pct | L26 | 100 | 88.59 | -11.41% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| sentiment_pct | L37 | 100 | 88.59 | -11.41% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| sentiment_pct | L38 | 100 | 88.59 | -11.41% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L27 | 56 | 88.59 | +36.79% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L28 | 87.97 | 88.59 | +0.70% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L39 | 87.89 | 88.59 | +0.79% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L40 | 87.89 | 88.59 | +0.79% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L41 | 87.89 | 88.59 | +0.79% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L49 | 86.69 | 88.59 | +2.14% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topics_filled | L29 | 104184 | 104601 | +0.40% |  | time-shift drift=+0.00% |
| topics_filled | L60 | 104184 | 104601 | +0.40% |  | time-shift drift=+0.00% |
| topics_filled | L72 | 104601 | 104601 | +0.00% |  | time-shift drift=+0.00% |
| voc_total | L30 | 117958 | 120928 | +2.46% |  | time-shift drift=+0.00% |
| voc_total | L42 | 118517 | 120928 | +1.99% |  | time-shift drift=+0.00% |
| voc_total | L50 | 118541 | 120928 | +1.97% |  | time-shift drift=+0.00% |
| voc_total | L61 | 120206 | 120928 | +0.60% |  | time-shift drift=+0.00% |

