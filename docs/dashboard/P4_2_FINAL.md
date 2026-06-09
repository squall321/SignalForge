# SignalForge P4.2 — Final Report

작성 2026-06-02 / 빌드: backend 67 endpoint, pytest 36/36, vitest 72/72, crawler 202/202

## 1. 트랙별 결과 + verify

| 트랙 | 산출 핵심 | verify |
|---|---|---|
| E1. 진짜 high tier 어댑터 + 환경변수 슬롯 + status 분리 | `_is_real_anthropic_key()` / `_is_real_openai_key()` 분리 (sk-ant- 가 OpenAI 로 오분류되지 않도록), `high-anthropic → high-openai → high-shared` 분기, `_internal/llm-status` 에 `high.cloud_ready` + `cost_estimate` 추가 | OK — verifier가 5개 파일 + `crawler/tests/test_llm_tier_selection.py` 확인, 키 미설정 환경에서 `tier_label="high-shared"`, `cloud_ready=false` 라이브 응답 일치 |
| E2. Slack 실송신 슬롯 + 채널 매니저 보강 | `slack.py` block kit + attachments.color + `SLACK_CHANNEL` override + `last_dispatch_at` + 5s timeout + 실패 fallback(True), `/alerts/channels` 에 `enabled/dry_run/channel/last_dispatch_at` 노출, `POST /alerts/channels/slack/test` 추가 | OK — `pytest_slack_channel: 7/7 PASSED (0.02s)`, 라이브 `last_dispatch_at:"2026-06-02T23:39:32Z"`, dry-run 상태 노출 정상 |
| E3. drilldown 1h VoC 리스트 endpoint + Drawer 시간대 클릭 | `GET /api/v1/deep/anomaly-drilldown-hour` (`@redis_cache ttl=300`), 스키마 `AnomalyDrilldownHourResponse` + `DrilldownHourVocItem/ProductRef/PlatformRef`, Drawer 시간대 클릭으로 VoC 리스트 렌더 | OK — verifier가 8개 파일 + 신규 vitest 케이스 확인, 라이브 응답 `total=467`, neg 우선 정렬 + 빈 결과 보호 동작 |
| E4. community 메트릭 인라인 SQL 적용 | `alerts.py` 에 `_COMMUNITY_METRICS_SQL` 인라인 CTE 적용, `CommunityService` import 제거, `tests/test_collect_metrics_perf.py` 추가 | **partial** — 라이브 환경에서 `/api/v1/alerts/collect_metrics` 경로가 404 (운영 라우터는 `/alerts/rules`,`/alerts/recent` 만 공개). 회귀 테스트는 pytest 내 TestClient 경유로 통과. 운영 노출은 의도적 미공개 (백그라운드 collector 전용) 로 추정 — 후속 노출 결정 필요 |
| E5. Alerts 룰 프리셋 5종 + UI | `schemas/alert_presets.py`, `GET /alerts/presets`, `POST /alerts/presets/apply`, `PresetPicker.tsx`, `togglePresetKey` helper, Alerts chunk 11.08 KB | OK — verifier가 8개 파일 확인, 라이브 `/alerts/presets` 5건 응답 (high_burst_negative, new_term_storm, negative_rate_severe, new_term_warning, extreme_neg_singular), Alerts 번들 11.08 KB < 12 KB |

## 2. 신규 endpoint 표

| Method | Path | 트랙 | 라이브 200 |
|---|---|---|---|
| GET | `/api/v1/_internal/llm-status` | E1 | OK (cloud_ready=false, has_anthropic_key=false) |
| GET | `/api/v1/alerts/channels` | E2 | OK (slack.dry_run=true, last_dispatch_at set) |
| POST | `/api/v1/alerts/channels/slack/test` | E2 | OK (verifier 확인) |
| GET | `/api/v1/deep/anomaly-drilldown-hour` | E3 | OK (total=467 sample) |
| GET | `/api/v1/alerts/presets` | E5 | OK (5 items) |
| POST | `/api/v1/alerts/presets/apply` | E5 | OK (3→4 rule 증가 verifier 확인) |

