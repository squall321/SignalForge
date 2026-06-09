"""SlackChannel 단위 테스트 — webhook 유/무 + 송신 실패 폴백.

backend 가 켜져 있지 않아도 실행 가능 (DB/네트워크 비의존).
httpx.AsyncClient 는 respx 가 아니라 monkeypatch 로 직접 mock — 의존성 최소화.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import pytest

from app.core.alerts.channels.slack import SlackChannel, build_slack_payload


def _make_alert(**override: Any) -> Dict[str, Any]:
    base = {
        "rule": "test_rule",
        "metric": "community.extreme_negative_count",
        "op": ">=",
        "threshold": 1,
        "value": 3,
        "severity": "critical",
        "description": "테스트 알림",
        "fired_at": "2026-06-02T00:00:00+00:00",
    }
    base.update(override)
    return base


# ──────────────────────────────────────────────────────────
# 1) SLACK_WEBHOOK_URL 없음 → dry-run, True 반환 + 로그
# ──────────────────────────────────────────────────────────
def test_slack_channel_dry_run_when_url_missing(caplog):
    ch = SlackChannel(webhook_url="")  # 빈 문자열 = dry-run
    assert ch.dry_run is True
    assert ch.enabled is False

    with caplog.at_level(logging.INFO, logger="app.core.alerts.channels.slack"):
        ok = asyncio.run(ch.send(_make_alert()))

    assert ok is True
    assert ch.last_dispatch_at is not None
    # dry-run 로그 1회
    assert any("[SLACK-DRY]" in rec.message for rec in caplog.records)


# ──────────────────────────────────────────────────────────
# 2) URL 있음 + 200 → POST body 가 block kit + attachments 포함
# ──────────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, status_code: int, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    """httpx.AsyncClient 자리에 들어가는 가짜. POST 1건 캡처."""

    captured: List[Dict[str, Any]] = []  # 클래스 변수 — 호출 통계 공유

    def __init__(self, *args: Any, status_code: int = 200, raise_exc: Optional[Exception] = None, **kwargs: Any) -> None:
        self._status = status_code
        self._raise = raise_exc

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, content: Optional[str] = None, headers: Optional[Dict[str, str]] = None, **_: Any) -> _FakeResp:
        body = json.loads(content) if content else {}
        type(self).captured.append({"url": url, "body": body, "headers": headers or {}})
        if self._raise is not None:
            raise self._raise
        return _FakeResp(self._status, "" if self._status < 300 else "err")


def test_slack_channel_real_send_uses_block_kit(monkeypatch):
    _FakeAsyncClient.captured.clear()

    def _factory(*args: Any, **kwargs: Any) -> _FakeAsyncClient:
        # SlackChannel 은 httpx.AsyncClient(timeout=5.0) 만 호출
        return _FakeAsyncClient(*args, status_code=200, **kwargs)

    monkeypatch.setattr("app.core.alerts.channels.slack.httpx.AsyncClient", _factory)

    ch = SlackChannel(webhook_url="https://hooks.slack.com/services/T/B/X", channel="#sf-alerts")
    assert ch.enabled is True
    assert ch.dry_run is False

    ok = asyncio.run(ch.send(_make_alert(severity="warning")))
    assert ok is True
    assert ch.last_dispatch_at is not None

    assert len(_FakeAsyncClient.captured) == 1
    sent = _FakeAsyncClient.captured[0]
    assert sent["url"].startswith("https://hooks.slack.com/")
    body = sent["body"]
    # fallback text 존재
    assert "text" in body and body["text"].startswith("[SignalForge]")
    # block kit + severity color attachments
    assert "attachments" in body and isinstance(body["attachments"], list)
    att = body["attachments"][0]
    assert att["color"].startswith("#")
    blocks = att["blocks"]
    types = [b["type"] for b in blocks]
    assert "header" in types and "section" in types and "context" in types
    # SLACK_CHANNEL override 가 payload 에 반영
    assert body.get("channel") == "#sf-alerts"


# ──────────────────────────────────────────────────────────
# 3) URL 있음 + 500 → True (폴백) + warning 로그
# ──────────────────────────────────────────────────────────
def test_slack_channel_real_send_failure_falls_back(monkeypatch, caplog):
    _FakeAsyncClient.captured.clear()

    def _factory(*args: Any, **kwargs: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(*args, status_code=500, **kwargs)

    monkeypatch.setattr("app.core.alerts.channels.slack.httpx.AsyncClient", _factory)

    ch = SlackChannel(webhook_url="https://hooks.slack.com/services/T/B/X")
    with caplog.at_level(logging.WARNING, logger="app.core.alerts.channels.slack"):
        ok = asyncio.run(ch.send(_make_alert()))

    assert ok is True  # 폴백 정책: 채널 실패가 룰 평가 흐름을 끊지 않음
    assert ch.last_dispatch_at is not None
    assert any("[SLACK]" in rec.message and "HTTP 500" in rec.message for rec in caplog.records)


# ──────────────────────────────────────────────────────────
# 보조: build_slack_payload 만 따로 — severity 매핑
# ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "sev,color",
    [
        ("critical", "#d72631"),
        ("warning", "#f4b400"),
        ("info", "#1f77b4"),
        ("unknown", "#1f77b4"),  # fallback = info color
    ],
)
def test_build_slack_payload_severity_color(sev: str, color: str):
    payload = build_slack_payload(_make_alert(severity=sev))
    assert payload["attachments"][0]["color"] == color
