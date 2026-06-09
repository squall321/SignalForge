"""알림 디스패처 — Slack / Discord incoming webhook.

규칙:
- ALERT_WEBHOOK_URL 미설정 → reports/alerts.log 에만 append
- ALERT_PROVIDER=slack (default) | discord
- 동기 httpx 호출 (Celery task 안에서 부담 없이 사용)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# /home/koopark/claude/SignalForge/reports/
REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"
ALERTS_LOG = REPORTS_DIR / "alerts.log"

_LEVEL_COLORS_SLACK = {
    "info":     "#36a64f",  # green
    "warning":  "#f5a623",  # orange
    "critical": "#d0021b",  # red
}
_LEVEL_COLORS_DISCORD = {
    "info":     0x36A64F,
    "warning":  0xF5A623,
    "critical": 0xD0021B,
}


def _format_slack(payload: dict, level: str) -> dict:
    title = payload.get("title", "SignalForge Alert")
    text = payload.get("text", "")
    fields = payload.get("fields", {}) or {}
    attachment = {
        "color": _LEVEL_COLORS_SLACK.get(level, "#cccccc"),
        "title": title,
        "text": text,
        "fields": [
            {"title": k, "value": str(v), "short": True} for k, v in fields.items()
        ],
        "ts": int(datetime.now(timezone.utc).timestamp()),
    }
    return {"attachments": [attachment]}


def _format_discord(payload: dict, level: str) -> dict:
    title = payload.get("title", "SignalForge Alert")
    text = payload.get("text", "")
    fields = payload.get("fields", {}) or {}
    embed = {
        "title": title,
        "description": text,
        "color": _LEVEL_COLORS_DISCORD.get(level, 0xCCCCCC),
        "fields": [
            {"name": k, "value": str(v), "inline": True} for k, v in fields.items()
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return {"embeds": [embed]}


def _log_to_file(payload: dict, level: str) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "payload": payload,
    }
    with ALERTS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def send_alert(payload: dict[str, Any], level: str = "info") -> dict:
    """알림 전송.

    Args:
        payload: {"title": str, "text": str, "fields": {k: v, ...}, "rule": str}
        level:   info | warning | critical

    Returns:
        {"status": "sent"|"logged"|"failed", "provider": ..., "level": ...}
    """
    if level not in ("info", "warning", "critical"):
        level = "info"

    # 항상 파일 로그는 남긴다 (감사 / 디버깅 용)
    _log_to_file(payload, level)

    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        logger.info("ALERT_WEBHOOK_URL 미설정 — reports/alerts.log 에만 기록")
        return {"status": "logged", "provider": "file", "level": level}

    provider = os.getenv("ALERT_PROVIDER", "slack").strip().lower() or "slack"
    body = _format_discord(payload, level) if provider == "discord" else _format_slack(payload, level)

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=body)
            if 200 <= resp.status_code < 300:
                return {"status": "sent", "provider": provider, "level": level}
            logger.warning("Webhook 응답 비정상: %s %s", resp.status_code, resp.text[:200])
            return {
                "status": "failed",
                "provider": provider,
                "level": level,
                "http_status": resp.status_code,
            }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Webhook 전송 실패: %s", exc)
        return {"status": "failed", "provider": provider, "level": level, "error": str(exc)}