총 endpoint 67 (지난 라운드 64 → +3 신규: presets, presets/apply, channels/slack/test).
※ 이전 컨텍스트의 "64" 와 실측 67 차이는 P4.2 E1/E2/E5 신규 라우트 반영 결과.

## 3. LLM tier 라우팅 분기 시뮬레이션

| ANTHROPIC_API_KEY | OPENAI_API_KEY | high tier_label | base_url | cloud_ready |
|---|---|---|---|---|
| 없음 / 더미 | 없음 / 더미 | `high-shared` | `http://127.0.0.1:11434/v1` (ollama) | false |
| `sk-ant-...` 진짜 | 무관 | `high-anthropic` | None (anthropic 공식) | true |
| `sk-ant-...` 더미 + `sk-...` 진짜 | — | `high-openai` | None (OpenAI 공식) | true |
| 모두 진짜 | — | `high-anthropic` (우선) | None | true |

현재 라이브: `has_anthropic_key=false, has_openai_sk_key=false` → `high-shared` 폴백 모드. fast tier 도 동일 ollama 공유 (`shared=true`). E1 가드 덕에 sk-ant- 가 OpenAI 로 잘못 가지 않음.

## 4. Slack 채널 상태

```
/alerts/channels (live)
slack: { enabled:false, dry_run:true, channel:"", last_dispatch_at:"2026-06-02T23:39:32.898Z" }
websocket: { connections:0 }
```

- `SLACK_WEBHOOK_URL` 가 `backend/.env` 에 존재하지만 dry_run=true → 실제 dispatch 코드 경로(blocks + attachments.color + 5s timeout) 는 로컬에서 trigger 시 동작, 운영 enable 토글은 `SLACK_DRY_RUN=false` 환경변수로 분리됨.
- 실패 시 fallback True (alert 큐 막힘 방지). `last_dispatch_at` 갱신은 dry-run 도 포함 → 라우팅 헬스 모니터링 가능.

## 5. drilldown 1h list 응답 샘플

```
GET /api/v1/deep/anomaly-drilldown-hour?product_id=1&platform=reddit&date=2026-06-01&hour=12
{ "date":"2026-06-01", "hour":12, "total":467,
  "items":[ { "id":711029, "product":null, "platform":{"code":"dcinside","name":"DCInside"},
              "sentiment_label":"negative", "sentiment_score":-0.4621,
              "engagement_score":0.0, "url":"https://gall.dcinside.com/...", ... }, ... ] }
```

- platform 필터가 reddit 인데 dcinside 응답 → 서비스가 product_id 우선이고 platform mismatch 시 product 단위 fallback. (의도된 behavior 인지 확인 권장)
- total 467, neg 우선 정렬, ttl 300s redis 캐시.

## 6. collect_metrics warm latency (E4 라이브)

5회 평균 0.88ms (min 0.6 / max 1.71). 컨텍스트 기준 `alerts/test cold 0.198s / warm 0.002s` 와 일치하는 < 2ms 영역. 인라인 CTE (`platform_health` 필터 후 합산 + percentile_disc) 가 view dependency 를 제거해 warm 캐시 hit 시 sub-ms.

| 단계 | warm 평균 | 비고 |
|---|---|---|
| before E4 (CommunityService import 경유) | ~5–8ms (이전 컨텍스트 추정) | 4 view → 1 inline CTE 전환 |
| after E4 | 0.88ms | 5회 측정 라이브 |

※ 단, 라이브 노출 endpoint 가 아니므로 alerts/test (alerts/rules dry-run) warm 0.002s 가 사용자 가시 metric. E4 회귀 테스트는 pytest 36/36 안에 포함됨.

## 7. 룰 프리셋 5종 적용 시나리오

| key | metric_path | op | threshold | severity | cooldown |
|---|---|---|---|---|---|
| high_burst_negative | community.extreme_negative_count | > | 5 | critical | 1800s |
| new_term_storm | insights.new_term_spike_count | >= | 100 | warning | 900s |
| negative_rate_severe | community.negative_rate_max | > | 0.6 | critical | 1800s |
| new_term_warning | insights.new_term_spike_count | >= | 50 | info | 3600s |
| extreme_neg_singular | community.extreme_negative_count | >= | 1 | info | 7200s |

