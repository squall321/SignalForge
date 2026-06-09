# SignalForge 외부 키 통합 결선 가이드 (Harvest 3 Track E)

Groq (외부 LLM tier) + Slack (실시간 알림) 두 통합 키를 **한 페이지 / 한 명령어**
로 활성·검증하는 운영자용 가이드.  키 미입력 환경에서도 시스템은 정상 동작
(graceful) — 이 문서의 동작은 키 입력 직후 즉시 활성을 보장한다.

## 한눈에 보기

```
┌──────────────────────────────────────────────────────────────────────┐
│ .env (root) 에 최대 4줄                                              │
│                                                                       │
│   # Slack (선택)                                                      │
│   ALERT_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...   │
│                                                                       │
│   # Groq (선택)                                                       │
│   EXTERNAL_API_KEY=gsk_...                                            │
│   EXTERNAL_BASE_URL=https://api.groq.com/openai/v1                    │
│   EXTERNAL_MODEL=llama-3.3-70b-versatile                              │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
       python -m scripts.key_health_check   ← 한 줄 검증
                                  │
                                  ▼
       backend /api/v1/_internal/key-status  ← cross-check
                                  │
                                  ▼
       ┌─────────────────┬────────────────────────┐
       │ Groq external   │ Slack incoming webhook │
       │ → LLM 보고서    │ → 알림 dispatch (5분) │
       └─────────────────┴────────────────────────┘
```

## 1. 키 발급 (각 1회)

### 1-1. Groq (LLM tier — 무료 권장)

1. https://console.groq.com 가입 (구글 SSO)
2. **API Keys** → **Create API Key** → 키 복사 (`gsk_...`)
3. 권장 모델: `llama-3.3-70b-versatile` (무료 daily limit, 70B, ~0.6 grounding 예상)

상세 옵션 (OpenRouter / Together / 자체호스트) 은 `LLM_EXTERNAL_GUIDE.md` 참고.

### 1-2. Slack Incoming Webhook (선택)

1. https://api.slack.com/apps → **Create New App** → From scratch
2. App 이름: `SignalForge Alerts`, 워크스페이스 선택
3. 좌측 **Incoming Webhooks** → 토글 ON → **Add New Webhook to Workspace**
4. 채널 선택 (예: `#sf-alerts`) → Webhook URL 복사

## 2. `.env` 입력 (한 번)

`/home/koopark/claude/SignalForge/.env` 한 파일 — backend / crawler 양쪽이 동시 사용.

```bash
# ── Slack (생략 가능 — dry-run 라벨만 남음) ───────────────────────────
ALERT_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
# 선택 — 기본 채널 override
SLACK_CHANNEL=
# 선택 — provider 변경 (slack 외에 discord 등). 기본 slack.
ALERT_PROVIDER=slack

# ── Groq external LLM (생략 가능 — local 14b 폴백) ────────────────────
EXTERNAL_API_KEY=gsk_...
EXTERNAL_BASE_URL=https://api.groq.com/openai/v1
EXTERNAL_MODEL=llama-3.3-70b-versatile
```

3 슬롯 중 하나라도 비어있으면 Groq 는 그대로 skipped, fast/high local 폴백.
`ALERT_WEBHOOK_URL` 가 비어있으면 Slack 은 dispatch 라벨이 `slack:dry` 로 남고
실제 POST 안 됨.

## 3. 적용 (서비스 재시작)

```bash
cd /home/koopark/claude/SignalForge
scripts/down.sh && scripts/up.sh
# 또는 backend 만 재시작 (FastAPI settings 가 시작 시 1회 캐시되므로 필수):
pkill -f "uvicorn app.main:app" && \
  nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    > /tmp/sf_backend.log 2>&1 &
```

backend FastAPI 는 시작 시 `Settings()` 를 1회 캐시 — `.env` 수정 후 재시작 필수.
crawler 의 5분 dispatcher / LLM provider 는 매 호출마다 환경변수 재읽음 → 재시작 불필요.

## 4. 자동 검증 (한 줄)

