"""monitoring.health_check 단위 테스트.

실 DB(read-only) + 순수함수 양쪽을 검증한다.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitoring.health_check import (
    HealthReport,
    PlatformHealth,
    build_report,
    collect_platform_stats,
    collect_totals,
    load_beat_platforms,
    render_markdown,
    send_alert,
    write_report,
)


# --- 1) Beat schedule 로드 ----------------------------------------------
def test_load_beat_platforms_returns_known_codes():
    codes = load_beat_platforms()
    assert isinstance(codes, set)
    # 핵심 사이트는 beat schedule 에 반드시 등록되어 있어야 함
    assert "reddit" in codes
    assert "amazon_us" in codes
    assert len(codes) >= 20


# --- 2) 실 DB 집계 (read-only) ------------------------------------------
def test_collect_totals_positive():
    total, last_24h = collect_totals()
    assert total > 0
    assert last_24h >= 0
    assert last_24h <= total


def test_collect_platform_stats_shape():
    now = datetime.now(timezone.utc)
    stats = collect_platform_stats(now)
    assert len(stats) > 0
    p = stats[0]
    assert isinstance(p, PlatformHealth)
    assert p.code
    assert p.status in {"active", "idle", "dead"}
    assert p.rows_24h <= p.rows_7d


# --- 3) Status 분류 로직 ------------------------------------------------
def test_status_classification_via_build():
    """build_report 가 active/idle/dead 합계가 platforms 총수와 일치하는지."""
    rep = build_report()
    total = len(rep.platforms)
    bucketed = (
        sum(1 for p in rep.platforms if p.status == "active")
        + sum(1 for p in rep.platforms if p.status == "idle")
        + sum(1 for p in rep.platforms if p.status == "dead")
    )
    assert bucketed == total


# --- 4) Markdown 렌더링 -------------------------------------------------
def test_render_markdown_contains_sections():
    rep = HealthReport(
        generated_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        total_rows=113971,
        rows_24h_total=12345,
        platforms=[
            PlatformHealth(
                code="reddit", name="Reddit", is_active=True,
                last_collected_at=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
                rows_24h=500, rows_7d=3500, avg_per_day_7d=500.0,
                status="active", in_beat_schedule=True,
            ),
            PlatformHealth(
                code="dead_site", name="DeadSite", is_active=True,
                last_collected_at=None,
                rows_24h=0, rows_7d=0, avg_per_day_7d=0.0,
                status="dead", in_beat_schedule=True,
            ),
        ],
        translation_total=1000,
        translation_failed=120,
        translation_fail_rate=0.12,
        product_total=1000,
        product_tagged=850,
        product_tag_rate=0.85,
        category_total=1000,
        category_empty=200,
        category_empty_rate=0.2,
        critical_alerts=["[DEAD] dead_site"],
    )
    md = render_markdown(rep)
    # 필수 섹션
    assert "# SignalForge 품질 모니터링 리포트" in md
    assert "## 1. Critical Alerts" in md
    assert "## 2. 플랫폼 Status 요약" in md
    assert "## 3. 사이트별 수집 현황" in md
    assert "## 4. NLP 품질" in md
    assert "## 5. Beat schedule vs 실제 dispatch" in md
    # 데이터 포함
    assert "reddit" in md and "dead_site" in md
    assert "[DEAD]" in md
    # 표 형식 — DEAD 가 위쪽
    dead_pos = md.find("dead_site")
    reddit_pos = md.find("reddit")
    assert dead_pos < reddit_pos


def test_render_markdown_handles_empty_alerts():
    rep = HealthReport(
        generated_at=datetime.now(timezone.utc),
        total_rows=0, rows_24h_total=0, platforms=[],
    )
    md = render_markdown(rep)
    assert "(없음)" in md
    assert "(7일간 표본 없음)" in md


# --- 5) 파일 출력 -------------------------------------------------------
def test_write_report_creates_dated_file(tmp_path, monkeypatch):
    from monitoring import health_check as hc
    monkeypatch.setattr(hc, "REPORTS_DIR", tmp_path)
    when = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    rep = HealthReport(generated_at=when, total_rows=1, rows_24h_total=0, platforms=[])
    out = hc.write_report(rep, when=when)
    assert out == tmp_path / "health_2026-06-01.md"
    assert out.exists()
    txt = out.read_text(encoding="utf-8")
    assert "SignalForge" in txt


# --- 6) Alert (webhook 없을 때 stdout fallback) -------------------------
def test_send_alert_without_webhook_returns_true(monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    assert send_alert("title", "body") is True
