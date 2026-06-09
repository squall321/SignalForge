"""Slack 채널 — webhook URL POST (block kit).

키가 없거나 빈 문자열이면 dry-run 모드: 실제 호출 없이 logger.info("[SLACK-DRY] ...") 출력.

block kit 구조:
- header  : 1줄 요약 ("[SEVERITY] rule_name")
- section : metric / op / value / threshold 표 (mrkdwn fields)
- context : fired_at + description
attachments.color: severity 매핑 (critical=#d72631 / warning=#f4b400 / info=#1f77b4)

last_dispatch_at: 마지막 실 전송(또는 dry-run) 발생 시각 ISO8601.
운영 모니터링 + /alerts/channels 응답에 노출.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.alerts.channels.base import AlertChannel

logger = logging.getLogger(__name__)


# Slack attachments.color (HEX) — severity 매핑.
_SEVERITY_COLOR = {
    "critical": "#d72631",
    "warning": "#f4b400",
    "info": "#1f77b4",
}


def _format_text(alert: Dict[str, Any]) -> str:
    """fallback 평문 1줄 (notification preview)."""
    severity = (alert.get("severity") or "info").upper()
    rule = alert.get("rule", "?")
    metric = alert.get("metric", "?")
    op = alert.get("op", "?")
    value = alert.get("value")
    threshold = alert.get("threshold")
    return f"[SignalForge][{severity}] {rule} — {metric} {op} {threshold} (value={value})"


def _build_blocks(alert: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Slack block kit 본문."""
    severity = (alert.get("severity") or "info").upper()
    rule = alert.get("rule", "?")
    metric = alert.get("metric", "?")
    op = alert.get("op", "?")
    value = alert.get("value")
    threshold = alert.get("threshold")
    desc = alert.get("description") or ""
    fired_at = alert.get("fired_at") or datetime.now(timezone.utc).isoformat()

    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"[SignalForge] {rule}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Severity*\n{severity}"},
                {"type": "mrkdwn", "text": f"*Metric*\n`{metric}`"},
                {"type": "mrkdwn", "text": f"*Value*\n{value}"},
                {"type": "mrkdwn", "text": f"*Threshold*\n{op} {threshold}"},
            ],
        },
    ]
    if desc:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"_{desc}_"},
            }
        )
    blocks.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"fired_at: `{fired_at}`"},
            ],
        }
    )
    return blocks


def build_slack_payload(alert: Dict[str, Any], channel: str = "") -> Dict[str, Any]:
    """Slack incoming webhook 페이로드 (block kit + attachments.color)."""
    severity = (alert.get("severity") or "info").lower()
    color = _SEVERITY_COLOR.get(severity, _SEVERITY_COLOR["info"])
    payload: Dict[str, Any] = {
        "text": _format_text(alert),  # fallback / notification preview
        "attachments": [
            {
                "color": color,
                "blocks": _build_blocks(alert),
            }
        ],
    }
    if channel:
        payload["channel"] = channel
    return payload


class SlackChannel(AlertChannel):
    name = "slack"

    def __init__(self, webhook_url: str = "", channel: str = "") -> None:
        self.webhook_url = (webhook_url or "").strip()
        self.channel = (channel or "").strip()
        self.last_dispatch_at: Optional[str] = None

    @property
    def dry_run(self) -> bool:
        return not self.webhook_url

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    async def send(self, alert: Dict[str, Any]) -> bool:
        """알림 1건 송신.

        - dry-run: 실제 호출 없이 로그 + last_dispatch_at 갱신 + True 반환
        - real: httpx POST (timeout=5s). 실패 시 warning 로그 + True 반환 (폴백)
          (운영 정책: 채널 송신 실패가 룰 평가 흐름을 끊지 않도록 함)
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        if self.dry_run:
            logger.info("[SLACK-DRY] %s", _format_text(alert))
            self.last_dispatch_at = now_iso
            return True

        payload = build_slack_payload(alert, self.channel)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    self.webhook_url,
                    content=json.dumps(payload, ensure_ascii=False),
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code >= 300:
                    logger.warning(
                        "[SLACK] HTTP %s body=%s",
                        resp.status_code,
                        (resp.text or "")[:200],
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SLACK] send 실패 (fallback dry-run): %s", exc)
        self.last_dispatch_at = now_iso
        return True


__all__ = ["SlackChannel", "build_slack_payload"]
