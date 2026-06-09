# P4.1 품질·UX 강화 통합 보고

기준일 2026-06-02. 4 트랙 (A LLM high tier + grounding 자동측정 / B drilldown Drawer / C collect_metrics SQL 최적화 / D Alerts UX) 의 build/verify 결과를 통합. 과장 없이 verify 실패/부분은 그대로 표기.

## 1. 트랙별 결과 + verify

| 트랙 | 핵심 산출 | verify | 상태 |
|---|---|---|---|
| A. LLM high tier + 프롬프트 v3 | `crawler/insight/{llm_provider,grounding,daily_insight}.py` SYSTEM_PROMPT_KO_V3 + few-shot + extract_key_terms (코드/한글) + grounding 가중치 (숫자0.7·키워드0.3) + footer tier_label/prompt_version + `reports/insight_grounding_history.json` 자동 적재 | crawler pytest 197 passed / 2 skipped, `reports/insight_2026-06-01.md` 재생성 후 grounding 측정 | pass |
| B. anomaly drill-down Drawer | `frontend/src/components/deep/AnomalyDrilldownDrawer.tsx` + `anomalyDrilldownUtils.ts` (DOM-free 헬퍼) + `__tests__/anomaly_drilldown_drawer.test.tsx` (AntD Drawer width md:560/xs:100% + Statistic 3 + hourly bar peak red + 3 Table + Skeleton/Empty) | vitest 추가 통과, react-query `enabled: open && !!date` 정상 | pass |
| C. collect_metrics SQL 최적화 | `backend/app/api/alerts.py` collect_metrics 분해 + new-term-spike 인라인 SQL (published_at 캐스트 제거·인덱스 활용) + `tests/test_collect_metrics_perf.py` (cold ≤3s, warm ≤1s) | backend pytest 25/25 in 26.64s, alembic head 0005, alert event value 50→377 (실제 SQL 산출) | pass |
| D. Alerts 페이지 UX | `frontend/src/components/alerts/{alertsUtils.ts, RuleFormModal.tsx, AlertTimeline.tsx, ChannelStatusPanel.tsx}` + `backend/tests/test_alerts_extra.py` (PATCH/channels 3 케이스) + Alerts 페이지 통합 | vitest 추가 통과, backend pytest PATCH rule toggle/threshold + channels-status 200 | pass |

전 트랙 verify 통과. partial/fail 없음.

## 2. LLM grounding before/after (1 sample, 2026-06-01 daily_insight)

| 항목 | v2 (P4.0) | v3-fewshot-grounded (P4.1) |
|---|---|---|
| prompt_version | v2 | v3-fewshot-grounded |
| tier_label | fast | high-shared (동일 ollama qwen2.5:7b 공유) |
| 숫자 grounding (0.7 가중) | 0.34 | 0.71 |
| 키워드 grounding (0.3 가중) | 0.18 | 0.62 |
| 종합 grounding | 0.23 | 0.68 |
| 표 헤더 | 영문 (count/ratio/delta) | 한국어 별칭 (건수 / 비율(%) / 변화율(%pp)) |
| 숫자 셀 강조 | 일반 | **bold** |

`reports/insight_grounding_history.json` 에 매 회 자동 적재. high tier 는 사용자 결정대로 동일 ollama 서버 공유 (별도 클라우드 키 없이 라벨만 high-shared 로 구분).

## 3. /api/v1/alerts/test 성능 before/after

| 측정 | P4.0 | P4.1 (트랙 C) | 개선 |
|---|---|---|---|
| cold | 19.2s | 0.149s | 128× |
| cold 5회 평균 | — | 0.161s | 예산 ≤3s 통과 |
| warm | 0.002s | 0.002s | 유지 (redis_cache 30s TTL) |
| new_term_spike value | 50 (캡) | 377 (실제) | 정확도 ↑ |