```bash
cd /home/koopark/claude/SignalForge/crawler && \
    /home/koopark/claude/SignalForge/.venv/bin/python -m scripts.key_health_check
```

출력 — 키 모두 입력 + 활성 상태:

```
[overall] ok
  groq:  ok   — tier_label=external:llama-3.3-70b-versatile (HTTP 200)
  slack: ok   — ALERT_WEBHOOK_URL → (webhook default) (https://hooks.slack.com/servic...XYZ4)
  guide: docs/dashboard/HARVEST_KEY_SETUP.md
```

출력 — 키 미입력 (graceful):

```
[overall] skipped
  groq:  skip — ['EXTERNAL_API_KEY', 'EXTERNAL_BASE_URL', 'EXTERNAL_MODEL']
  slack: skip — ALERT_WEBHOOK_URL / SLACK_WEBHOOK_URL 미입력 (graceful skip)
```

출력 — Slack 만 입력 (한쪽만 활성):

```
[overall] partial
  groq:  skip — ['EXTERNAL_API_KEY', 'EXTERNAL_BASE_URL', 'EXTERNAL_MODEL']
  slack: ok   — ALERT_WEBHOOK_URL → (webhook default) (https://hooks.slack.com/...)
```

JSON 출력 (CI/CD 통합):

```bash
python -m scripts.key_health_check --json
```

종료 코드:
- **0** : 모두 skipped (graceful) 또는 모두 ok
- **1** : partial (한쪽 fail) 또는 fail

옵션:
- `--skip-groq` : Slack 만 검증 (Groq 호출 안 함, daily limit 절약)
- `--skip-slack` : Groq 만 검증
- `--backend-url http://...` : cross-check 대상 backend URL (기본 `127.0.0.1:8000`)

## 5. Backend cross-check endpoint

```bash
curl -s http://127.0.0.1:8000/api/v1/_internal/key-status | jq .
# 외부 호출까지 확인 (Groq daily limit 1회 소모):
curl -s 'http://127.0.0.1:8000/api/v1/_internal/key-status?ping=true' | jq .
```

응답 (요약):

```json
{
  "generated_at": "2026-06-06T...",
  "ping": false,
  "groq": {
    "configured": true,
    "api_key_redacted": "gsk_...XYZ4",
    "base_url": "https://api.groq.com/openai/v1",
    "model": "llama-3.3-70b-versatile",
    "reachable": null
  },
  "slack": {
    "configured": true,
    "enabled": true,
    "dry_run": false,
    "source": "ALERT_WEBHOOK_URL",
    "url_redacted": "https://hooks.slack.com/...XYZ4"
  },
  "summary": { "groq_ok": true, "slack_ok": true, "all_ok": true }
}
```

- `localhost only` — `127.0.0.1 / ::1 / localhost` 외 요청은 403.
- 키 자체는 절대 응답에 노출되지 않음 (prefix/suffix 만).

## 6. 수동 활성 확인 (개별)

### Slack 1회 테스트 송신

```bash
curl -X POST http://127.0.0.1:8000/api/v1/alerts/channels/slack/test
# → 슬랙 채널에 [SignalForge] 테스트 메시지 1건 도착해야 정상.
```

OR crawler dispatcher 직접:

```bash
cd /home/koopark/claude/SignalForge/crawler
DATABASE_URL="postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge" \
ALERT_WEBHOOK_URL="https://hooks.slack.com/services/..." \
/home/koopark/claude/SignalForge/.venv/bin/python -m insight.slack_notifier --limit 3
```

출력: `[slack-notifier] found=3 sent=3 dry=0 failed=0 ...`

### Groq 1회 호출

```bash
cd /home/koopark/claude/SignalForge/crawler
/home/koopark/claude/SignalForge/.venv/bin/python -m scripts.groq_health_check
# → [ok] external reachable — tier_label=external:llama-3.3-70b-versatile
```

또는 backend `/api/v1/_internal/llm-status?ping=true` 로 외부 tier 단독 검증.

### DB 직접 점검 (Slack 라벨 분포)

