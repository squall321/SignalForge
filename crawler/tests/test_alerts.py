"""crawler.alerts 단위 테스트.

- dispatcher.send_alert: webhook URL 없을 때 reports/alerts.log 에 append
- dispatcher: Slack/Discord 포맷 분기
- rules.check_all_rules: read-only 실 DB 호출 (스키마/SQL 동작 확인)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# crawler 디렉토리를 path 에 보장
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts import dispatcher  # noqa: E402
from alerts.rules import check_all_rules  # noqa: E402


# ─── dispatcher: webhook URL 없을 때 파일 로그 동작 ──────────────────

def test_send_alert_logged_when_no_webhook(tmp_path, monkeypatch):
    # ALERT_WEBHOOK_URL 제거
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    # alerts.log 경로를 임시 디렉토리로 리다이렉트
    fake_log = tmp_path / "alerts.log"
    monkeypatch.setattr(dispatcher, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(dispatcher, "ALERTS_LOG", fake_log)

    res = dispatcher.send_alert(
        {"title": "t1", "text": "hello", "fields": {"k": "v"}, "rule": "unit_test"},
        level="warning",
    )

    assert res["status"] == "logged"
    assert res["provider"] == "file"
    assert res["level"] == "warning"

    # 파일에 1줄 JSON 이 기록되어야 함
    lines = fake_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["level"] == "warning"
    assert entry["payload"]["title"] == "t1"
    assert entry["payload"]["rule"] == "unit_test"


def test_send_alert_clamps_invalid_level(tmp_path, monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(dispatcher, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(dispatcher, "ALERTS_LOG", tmp_path / "alerts.log")

    res = dispatcher.send_alert({"title": "x", "text": "y"}, level="emergency")
    assert res["level"] == "info"  # 알 수 없는 레벨은 info 로 강등


# ─── dispatcher: 포맷 함수 단위 검증 ──────────────────────────────────

def test_format_slack_structure():
    body = dispatcher._format_slack(
        {"title": "T", "text": "X", "fields": {"a": 1, "b": "two"}},
        "critical",
    )
    assert "attachments" in body and len(body["attachments"]) == 1
    att = body["attachments"][0]
    assert att["title"] == "T"
    assert att["text"] == "X"
    assert att["color"] == dispatcher._LEVEL_COLORS_SLACK["critical"]
    assert {f["title"] for f in att["fields"]} == {"a", "b"}


def test_format_discord_structure():
    body = dispatcher._format_discord(
        {"title": "T", "text": "X", "fields": {"a": 1}},
        "warning",
    )
    assert "embeds" in body and len(body["embeds"]) == 1
    emb = body["embeds"][0]
    assert emb["title"] == "T"
    assert emb["color"] == dispatcher._LEVEL_COLORS_DISCORD["warning"]
    assert emb["fields"][0]["name"] == "a"


# ─── rules: 실제 DB 호출 (read-only). DB 없는 환경이면 skip. ──────────

def _db_available() -> bool:
    """asyncpg 기반 async 엔진으로 SELECT 1 시도."""
    try:
        import asyncio
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from alerts.rules import _async_db_url

        async def _ping() -> bool:
            eng = create_async_engine(_async_db_url(), pool_pre_ping=True)
            try:
                async with eng.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                return True
            finally:
                await eng.dispose()

        return asyncio.run(_ping())
    except Exception:
        return False


@pytest.mark.skipif(not _db_available(), reason="signalforge DB 접근 불가")
def test_check_all_rules_runs_against_real_db():
    """SQL 이 실제 voc_records 스키마와 호환되는지 + 반환 구조 확인."""
    out = check_all_rules(run_daily=True)
    assert isinstance(out, list)
    # daily_summary 는 run_daily=True 일 때 반드시 1건 이상 (info)
    daily = [a for a in out if a["rule"] == "daily_summary"]
    assert len(daily) == 1, "daily_summary 는 정확히 1건 발생해야 함"

    d = daily[0]
    assert d["level"] == "info"
    assert "Total" in d["payload"]["fields"]
    assert "Positive" in d["payload"]["fields"]

    # 모든 알림 페이로드는 title/text/rule 키를 가진다
    for a in out:
        p = a["payload"]
        assert "title" in p and "text" in p and "rule" in p
        assert a["level"] in ("info", "warning", "critical")
        assert a["rule"] in ("sentiment_drop", "site_dead", "issue_spike", "daily_summary")
