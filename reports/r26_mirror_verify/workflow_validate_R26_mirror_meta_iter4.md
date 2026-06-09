# Workflow Validate Meta — R26_mirror (iter 4)

생성 (UTC): 2026-06-05T23:34:34+00:00  
backend: `http://localhost:8000`  
available: `{'regression': True, 'coverage': True, 'topic_eval': True, 'crisis': True}`  
threshold: ±10%  
메타 재귀 cap: 5 iteration (현 iter=4)

## 1. 메타 파서 산출 (validator 자기 보고서의 표 셀 직접 파싱)

### `reports/r26_mirror_verify/workflow_validate_R26_mirror_meta_iter3.md` — claim 38건, alert 5건

| metric | line | 보고 (reported) | 실측 (live) | drift% | alert | note |
|---|---:|---:|---:|---:|---|---|
| GN7 | L15 | 387 | 529 | +26.84% | **ALERT** | time-shift drift=+0.00% |
| GZF1 | L16 | 281 | 381 | +26.25% | **ALERT** | time-shift drift=+0.00% |
| voc_total | L17 | 150000 | 121230 | -19.18% | **ALERT** | time-shift drift=+0.00% |
| voc_total | L18 | 113 | 121230 | +99.91% | **ALERT** | time-shift drift=+0.00% |
| voc_total | L19 | 150000 | 121230 | -19.18% | **ALERT** | time-shift drift=+0.00% |
| GB3 | L20 | 210 | 215 | +2.33% |  | time-shift drift=+0.00% |
| GS22 | L21 | 218 | 230 | +5.22% |  | time-shift drift=+0.00% |
| GS25 | L22 | 847 | 872 | +2.87% |  | time-shift drift=+0.00% |
| crisis_delta_geon | L23 | 0 | missing | — |  | no live crisis baseline (cross-check skipped); no live measurement |
| f1_overall | L24 | 0.65 | 0.65 | +0.00% |  | time-shift drift=+0.00% |
| hn_total | L25 | 34253 | 34670 | +1.20% |  | time-shift drift=+0.00% |
| linked | L26 | 19392 | 20098 | +3.51% |  | time-shift drift=+0.00% |
| linked | L27 | 19439 | 20098 | +3.28% |  | time-shift drift=+0.00% |
| linked | L28 | 19725 | 20098 | +1.86% |  | time-shift drift=+0.00% |
| linked | L29 | 19721 | 20098 | +1.88% |  | time-shift drift=+0.00% |
| linked | L30 | 20084 | 20098 | +0.07% |  | time-shift drift=+0.00% |
| linked | L31 | 20084 | 20098 | +0.07% |  | time-shift drift=+0.00% |
| products_count | L32 | 389 | 389 | +0.00% |  | time-shift drift=+0.00% |
| products_count | L33 | 389 | 389 | +0.00% |  | time-shift drift=+0.00% |
| products_count | L34 | 389 | 389 | +0.00% |  | time-shift drift=+0.00% |
| products_count | L35 | 389 | 389 | +0.00% |  | time-shift drift=+0.00% |
| sentiment_pct | L36 | 100 | 88.62 | -11.38% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| sentiment_pct | L37 | 100 | 88.62 | -11.38% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| sentiment_pct | L38 | 100 | 88.62 | -11.38% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| sentiment_pct | L39 | 100 | 88.62 | -11.38% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L40 | 56 | 88.62 | +36.81% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L41 | 87.97 | 88.62 | +0.73% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L42 | 87.89 | 88.62 | +0.82% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L43 | 87.89 | 88.62 | +0.82% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L44 | 87.89 | 88.62 | +0.82% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topic_pct | L45 | 86.69 | 88.62 | +2.18% |  | time-shift drift=+0.00%; approx source (coverage-status.analyzable_pct (approx)) — alert suppressed |
| topics_filled | L46 | 104184 | 104601 | +0.40% |  | time-shift drift=+0.00% |
| topics_filled | L47 | 104184 | 104601 | +0.40% |  | time-shift drift=+0.00% |
| topics_filled | L48 | 104601 | 104601 | +0.00% |  | time-shift drift=+0.00% |
| voc_total | L49 | 117958 | 121230 | +2.70% |  | time-shift drift=+0.00% |
| voc_total | L50 | 118517 | 121230 | +2.24% |  | time-shift drift=+0.00% |
| voc_total | L51 | 118541 | 121230 | +2.22% |  | time-shift drift=+0.00% |
| voc_total | L52 | 120206 | 121230 | +0.84% |  | time-shift drift=+0.00% |

