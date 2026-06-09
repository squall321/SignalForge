"""alert_events 미전송분 → Slack Incoming Webhook 자동 송출.

R28-harvest 트랙 D
~~~~~~~~~~~~~~~~~~
기존 모듈은 두 갈래로 분리되어 있었다::

    crawler/alerts/dispatcher.py   - send_alert(payload, level) 즉시 호출 (rules 평가 직후)
    backend/app/core/alerts/...    - RuleEngine + SlackChannel (실시간 WS broadcast)

문제: ``ops_alerts`` / ``operations_monitor`` / ``collection_health`` 등 *Celery beat
가 직접 INSERT 한* alert_events 는 위 두 dispatcher 어디서도 픽업되지 않아 채널
미전송 상태로 남는다.  실측 (2026-06-06): 24h 107건 발생 / Slack 0건 전송.

해결: 이 모듈은 **alert_events 폴링 dispatcher** 다.  5분마다::

  1. ``dispatched_channels`` 에 'slack' / 'slack:dry' / 'slack:fail' 어느 라벨도 없는
     최근 24h alert_events 조회 (rule_name JOIN 포함, 최대 batch 50건).
  2. 각 row → Slack block kit payload 구성 → Incoming Webhook POST.
     - ALERT_WEBHOOK_URL 미설정 → dry-run, label='slack:dry'
     - HTTP 200 OK            → label='slack'
     - HTTP 3xx+ / 예외        → label='slack:fail'
  3. ``UPDATE alert_events SET dispatched_channels = array_append(...)`` 로 라벨 추가.

설계 결정
~~~~~~~~~
- **dry-run 우선**: 키 없으면 절대 호출하지 않음.  로그 + 라벨링만.  운영 시 키 입력
  순간 다음 5분 tick 부터 자동 활성.
- **backend SlackChannel 미공유**: crawler 는 backend 패키지를 import 하지 않는다
  (배포 의존성 분리).  payload 포맷은 backend 의 ``build_slack_payload`` 와 동일하게
  유지 — Slack UI 일관성 + 시각적 통일.
- **idempotent dispatched_channels**: array_append 전 ``NOT ('slack' = ANY(...))`` 가드.
- **HTTP 폴백 정책**: 실패해도 task 가 깨지지 않는다.  'slack:fail' 라벨이 남아 운영자
  추적 가능, 다음 tick 에서 재시도하지 않음 (라벨이 이미 있으므로).

CLI::

    python -m insight.slack_notifier            # 1회 실행
    python -m insight.slack_notifier --dry-run  # 강제 dry-run (키 무시)
    python -m insight.slack_notifier --limit 10 # 최대 10건만
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg
import httpx

# crawler/ sys.path 보장
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

logger = logging.getLogger(__name__)


DEFAULT_LIMIT = 50
DEFAULT_LOOKBACK_HOURS = 24
DEFAULT_TIMEOUT_SEC = 5.0

# Slack attachments.color (HEX) — backend SlackChannel 과 일치.
_SEVERITY_COLOR = {
    "critical": "#d72631",
    "warning": "#f4b400",
    "info": "#1f77b4",
}


def _dsn() -> str:
    """DATABASE_URL → asyncpg dsn 변환 (alembic / SQLAlchemy 의 +asyncpg 접두 제거)."""
    url = (os.getenv("DATABASE_URL") or "").strip()
    if url:
        if url.startswith("postgresql+asyncpg://"):
            url = "postgresql://" + url[len("postgresql+asyncpg://"):]
        elif url.startswith("postgres+asyncpg://"):
            url = "postgres://" + url[len("postgres+asyncpg://"):]
        return url
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5434")
    user = os.getenv("POSTGRES_USER", "signalforge")
    pwd = os.getenv("POSTGRES_PASSWORD", "signalforge_pass")
    db = os.getenv("POSTGRES_DB", "signalforge")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"


def _webhook_url() -> str:
    """ALERT_WEBHOOK_URL 우선, SLACK_WEBHOOK_URL fallback (둘 다 trim)."""
    url = (os.getenv("ALERT_WEBHOOK_URL") or "").strip()
    if url:
        return url
    return (os.getenv("SLACK_WEBHOOK_URL") or "").strip()


# ── payload 포맷 (backend SlackChannel 과 동일 구조) ──────────────────
def _format_text(row: Dict[str, Any]) -> str:
    severity = str(row.get("severity") or "info").upper()
    rule = row.get("rule_name") or "?"
    metric = (row.get("payload") or {}).get("metric") or "?"
    value = row.get("value")
    threshold = row.get("threshold")
    return f"[SignalForge][{severity}] {rule} — {metric} value={value} threshold={threshold}"


def _build_blocks(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    severity = str(row.get("severity") or "info").upper()
    rule = row.get("rule_name") or "?"
    payload = row.get("payload") or {}
    metric = payload.get("metric") or "?"
    value = row.get("value")
    threshold = row.get("threshold")
    fired_at = row.get("fired_at")
    fired_iso = (
        fired_at.isoformat() if isinstance(fired_at, datetime)
        else (str(fired_at) if fired_at is not None
              else datetime.now(timezone.utc).isoformat())
    )
    desc = payload.get("description") or payload.get("reason") or ""
    blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"[SignalForge] {rule}"}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Severity*\n{severity}"},
                {"type": "mrkdwn", "text": f"*Metric*\n`{metric}`"},
                {"type": "mrkdwn", "text": f"*Value*\n{value}"},
                {"type": "mrkdwn", "text": f"*Threshold*\n{threshold}"},
            ],
        },
    ]
    if desc:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"_{desc}_"}})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"fired_at: `{fired_iso}` (event_id={row.get('id')})"}],
    })
    return blocks


def build_slack_payload(row: Dict[str, Any], channel: str = "") -> Dict[str, Any]:
    """Slack incoming webhook payload (block kit + attachments.color).

    backend SlackChannel.build_slack_payload 와 동일 구조 — UI 일관성 유지.
    """
    severity = str(row.get("severity") or "info").lower()
    color = _SEVERITY_COLOR.get(severity, _SEVERITY_COLOR["info"])
    payload: Dict[str, Any] = {
        "text": _format_text(row),
        "attachments": [{"color": color, "blocks": _build_blocks(row)}],
    }
    if channel:
        payload["channel"] = channel
    return payload


# ── DB 조회 / 업데이트 ────────────────────────────────────────────────
_FETCH_UNSENT_SQL = """
    SELECT e.id, e.rule_id, e.fired_at, e.severity, e.value, e.threshold,
           e.payload, e.dispatched_channels, r.name AS rule_name
    FROM alert_events e
    LEFT JOIN alert_rules r ON r.id = e.rule_id
    WHERE e.fired_at >= now() - make_interval(hours => $1)
      AND NOT ('slack'      = ANY(e.dispatched_channels))
      AND NOT ('slack:dry'  = ANY(e.dispatched_channels))
      AND NOT ('slack:fail' = ANY(e.dispatched_channels))
    ORDER BY e.fired_at ASC
    LIMIT $2
