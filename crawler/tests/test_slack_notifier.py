"""insight.slack_notifier 단위 테스트 — mock httpx + mock asyncpg connection.

DB·네트워크 비의존. 두 경로 검증:
1. webhook URL 없음 → dry-run, 'slack:dry' 라벨 추가.
2. webhook URL 있음 + 200 OK → block kit payload POST, 'slack' 라벨 추가.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

# crawler/ 디렉토리 보장
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight import slack_notifier  # noqa: E402


def _make_row(
    *,
    event_id: int = 1276,
    severity: str = "warning",
    rule_name: str = "collection_health",
    metric: str = "collection.platforms_zero_count",
) -> Dict[str, Any]:
    return {
        "id": event_id,
        "rule_id": 81,
        "rule_name": rule_name,
        "fired_at": datetime(2026, 6, 6, 7, 50, tzinfo=timezone.utc),
        "severity": severity,
        "value": 11.0,
        "threshold": 5.0,
        "payload": {
            "metric": metric,
            "reason": "11 sites zero collection in 24h",
        },
        "dispatched_channels": [],
    }


# ── 가짜 asyncpg connection ───────────────────────────────────────────
class _FakeConn:
    """slack_notifier 가 호출하는 connect()/fetch()/execute()/close() 만 구현."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows
        self.executed: List[Dict[str, Any]] = []
        self.closed = False

    async def fetch(self, sql: str, *args: Any) -> List[Dict[str, Any]]:
        # slack_notifier.fetch_unsent 는 r["payload"] 가 jsonb -> Python dict 라고 가정.
        # 우리는 이미 row dict 를 줬으니 그대로 반환.
        return self._rows

    async def execute(self, sql: str, *args: Any) -> None:
        # mark_dispatched 호출 캡처
        self.executed.append({"sql": sql.strip().splitlines()[0], "args": list(args)})

    async def close(self) -> None:
        self.closed = True


class _FakeResp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = "ok" if status_code < 300 else "err"


class _FakeAsyncClient:
    captured: List[Dict[str, Any]] = []

    def __init__(self, *args: Any, status_code: int = 200, **kwargs: Any) -> None:
        self._status = status_code

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, content: str = "", headers: Dict[str, str] | None = None, **_: Any) -> _FakeResp:
        body = json.loads(content) if content else {}
        type(self).captured.append({"url": url, "body": body})
        return _FakeResp(self._status)


# ──────────────────────────────────────────────────────────────────────
# 1) ALERT_WEBHOOK_URL 없음 → dry-run, 라벨 'slack:dry' 추가.
# ──────────────────────────────────────────────────────────────────────
def test_slack_notifier_dry_run_marks_slack_dry(monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    fake_conn = _FakeConn([_make_row(event_id=100), _make_row(event_id=101)])

    async def _connect(_dsn: str) -> _FakeConn:
        return fake_conn

    monkeypatch.setattr(slack_notifier.asyncpg, "connect", _connect)

    result = asyncio.run(slack_notifier.run(dsn="postgresql://dummy/x"))

    assert result["enabled"] is False
    assert result["dry_run"] is True
    assert result["found"] == 2
    assert result["dry"] == 2
    assert result["sent"] == 0
    assert result["failed"] == 0
    # mark_dispatched 가 2번 호출되었고 라벨이 'slack:dry'
    labels = [op["args"][0] for op in fake_conn.executed]
    assert labels == ["slack:dry", "slack:dry"]
    # event_id 도 정상 전달
    ids = [op["args"][1] for op in fake_conn.executed]
    assert ids == [100, 101]
    assert fake_conn.closed is True


# ──────────────────────────────────────────────────────────────────────
# 2) URL 있음 + 200 → block kit POST, 라벨 'slack' 추가.
# ──────────────────────────────────────────────────────────────────────
def test_slack_notifier_real_send_marks_slack(monkeypatch):
    _FakeAsyncClient.captured.clear()
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")
    monkeypatch.delenv("SLACK_CHANNEL", raising=False)

    fake_conn = _FakeConn([_make_row(event_id=200, severity="critical")])

    async def _connect(_dsn: str) -> _FakeConn:
        return fake_conn

    def _factory(*args: Any, **kwargs: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(*args, status_code=200, **kwargs)

    monkeypatch.setattr(slack_notifier.asyncpg, "connect", _connect)
    monkeypatch.setattr(slack_notifier.httpx, "AsyncClient", _factory)

    result = asyncio.run(slack_notifier.run(dsn="postgresql://dummy/x"))

    assert result["enabled"] is True
    assert result["dry_run"] is False
    assert result["found"] == 1
    assert result["sent"] == 1
    assert result["dry"] == 0
    assert result["failed"] == 0

    # POST body 검증 — block kit 구조
    assert len(_FakeAsyncClient.captured) == 1
    body = _FakeAsyncClient.captured[0]["body"]
    assert body["text"].startswith("[SignalForge][CRITICAL]")
    att = body["attachments"][0]
    assert att["color"] == "#d72631"  # critical
    types = [b["type"] for b in att["blocks"]]
    assert "header" in types and "section" in types and "context" in types

    # 라벨 'slack' 추가
    labels = [op["args"][0] for op in fake_conn.executed]
    assert labels == ["slack"]
    assert fake_conn.executed[0]["args"][1] == 200
