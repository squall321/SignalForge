# Slack Webhook 알림 채널 결선 가이드

R28-harvest 트랙 D 결과물. **단일 환경변수 `ALERT_WEBHOOK_URL` 한 줄** 입력으로
crawler / backend 양쪽 Slack 통합이 동시 활성된다.  미입력 상태에서도 시스템은
정상 동작 (dry-run 라벨링).

## 아키텍처 한눈

```
┌──────────────────────────────────────────────────────────────────────┐
│ Celery beat                                                          │
│  • operations-monitor-hourly  (매시 30분)  → alert_events INSERT      │
│  • ops-alerts-hourly          (매시 35분)  → alert_events INSERT      │
│  • collection-health-hourly   (매시 50분)  → alert_events INSERT      │
│  • alert-slack-dispatch-5m    (매 5분)    ← NEW: 미전송분 Slack POST  │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        alert_events.dispatched_channels 에 라벨 추가
        • 'slack'      = HTTP 200 OK
        • 'slack:dry'  = ALERT_WEBHOOK_URL 미설정 (dry-run)
        • 'slack:fail' = HTTP 3xx+ / 예외
```

## 동작 보장

- **dry-run 안전성**: 키 없으면 절대 외부 호출 안 함. 로그 + DB 라벨링만.
- **idempotent**: 한 번 라벨이 추가된 row 는 다시 픽업되지 않음 (NOT ANY 가드).
- **HTTP 실패 폴백**: `slack:fail` 라벨이 남아도 task 가 깨지지 않음. 운영자가
  webhook 점검 후 라벨 삭제하면 다음 tick 에서 재시도 가능 (`UPDATE … SET
  dispatched_channels = array_remove(dispatched_channels, 'slack:fail')`).
- **24h 룩백 / 50건 batch**: 1 tick 당 최대 50건 (5분 주기 → 시간당 600건 처리량).
  과거 적체 알림이 모두 ALERT_WEBHOOK_URL 입력 직후 한꺼번에 발사되지 않도록 제한.

## Slack 측 준비

### 1. Slack App 생성 (1회)

1. https://api.slack.com/apps → **Create New App** → **From scratch**
2. App Name: `SignalForge Alerts`, Workspace 선택
3. 좌측 메뉴 **Incoming Webhooks** → **Activate Incoming Webhooks** 토글 ON
4. 페이지 하단 **Add New Webhook to Workspace** → 채널 선택 (예: `#sf-alerts`)
5. 생성된 Webhook URL 복사 (`https://hooks.slack.com/services/T.../B.../...`)

### 2. SignalForge `.env` 입력

```bash
# /home/koopark/claude/SignalForge/.env (root)
ALERT_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
ALERT_PROVIDER=slack       # default — discord 로 바꾸려면 slack 대신 discord
SLACK_CHANNEL=             # 선택. 비우면 webhook 기본 채널. 'override' 시 #채널명
```

### 3. 적용 (서비스 재시작)

```bash
cd /home/koopark/claude/SignalForge
scripts/down.sh && scripts/up.sh
# 또는 celery 만 재시작:
kill $(cat logs/celery-worker.pid) $(cat logs/celery-beat.pid)
# 다음 cron tick 자동 재시작 (scripts/start_celery.sh 가 systemd timer 로 가동)
```

## 동작 확인

### A. Backend /alerts/channels (실시간 상태)

```bash
curl -s http://127.0.0.1:8000/api/v1/alerts/channels | jq .
```

- 키 미입력: `{"slack":{"enabled":false, "dry_run":true, ...}}`
- 키 입력 + 재시작: `{"slack":{"enabled":true, "dry_run":false, ...}}`
- `last_dispatch_at`: 최근 dispatch tick 의 ISO8601 (5분 주기로 갱신)

### B. 수동 1회 테스트 (CLI)

```bash
cd /home/koopark/claude/SignalForge/crawler
DATABASE_URL="postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge" \
ALERT_WEBHOOK_URL="https://hooks.slack.com/services/T/B/X" \
python -m insight.slack_notifier --limit 3
```

출력 예시:
```
[slack-notifier] found=3 sent=3 dry=0 failed=0 skipped=0 enabled=True dry_run=False
```

### C. dry-run 강제 (운영 키 입력된 상태에서 테스트만)

```bash
python -m insight.slack_notifier --limit 3 --dry-run
```

### D. DB 직접 점검