```sql
SELECT
  count(*) FILTER (WHERE 'slack'      = ANY(dispatched_channels)) AS sent,
  count(*) FILTER (WHERE 'slack:dry'  = ANY(dispatched_channels)) AS dry,
  count(*) FILTER (WHERE 'slack:fail' = ANY(dispatched_channels)) AS failed
FROM alert_events
WHERE fired_at >= now() - interval '24 hours';
```

키 입력 후 정상: `sent` ≥ 90%, `dry`/`failed` ≈ 0.

## 7. 트러블슈팅

### `key_health_check` 가 `partial` 인데 한쪽만 입력했음
정상.  한 쪽만 활성한 운영 상태 — exit 1 은 운영 CI 가 "양쪽 활성 필수" 정책일 때
탐지 신호.  반쪽 활성이 의도라면 `--skip-groq` / `--skip-slack` 으로 의도 명시.

### `groq: fail` — HTTP 401
API Key 가 잘못됨.  Groq console 에서 키 재발급, `.env` 갱신, 재시도.

### `groq: fail` — HTTP 429
daily limit 초과 또는 분당 RPM 초과.  Groq free tier 는 30 RPM / 14400 req/day.
보고서는 1일 1회 + alert 라우팅 약간 → 일상 운영에는 충분.

### `slack: fail` — 형식 잘못
URL 이 `https://hooks.slack.com/` 으로 시작 안 함.  Slack app 의 Incoming Webhook
탭에서 URL 복사가 잘못된 케이스.

### `backend: slack mismatch — backend 재시작 필요`
backend 가 settings 캐시를 들고 있음.  `.env` 갱신 후 backend 재시작 필요
(crawler dispatcher 는 매 tick 재읽기 → 영향 없음).

### Slack 폭주 (한 번에 너무 많이 발사)
dispatcher 는 5분 / 50건 (분당 10건) 제한 — Slack rate limit 안전 범위.
키 입력 직후 적체 알림이 한꺼번에 발사되지 않도록 batch 가 보호.

### Webhook URL 노출 의심
Slack App 페이지에서 webhook 재발급.  `.env` 만 노출되면 Bearer 키 등급 위험.

## 8. 키 미입력 환경 동작 (CI / staging)

| 슬롯 | 동작 |
|---|---|
| `EXTERNAL_*` 누락 | LLM 라우터 high-shared (local qwen2.5:14b) 로 폴백, 보고서는 정상 생성 |
| `ALERT_WEBHOOK_URL` 누락 | `alert_events` INSERT 는 그대로, 5분 dispatcher 가 `slack:dry` 라벨 추가 |
| 양쪽 누락 | `key_health_check` → `skipped` (exit 0), 시스템 정상 동작 |

## 9. 관련 파일

| 파일 | 역할 |
|---|---|
| `.env` (root) | 단일 진실 — backend·crawler 공유 |
| `crawler/scripts/key_health_check.py` | 통합 검증 스크립트 (Harvest 3 신설) |
| `crawler/scripts/groq_health_check.py` | Groq 단독 검증 (선행 작업) |
| `crawler/tests/test_key_health.py` | 단위 테스트 5건 (mock httpx) |
| `backend/app/api/_internal.py::key_status` | `/api/v1/_internal/key-status` endpoint |
| `backend/app/api/_internal.py::llm_status` | `/api/v1/_internal/llm-status` (LLM tier 상세) |
| `backend/app/config.py::_fallback_slack_webhook` | `ALERT_WEBHOOK_URL` → `SLACK_WEBHOOK_URL` 자동 매핑 |
| `crawler/insight/slack_notifier.py` | 5분 dispatcher (실제 Slack POST) |
| `crawler/insight/llm_provider.py` | external/high/fast 3 티어 라우팅 |
| `docs/dashboard/LLM_EXTERNAL_GUIDE.md` | LLM tier 상세 (모델·비용 비교) |
| `docs/dashboard/SLACK_SETUP_GUIDE.md` | Slack dispatcher 상세 (라벨·재시도) |

## 변경 이력

- 2026-06-06 Harvest 3 Track E: 통합 가이드 + `key_health_check.py` + `/key-status` endpoint 신설.