"""

_MARK_SQL = """
    UPDATE alert_events
       SET dispatched_channels = array_append(dispatched_channels, $1::varchar)
     WHERE id = $2
       AND NOT ($1 = ANY(dispatched_channels))
"""


async def fetch_unsent(
    conn: asyncpg.Connection,
    *,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    limit: int = DEFAULT_LIMIT,
) -> List[Dict[str, Any]]:
    """24h 내 slack 라벨이 하나도 없는 alert_events 조회 → 행 dict 리스트."""
    rows = await conn.fetch(_FETCH_UNSENT_SQL, int(lookback_hours), int(limit))
    out: List[Dict[str, Any]] = []
    for r in rows:
        pl_raw = r["payload"]
        if isinstance(pl_raw, (bytes, bytearray)):
            try:
                pl = json.loads(pl_raw.decode("utf-8"))
            except Exception:
                pl = {}
        elif isinstance(pl_raw, str):
            try:
                pl = json.loads(pl_raw)
            except Exception:
                pl = {}
        else:
            pl = dict(pl_raw or {})
        out.append({
            "id": int(r["id"]),
            "rule_id": int(r["rule_id"]),
            "rule_name": r["rule_name"] or "?",
            "fired_at": r["fired_at"],
            "severity": r["severity"],
            "value": float(r["value"]),
            "threshold": float(r["threshold"]),
            "payload": pl,
            "dispatched_channels": list(r["dispatched_channels"] or []),
        })
    return out


async def mark_dispatched(conn: asyncpg.Connection, event_id: int, label: str) -> None:
    """라벨 추가 (idempotent — 이미 있으면 no-op)."""
    await conn.execute(_MARK_SQL, label, int(event_id))


# ── HTTP 송신 ────────────────────────────────────────────────────────
async def _post_slack(
    client: httpx.AsyncClient,
    url: str,
    payload: Dict[str, Any],
) -> int:
    """webhook POST → status_code 반환 (예외는 호출자가 처리)."""
    resp = await client.post(
        url,
        content=json.dumps(payload, ensure_ascii=False),
        headers={"Content-Type": "application/json"},
    )
    return int(resp.status_code)


# ── 실행 ─────────────────────────────────────────────────────────────
async def run(
    *,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    limit: int = DEFAULT_LIMIT,
    force_dry: bool = False,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    dsn: Optional[str] = None,
    channel_override: Optional[str] = None,
) -> Dict[str, Any]:
    """1회 폴링 → dispatch → 라벨링.

    반환 payload::

        {
          "found": 7,
          "sent": 5,
          "dry": 0,
          "failed": 2,
          "skipped": 0,
          "enabled": True,
          "dry_run": False,
        }
    """
    url = "" if force_dry else _webhook_url()
    enabled = bool(url)
    dry_run = not enabled
    channel = channel_override if channel_override is not None else (os.getenv("SLACK_CHANNEL") or "").strip()

    sent = dry = failed = skipped = 0

    conn = await asyncpg.connect(dsn or _dsn())
    try:
        rows = await fetch_unsent(conn, lookback_hours=lookback_hours, limit=limit)
        if not rows:
            return {
                "found": 0, "sent": 0, "dry": 0, "failed": 0, "skipped": 0,
                "enabled": enabled, "dry_run": dry_run,
            }

        if dry_run:
            for row in rows:
                logger.info("[SLACK-DRY] %s", _format_text(row))
                try:
                    await mark_dispatched(conn, row["id"], "slack:dry")
                    dry += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[slack-notifier] mark dry 실패 id=%s: %s", row["id"], exc)
                    skipped += 1
            return {
                "found": len(rows), "sent": sent, "dry": dry,
                "failed": failed, "skipped": skipped,
                "enabled": enabled, "dry_run": dry_run,
            }

        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            for row in rows:
                payload = build_slack_payload(row, channel)
                label = "slack:fail"
                try:
                    status_code = await _post_slack(client, url, payload)
                    if 200 <= status_code < 300:
                        label = "slack"
                        sent += 1
                    else:
                        logger.warning("[slack-notifier] HTTP %s for event_id=%s", status_code, row["id"])
                        failed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[slack-notifier] POST 실패 event_id=%s: %s", row["id"], exc)
                    failed += 1
                try:
                    await mark_dispatched(conn, row["id"], label)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[slack-notifier] mark 실패 id=%s label=%s: %s", row["id"], label, exc)
                    skipped += 1
    finally:
        await conn.close()

    return {
        "found": len(rows), "sent": sent, "dry": dry,
        "failed": failed, "skipped": skipped,
        "enabled": enabled, "dry_run": dry_run,
    }


def _parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="slack_notifier")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="최대 dispatch 건수")
    p.add_argument("--hours", type=int, default=DEFAULT_LOOKBACK_HOURS, help="조회 룩백 시간")
    p.add_argument("--dry-run", action="store_true", help="키 무시하고 강제 dry-run")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = _parse_cli()
    result = asyncio.run(run(
        lookback_hours=args.hours,
        limit=args.limit,
        force_dry=args.dry_run,
    ))
    print(
        f"[slack-notifier] found={result['found']} sent={result['sent']} "
        f"dry={result['dry']} failed={result['failed']} skipped={result['skipped']} "
        f"enabled={result['enabled']} dry_run={result['dry_run']}"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