```sql
-- 라벨 분포 (24h)
SELECT
  count(*) FILTER (WHERE 'slack'      = ANY(dispatched_channels)) AS slack_sent,
  count(*) FILTER (WHERE 'slack:dry'  = ANY(dispatched_channels)) AS slack_dry,
  count(*) FILTER (WHERE 'slack:fail' = ANY(dispatched_channels)) AS slack_failed,
  count(*) FILTER (WHERE NOT (
    'slack'      = ANY(dispatched_channels) OR
    'slack:dry'  = ANY(dispatched_channels) OR
    'slack:fail' = ANY(dispatched_channels)
  )) AS pending
FROM alert_events
WHERE fired_at >= now() - interval '24 hours';
```

정상 운영 (키 입력 후):
- `slack_sent` ≥ 90%
- `slack:fail` ~0
- `pending` ≤ 50 (5분 안에 처리됨)

## 트러블슈팅

### "enabled=true" 인데 sent=0

- Webhook URL 유효성 점검: `curl -X POST -H 'Content-type: application/json' --data '{"text":"test"}' $ALERT_WEBHOOK_URL`
- Slack App 의 Webhook 재발급 (앱 권한 회수 시)
- `slack:fail` 라벨이 누적되면: `UPDATE alert_events SET dispatched_channels = array_remove(dispatched_channels, 'slack:fail') WHERE 'slack:fail' = ANY(dispatched_channels) AND fired_at >= now() - interval '1 hour';` → 다음 tick 재시도

### dry-run 이 안 빠짐 (키 입력 후에도 dry_run=true)

- `.env` 파일 위치 확인: root `.env` (= `/home/koopark/claude/SignalForge/.env`)
- celery 재시작 (`scripts/up.sh` 또는 worker/beat 프로세스 kill)
- `/alerts/channels` 의 `slack.dry_run` 값은 backend FastAPI 가 시작 시 settings 를 한 번
  로드하므로 backend 재시작 필요. crawler 의 5분 dispatcher 는 매 tick 환경변수 재읽음.

### 폭주 (한 번에 너무 많이 발사)

- Slack rate limit: Incoming Webhook 은 보통 분당 1건 권장. 본 dispatcher 는
  5분 / 50건 (분당 10건) → 안전 범위. 초과 시 Slack 429 → `slack:fail` 라벨.
- 임시 차단: `crawler/celery_app.py` 의 `alert-slack-dispatch-5m` 의 args `(50, 24)`
  중 첫 값 `limit` 을 10 등으로 축소.

## 보안

- Webhook URL = Bearer 키 등급. `.env` 만 노출되어도 임의 메시지 발송 가능.
- `.env` 는 git ignore 됨 (`.gitignore` 점검).
- 로그에 URL 안 찍힘: dispatcher 는 status code 만 로깅.
- 노출 의심 시 Slack App 페이지에서 webhook 재발급.

## 미입력 환경 동작

ALERT_WEBHOOK_URL 비어 있는 환경 (CI / staging):
- alert_events INSERT 는 그대로 동작 (operations_monitor / ops_alerts / collection_health).
- 5분 dispatcher 가 모든 row 에 `slack:dry` 라벨 추가 — *재발화 폭주 방지*.
- `/alerts/recent` 에서 dispatched_channels = `['slack:dry']` 로 확인 가능 — UI 가
  배지로 "dry-run" 표시 (frontend 작업 필요 시 별도 트랙).
- 로그: `[SLACK-DRY] [SignalForge][SEVERITY] rule — metric value=... threshold=...`

## 관련 파일

- `crawler/insight/slack_notifier.py` — 5분 dispatcher (이 가이드의 주역)
- `crawler/tasks.py::run_alert_slack_dispatch` — Celery task wrapper
- `crawler/celery_app.py::alert-slack-dispatch-5m` — beat 스케줄
- `backend/app/core/alerts/channels/slack.py` — backend RuleEngine 용 SlackChannel
  (alert/test 수동 트리거 시 사용. dispatcher 와 동일 webhook URL 공유)
- `backend/app/api/alerts.py::channels_status` — `/alerts/channels` endpoint
- `crawler/tests/test_slack_notifier.py` — 단위 테스트 (mock httpx + mock asyncpg)
- `backend/app/config.py::_fallback_slack_webhook` — `ALERT_WEBHOOK_URL` →
  `SLACK_WEBHOOK_URL` 자동 매핑

## 변경 이력

- 2026-06-06 R28-harvest 트랙 D: 5분 dispatcher 신설, 단일 env 통합 가이드 작성.
