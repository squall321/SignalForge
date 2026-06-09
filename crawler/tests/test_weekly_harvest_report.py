"""insight.harvest_week_report 단위 테스트 (Harvest 5 / V5).

검증 범위:
    1) render_harvest_week_md — 5개 절 헤더 + 누적 표 + 잔여 과제 표
    2) build_slack_digest — 누적 voc / NULL 비율 포함
    3) post_slack — 키 없음 → graceful skip, mock urlopen → sent
    4) run 통합 — collect_snapshot / weekly_monitor 모두 mock 한 상태로
       (a) HARVEST_WEEK_<date>.md 가 실제 생성되는지
       (b) audit JSONL 에 round=harvest5 track=V5 의 start/end 가 적재되는지
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight import harvest_week_report as hwr  # noqa: E402


def _sample_snapshot() -> hwr.Harvest5Snapshot:
    return hwr.Harvest5Snapshot(
        voc_total=129827,
        voc_24h=8573,
        voc_7d=80458,
        active_platforms_total=69,
        active_platforms_24h=52,
        active_platforms_7d=69,
        alert_events_24h=301,
        alert_events_7d=1472,
        archive_rounds=["harvest3p", "harvest4", "R26"],
        gsmarena_forum_null_24h=161,
        gsmarena_forum_total_24h=236,
        hardware_fr_null_24h=198,
        hardware_fr_total_24h=375,
        xda_voc_24h=0,
        xda_voc_total=77,
    )


def test_render_harvest_week_md_contains_all_sections():
    md = hwr.render_harvest_week_md(date(2026, 6, 7), _sample_snapshot())
    # 헤더
    assert "# Harvest 1주 누적 보고 — 2026-06-07" in md
    # 4개 절
    assert "## 1. Harvest 1-5 진척" in md
    assert "## 2. 현재 KPI 스냅샷" in md
    assert "## 3. Harvest 5 잔여 과제" in md
    assert "## 4. 안전장치" in md
    # Harvest 1-5 행이 모두 포함 (5개)
    assert "| harvest1 |" in md
    assert "| harvest2 |" in md
    assert "| harvest3p |" in md
    assert "| harvest4 |" in md
    assert "| harvest5 |" in md
    # 누적 voc 및 활성 사이트
    assert "129,827" in md
    assert "활성 사이트: total **69**" in md
    # 잔여 과제 NULL 비율 (정확도)
    assert "161/236" in md
    assert "198/375" in md
    assert "(68.2%)" in md  # gsmarena 161/236
    assert "(52.8%)" in md  # hardware_fr 198/375


def test_render_harvest_week_md_delta_marker():
    """Harvest 진척 표는 voc_total 증분을 (+N) 으로 표시."""
    md = hwr.render_harvest_week_md(date(2026, 6, 7), _sample_snapshot())
    # harvest2 - harvest1 = 122231 - 119739 = +2,492
    assert "+2,492" in md
    # harvest4 - harvest3p = 127466 - 124500 = +2,966
    assert "+2,966" in md


def test_build_slack_digest_includes_kpis():
    payload = hwr.build_slack_digest(date(2026, 6, 7), _sample_snapshot())
    assert payload["text"].startswith("[SignalForge][harvest-week] 2026-06-07")
    blocks = payload["attachments"][0]["blocks"]
    assert blocks[0]["type"] == "header"
    assert "Harvest 1주 누적 2026-06-07" in blocks[0]["text"]["text"]
    summary = blocks[1]["text"]["text"]
    assert "voc_total=129,827" in summary
    assert "voc24h=8,573" in summary
    assert "active=69" in summary
    assert "GSM_null=68.2%" in summary
    assert "HWfr_null=52.8%" in summary
    # context block 에 round/track 표기
    ctx = blocks[2]["elements"][0]["text"]
    assert "round=`harvest5`" in ctx
    assert "track=`V5`" in ctx


def test_post_slack_skips_when_no_webhook(monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    result = hwr.post_slack(date(2026, 6, 7), _sample_snapshot())
    assert result["status"] == "skipped"
    assert result["reason"] == "no webhook"
    assert result["http_status"] is None


def test_post_slack_force_skip(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.test/abc")
    result = hwr.post_slack(date(2026, 6, 7), _sample_snapshot(), force_skip=True)
    assert result["status"] == "skipped"
    assert result["reason"] == "force_skip"


def test_post_slack_sends_with_mock_urlopen(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.test/xyz")
    captured: Dict[str, Any] = {}

    class _FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_open(req, timeout=5.0):
        captured["url"] = req.full_url
        captured["body"] = req.data.decode("utf-8")
        return _FakeResp()

    result = hwr.post_slack(date(2026, 6, 7), _sample_snapshot(), _opener=_fake_open)
    assert result["status"] == "sent"
    assert result["http_status"] == 200
    body = json.loads(captured["body"])
    assert "Harvest" in body["text"]
    assert "voc_total=129,827" in body["attachments"][0]["blocks"][1]["text"]["text"]


def test_run_integration_writes_md_and_audit(tmp_path, monkeypatch):
    """collect_snapshot / weekly_monitor 모두 mock 한 통합 실행.

    검증:
      - HARVEST_WEEK_<date>.md 가 tmp_path 에 실제 생성
      - audit JSONL 에 round=harvest5 / track=V5 / event=start, event=end 1쌍
      - slack 은 키 없으므로 skipped
    """
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    report_dir = tmp_path
    audit_path = tmp_path / "backfill_audit.jsonl"

    snap = _sample_snapshot()

    async def _fake_collect(report_dir):
        return snap

    async def _fake_weekly_run(**kwargs):
        return {"json_path": report_dir / "weekly_monitor_2026-23.json",
                "md_path": report_dir / "weekly_monitor_2026-06-07.md",
                "alerts": 1,
                "slack": {"status": "skipped"}}

    target = date(2026, 6, 7)
    with mock.patch.object(hwr, "collect_snapshot", _fake_collect), \
         mock.patch.object(hwr.weekly_monitor, "run", _fake_weekly_run):
        result = asyncio.run(hwr.run(
            target=target,
            report_dir=report_dir,
            slack=True,
            invoke_weekly=True,
            audit_path=audit_path,
        ))

    # MD 파일 존재
    md_path = report_dir / "HARVEST_WEEK_2026-06-07.md"
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert "# Harvest 1주 누적 보고 — 2026-06-07" in content
    assert "129,827" in content

    # audit JSONL 에 start + end 1쌍
    assert audit_path.exists()
    rows = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    starts = [r for r in rows if r.get("event") == "start" and r.get("round") == "harvest5"]
    ends = [r for r in rows if r.get("event") == "end" and r.get("round") == "harvest5"]
    assert len(starts) == 1
    assert len(ends) == 1
    assert starts[0]["track"] == "V5"
    assert ends[0]["track"] == "V5"
    assert ends[0]["status"] == "ok"
    # end 의 counters 에 KPI 들이 누적
    counters = ends[0]["counters"]
    assert counters["voc_total"] == 129827
    assert counters["voc_24h"] == 8573
    assert counters["active_total"] == 69
    assert counters["alerts_24h"] == 301
    assert counters["archive_rounds"] == 3
    assert counters["weekly_alerts"] == 1

    # harvest_week_md 중간 이벤트 1줄
    md_events = [r for r in rows if r.get("event") == "harvest_week_md"]
    assert len(md_events) == 1
    assert md_events[0]["path"].endswith("HARVEST_WEEK_2026-06-07.md")

    # slack 결과 (키 없음 → skipped)
    assert result["slack"]["status"] == "skipped"
    # snapshot dict 반환 검증
    assert result["snapshot"]["voc_total"] == 129827
    assert result["target_date"] == "2026-06-07"