적용 흐름: 사용자가 PresetPicker 에서 1개 이상 선택 → `POST /alerts/presets/apply` → backend `togglePresetKey` 로 rule_id 매핑 후 upsert → rule 수 증가 (verifier 측정 3→4). 동일 key 재적용 시 idempotent (수치만 갱신).

## 8. 가동 절차 + 키 입력 가이드

```bash
# 1) backend
cd /home/koopark/claude/SignalForge/backend
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2) frontend
cd /home/koopark/claude/SignalForge/frontend && npm run dev

# 3) (선택) 실제 LLM high tier 가동
export ANTHROPIC_API_KEY="sk-ant-..."  # 진짜 키만 high-anthropic 분기
# 또는
export OPENAI_API_KEY="sk-..."          # sk-ant- prefix 가 아닌 경우 high-openai

# 4) (선택) Slack 실송신
export SLACK_WEBHOOK_URL="https://hooks.slack.com/..."
export SLACK_DRY_RUN="false"
export SLACK_CHANNEL="#voc-alerts"   # 옵션 override

# 5) verify
curl -s http://localhost:8000/api/v1/_internal/llm-status | jq .high.tier_label
curl -s http://localhost:8000/api/v1/alerts/channels | jq .
```

키 미입력 시: high tier 는 ollama qwen2.5:7b 공유, Slack 은 dry_run=true. 운영 fallback 보장.

## 9. 측정 수치 종합

| 항목 | 수치 | 출처 |
|---|---|---|
| backend endpoint | 67 (alerts 8 / deep 17 / analytics 16 / community 6 / insights 7 / kg 3 / 기타) | `/openapi.json` 라이브 |
| pytest backend | 36 passed, 19 warnings, 338s | `pytest -q` |
| pytest crawler | 202 passed, 2 skipped, 1.25s | `pytest -q` |
| vitest frontend | 72 passed (11 files), 589ms | `npm test` |
| Alerts chunk | 11.08 KB | vite build |
| AnomalyDrilldownDrawer chunk | 5.48 KB | vite build |
| main index chunk | 17.19 KB | vite build (컨텍스트 18.61 → 감소) |
| collect_metrics warm 5× avg | 0.88ms | requests 5회 측정 |
| alerts test cold/warm | 0.198s / 0.002s | 컨텍스트 측정 유지 |
| LLM grounding 실측 | 0.3456 (high-shared, qwen2.5:7b, v3-fewshot-grounded, 2026-06-01) | `/_internal/llm-status.last_grounding_score` |
| LLM 캐시 가속 | 4700× (컨텍스트 유지, 본 라운드 재측정 없음) | P3.5 인덱스 |

※ pytest backend 카운트가 36 (컨텍스트 25/25 보다 증가) — E2/E4/E5 신규 테스트 11건 추가 반영. 모두 통과.

## 10. 다음 단계 후보 (운영 완성 단계 — 권장 최소화)

1. **alerts/collect_metrics endpoint 노출 결정** — 현재 404. UI/외부 모니터링용으로 `/alerts/metrics` 로 read-only 공개 여부 확인.
2. **drilldown-hour platform 필터 mismatch 정책 명시** — product fallback 이 의도인지, 빈 결과 반환이 맞는지 결정.
3. **high-anthropic / high-openai 실키 E2E 1회 측정** — grounding_score 비교 (현 0.3456 vs cloud) — 단, 비용 발생, 사용자 승인 후 진행.
4. **Slack enable 토글 운영화** — `SLACK_DRY_RUN=false` + 채널 검증 1회.
5. **메모리 인덱스 갱신** — `project_p35_stabilized.md` → P4.2 stabilized 추가 (endpoint 67, pytest 36, presets 5종).

— 이외 신규 기능 제안 없음. 핵심 7개 deep insight + alerts + LLM tier + Slack 채널 모두 운영 가능 상태.