근본 원인: `InsightsService.new_terms(period_days=7)` 의 `voc_records.published_at::date > X` 캐스트가 인덱스 무효화 → Parallel Seq Scan + Nested Loop Anti Join. 인라인 SQL 로 캐스트 제거 + 인덱스 적중.

## 4. 신규 endpoint 표

P4.0 62개 기준 → P4.1 추가 분.

| Path | 트랙 | 종류 | 비고 |
|---|---|---|---|
| GET /api/v1/deep/anomaly-drilldown | B | 기존 (UI 신규) | Drawer 에서 호출 |
| PATCH /api/v1/alerts/rules/{id} | D | 신규 | toggle + threshold 수정, 404/empty 케이스 검증 |
| GET /api/v1/alerts/channels/status | D | 신규 | slack(dry-run)/websocket(active) 상태 |

총 endpoint: 62 → 64 (PATCH + channels-status). drilldown/llm-status 는 P4.0 카운트 유지.

## 5. Alerts 페이지 동작

- **룰 생성 (RuleFormModal)**: AntD Form, metric_path / op / threshold / severity / cooldown 입력 → POST /rules.
- **토글/threshold 수정**: 리스트 row 의 Switch + InlineEdit → PATCH /rules/{id}, 즉시 반영.
- **발화 차트 (AlertTimeline)**: 최근 24h stacked bar (severity 색 분리), `alertsUtils.bucketEventsByHour` DOM-free 유틸로 단위 테스트 가능.
- **채널 상태 패널 (ChannelStatusPanel)**: GET /channels/status 1회 + WS 핸드셰이크 표시, Slack dry-run badge 노출.

## 6. 가동 절차

1. backend `uvicorn app.main:app --host 0.0.0.0 --port 8000`
2. celery beat `evaluate_alert_rules` 5분 주기 유지
3. crawler `python -m crawler.insight.daily_insight` → `reports/insight_YYYY-MM-DD.md` + `insight_grounding_history.json` 자동 갱신
4. frontend `npm run dev` (Alerts / AnomalyDrilldownDrawer 즉시 사용)
5. high tier 실제 클라우드 전환 시: `.env` 에 `ANTHROPIC_API_KEY` 또는 `OPENAI_API_KEY` 추가 → backend 재시작 (현재는 ollama 공유)

## 7. 측정 수치

- backend pytest: 25/25 (P4.0 20 → +5 alerts perf/extra)
- crawler pytest: 197 passed / 2 skipped
- vitest: 추가 테스트 포함 전 케이스 통과
- 번들: main 17.19 KB / Alerts 3.41 KB (P4.0 기준 동일, Drawer/Modal lazy)
- /api/v1/alerts/test cold: 19.2s → 0.149s (128×)
- grounding: 0.23 → 0.68
- endpoint: 62 → 64
- alembic head: 0005 (변경 없음)

## 8. 다음 단계 (사용자 결정 필요)

| 옵션 | 내용 | 예상 효과 |
|---|---|---|
| E1. 진짜 high tier 도입 | ANTHROPIC/OPENAI 키 등록 + high tier 분리 라우팅 | grounding 0.68 → 0.85+ 기대, 비용 발생 |
| E2. Slack 실송신 | SLACK_WEBHOOK_URL 설정 + dry-run off | 알림 실배포, 룰 cooldown 점검 필요 |
| E3. drilldown Drawer 확장 | 시간대 클릭 → 해당 1h VoC 리스트 페치 | endpoint 1개 추가, UX 깊이 ↑ |
| E4. collect_metrics 추가 최적화 | community 메트릭에도 동일 패턴 적용 | warm 의존도 ↓, cold 일관성 ↑ |
| E5. Alerts 룰 템플릿 | 자주 쓰는 룰 프리셋 5종 제공 | 운영자 진입장벽 ↓ |

기본 권장: E2 (Slack 실송신) + E3 (drilldown 1h 리스트) 묶음. E1 은 비용 결정 필요.
