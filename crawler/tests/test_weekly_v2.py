"""insight.harvest_week_report_v2 단위 테스트 (Harvest 7 / X3).

검증 범위:
    1) render_harvest_week_md_v2 — 5개 절 + alert 24h/7d/30d 명시 + notebookcheck NULL
    2) run 통합 — collect_snapshot_v2 mock 후
       (a) HARVEST_WEEK_<date>_v2.md 가 생성
       (b) audit JSONL 에 round=harvest7 / track=X3 의 start/end 1쌍
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

from insight import harvest_week_report_v2 as hwr2  # noqa: E402


def _sample_snapshot_v7() -> hwr2.Harvest7Snapshot:
    return hwr2.Harvest7Snapshot(
        voc_total=132620,
        voc_24h=7651,
        voc_7d=80976,
        active_platforms_total=70,
        active_platforms_24h=44,
        active_platforms_7d=68,
        alert_events_24h=346,
        alert_events_7d=1619,
        alert_events_30d=1619,
        archive_rounds=["harvest3p", "harvest4", "harvest5", "R26"],
        gsmarena_forum_null_24h=133,
        gsmarena_forum_total_24h=236,
        hardware_fr_null_24h=86,
        hardware_fr_total_24h=375,
        notebookcheck_null_24h=66,
        notebookcheck_total_24h=182,
        xda_null_24h=55,
        xda_total_24h=72,
        xda_voc_total=149,
    )


def test_render_v2_contains_all_sections_and_alert_labels():
    md = hwr2.render_harvest_week_md_v2(date(2026, 6, 7), _sample_snapshot_v7())
    # 헤더 (v2)
    assert "# Harvest 1주 누적 보고 v2 — 2026-06-07" in md
    # 4 절
    assert "## 1. Harvest 1-7 진척" in md
    assert "## 2. 현재 KPI 스냅샷" in md
    assert "### 2-1. Alert 라벨 명시" in md
    assert "## 3. NULL 매핑 잔여 과제" in md
    assert "## 4. 안전장치" in md
    # Alert 라벨 분리 명시 (X3 핵심)
    assert "| **24h** | 346 |" in md
    assert "| **7d**  | 1,619 |" in md
    assert "| **30d** | 1,619 |" in md
    # 7d == 30d 일치 노트
    assert "라벨 drift 가 아닌 실측 일치" in md
    # NULL 잔여 (notebookcheck 추가)
    assert "55/72" in md           # xda
    assert "133/236" in md         # gsmarena_forum
    assert "86/375" in md          # hardware_fr
    assert "66/182" in md          # notebookcheck
    assert "22.9%" in md           # hardware_fr 86/375
    assert "36.3%" in md           # notebookcheck 66/182
    assert "76.4%" in md           # xda 55/72
    # Harvest 6 / 7 행 모두 포함
    assert "| harvest6p |" in md
    assert "| harvest7 |" in md
    # voc 누적
    assert "132,620" in md


def test_run_integration_writes_v2_and_audit(tmp_path, monkeypatch):
    """collect_snapshot_v2 mock 한 통합 — md 생성 + audit start/end."""
    report_dir = tmp_path
    audit_path = tmp_path / "backfill_audit.jsonl"
    snap = _sample_snapshot_v7()

    async def _fake_collect(report_dir):
        return snap

    target = date(2026, 6, 7)
    with mock.patch.object(hwr2, "collect_snapshot_v2", _fake_collect):
        result = asyncio.run(hwr2.run(
            target=target,
            report_dir=report_dir,
            audit_path=audit_path,
        ))

    # MD 파일
    md_path = report_dir / "HARVEST_WEEK_2026-06-07_v2.md"
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert "Harvest 1주 누적 보고 v2 — 2026-06-07" in content
    assert "132,620" in content
    # alert 명시
    assert "| **30d** | 1,619 |" in content

    # audit JSONL
    assert audit_path.exists()
    rows = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    starts = [r for r in rows if r.get("event") == "start" and r.get("round") == "harvest7"]
    ends = [r for r in rows if r.get("event") == "end" and r.get("round") == "harvest7"]
    assert len(starts) == 1
    assert len(ends) == 1
    assert starts[0]["track"] == "X3"
    assert ends[0]["track"] == "X3"
    assert ends[0]["status"] == "ok"
    # counters 에 alert 3종 모두 적재
    counters = ends[0]["counters"]
    assert counters["voc_total"] == 132620
    assert counters["alerts_24h"] == 346
    assert counters["alerts_7d"] == 1619
    assert counters["alerts_30d"] == 1619
    assert counters["archive_rounds"] == 4

    # 중간 이벤트 1줄
    md_events = [r for r in rows if r.get("event") == "harvest_week_md_v2"]
    assert len(md_events) == 1
    assert md_events[0]["path"].endswith("HARVEST_WEEK_2026-06-07_v2.md")

    # snapshot dict 반환
    assert result["snapshot"]["voc_total"] == 132620
    assert result["snapshot"]["alert_events_30d"] == 1619
    assert result["target_date"] == "2026-06-07"
