"""insight.weekly_monitor 단위 테스트.

원 케이스 (R10):
  1) evaluate_alerts — 5종 룰의 정확한 발화 / 미발화
  2) collect_grounding_history — 임시 history JSON 윈도우 절단 + avg/min/max

P4 Harvest 3p 신규:
  3) render_markdown_report — 모든 섹션 헤더와 핵심 지표 라인이 포함
  4) post_slack_digest — mock urlopen 으로 sent / 키 없을 때 graceful skip 확인
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.weekly_monitor import (  # noqa: E402
    build_slack_digest_payload,
    collect_grounding_history,
    evaluate_alerts,
    post_slack_digest,
    render_markdown_report,
)


def test_evaluate_alerts_triggers_all_five_rules():
    """5종 룰을 한꺼번에 발화시키는 합성 payload — 각 rule 가 정확히 1회씩."""
    payload = {
        "target_date": "2026-06-04",  # 오늘 — 룰 평가에서 제외됨
        "voc_daily": [
            {"day": "2026-06-02", "voc_count": 10000, "sentiment_avg": 0.30},
            {"day": "2026-06-03", "voc_count": 3000,  "sentiment_avg": 0.05},  # -70%, -0.25
            {"day": "2026-06-04", "voc_count": 100,   "sentiment_avg": 0.10},  # 오늘 (제외)
        ],
        "collection_status": {"total_active": 10},          # < 15 → 발화
        "grounding": {"last": 0.30},                        # < 0.4 → 발화
        "regression": {
            "summary": {"failed": 1, "ok": 8, "total": 9},
            "checks": [{"name": "note7_voc", "ok": False}],
            "alembic_ok": True,
        },
    }
    alerts = evaluate_alerts(payload)
    rules = sorted(a["rule"] for a in alerts)
    assert rules == [
        "llm_grounding_below_0p4",
        "regression_failed",
        "sentiment_shift_0p2",
        "sites_active_below_15",
        "voc_daily_drop_50pct",
    ], f"got {rules}"


def test_evaluate_alerts_quiet_payload_returns_empty():
    """모든 지표가 정상이면 0 알림."""
    payload = {
        "target_date": "2026-06-04",
        "voc_daily": [
            {"day": "2026-06-02", "voc_count": 10000, "sentiment_avg": 0.10},
            {"day": "2026-06-03", "voc_count": 9500,  "sentiment_avg": 0.12},
            {"day": "2026-06-04", "voc_count": 100,   "sentiment_avg": 0.05},  # 오늘 (제외)
        ],
        "collection_status": {"total_active": 62},
        "grounding": {"last": 0.55},
        "regression": {"summary": {"failed": 0}, "alembic_ok": True, "checks": []},
    }
    assert evaluate_alerts(payload) == []


def test_collect_grounding_history_window_slicing(tmp_path: Path):
    """history JSON 의 윈도우 절단 + 통계 계산."""
    history = [
        {"date": "2026-05-29", "grounding_score": 0.10},   # 윈도우 밖
        {"date": "2026-05-30", "grounding_score": 0.20},   # 경계 (days=7, target=2026-06-04 → 시작 2026-05-29 — 포함)
        {"date": "2026-06-02", "grounding_score": 0.40},
        {"date": "2026-06-03", "grounding_score": 0.60},
    ]
    (tmp_path / "insight_grounding_history.json").write_text(
        json.dumps(history), encoding="utf-8"
    )
    out = collect_grounding_history(tmp_path, target=date(2026, 6, 4), days=7)
    # 4 entry 모두 윈도우 안 (2026-05-29 ~ 2026-06-04)
    assert len(out["entries"]) == 4
    assert out["min"] == 0.10
    assert out["max"] == 0.60
    assert out["last"] == 0.60
    # avg = (0.1+0.2+0.4+0.6)/4 = 0.325
    assert out["avg"] == pytest.approx(0.325, abs=1e-4)


# ── P4 Harvest 3p: MD 렌더 + Slack 다이제스트 ────────────────────────────
def _sample_payload() -> Dict[str, Any]:
    """4 절 (헤더/추세/사이트/알림·LLM·회귀/신규 사이트/이상치) 모두 채운 합성 payload."""
    return {
        "generated_at": "2026-06-06T00:30:00+00:00",
        "target_date": "2026-06-06",
        "window_days": 7,
        "iso_year_week": "2026-23",
        "voc_daily": [
            {"day": "2026-05-31", "voc_count": 7853, "sentiment_avg": 0.12},
            {"day": "2026-06-01", "voc_count": 25,   "sentiment_avg": 0.10},   # 저조
            {"day": "2026-06-02", "voc_count": 0,    "sentiment_avg": None},   # 죽음
            {"day": "2026-06-05", "voc_count": 9304, "sentiment_avg": 0.08},
            {"day": "2026-06-06", "voc_count": 4514, "sentiment_avg": 0.09},
        ],
        "collection_status": {
            "total_active": 67,
            "total_inactive": 5,
            "total_records_24h": 10256,
            "health_counts": {"active": 50, "slow": 10, "stale": 5, "dead": 2},
        },
        "alert_trends": {
            "days": 7,
            "cooldown_violations_24h": 0,
            "rules_total": 20,
            "fires_window": 107,
            "fires_24h": 12,
            "silent_rules": 3,
        },
        "grounding": {
            "window_days": 7,
            "entries": [{"date": "2026-06-05", "grounding_score": 0.55}],
            "avg": 0.51, "min": 0.42, "max": 0.60, "last": 0.55,
        },
        "regression": {
            "summary": {"total": 9, "ok": 9, "failed": 0},
            "checks": [],
            "alembic_head": "0018", "alembic_ok": True,
        },
        "new_sites": [
            {"code": "hardware_fr",   "first_seen": "2026-06-06",
             "voc_total": 48, "voc_24h": 48, "active_24h": True},
            {"code": "notebookcheck", "first_seen": "2026-06-06",
             "voc_total": 138, "voc_24h": 138, "active_24h": True},
            {"code": "zdnet_kr",      "first_seen": "2026-06-06",
             "voc_total": 4, "voc_24h": 0, "active_24h": False},
        ],
        "alerts": [],
    }


def test_render_markdown_report_contains_all_sections():
    """MD 보고서는 5개 절 헤더, 추세 표, 신규 사이트 행, 알림 안내까지 모두 포함."""
    md = render_markdown_report(_sample_payload())
    # 헤더 & 절
    assert "# 운영 1주 모니터 — 2026-06-06 (7d)" in md
    assert "## 1. 7일 추세" in md
    assert "## 2. 사이트 상태" in md
    assert "## 3. 알림·LLM·회귀" in md
    assert "## 4. 신규 사이트 진척 (14d)" in md
    assert "## 5. 이상치 자동 탐지" in md
    # 추세 표: 라벨 — ≥50 정상, 1~49 저조, 0 죽음 + None sentiment 는 — 로
    assert "| 2026-06-06 | 4,514 | 0.090 | 정상 |" in md
    assert "| 2026-06-01 | 25 | 0.100 | 저조 |" in md
    assert "| 2026-06-02 | 0 | — | 죽음 |" in md
    # 사이트 상태 active 숫자
    assert "active: **67**" in md
    # 신규 사이트 표 (active_24h 마크)
    assert "| hardware_fr | 2026-06-06 | 48 | 48 | yes |" in md
    assert "| zdnet_kr | 2026-06-06 | 4 | 0 | no |" in md
    # 이상치 없음 처리
    assert "발화 없음" in md


def test_render_markdown_report_renders_alerts_table_when_present():
    payload = _sample_payload()
    payload["alerts"] = [
        {"rule": "voc_daily_drop_50pct", "severity": "warning",
         "message": "voc 2026-06-04=4806 → 2026-06-05=9304 (-93.5%)"},
    ]
    md = render_markdown_report(payload)
    assert "| rule | severity | message |" in md
    assert "voc_daily_drop_50pct" in md
    assert "warning" in md


def test_post_slack_digest_skips_when_no_webhook(monkeypatch):
    """ALERT_WEBHOOK_URL 와 SLACK_WEBHOOK_URL 모두 미설정 → graceful skip."""
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    result = post_slack_digest(_sample_payload())
    assert result["status"] == "skipped"
    assert result["reason"] == "no webhook"
    assert result["http_status"] is None


def test_post_slack_digest_force_skip_ignores_env(monkeypatch):
    """force_skip=True → 키가 있어도 송출 안함."""
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.test/abc")
    result = post_slack_digest(_sample_payload(), force_skip=True)
    assert result["status"] == "skipped"
    assert result["reason"] == "force_skip"


def test_post_slack_digest_sends_with_mock_urlopen(monkeypatch):
    """mock urlopen 으로 200 응답 → status=sent. payload 포맷 검증."""
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.test/xyz")

    captured: Dict[str, Any] = {}

    class _FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_open(req, timeout=5.0):
        # urllib.request.Request → 데이터 검증
        captured["url"] = req.full_url
        captured["body"] = req.data.decode("utf-8") if req.data else ""
        captured["method"] = req.get_method()
        captured["content_type"] = req.headers.get("Content-type") or req.headers.get("Content-Type")
        return _FakeResp()

    result = post_slack_digest(_sample_payload(), _opener=_fake_open)
    assert result["status"] == "sent"
    assert result["http_status"] == 200
    assert captured["url"] == "https://hooks.slack.test/xyz"
    assert captured["method"] == "POST"
    body = json.loads(captured["body"])
    # block kit 1단 요약 구조 확인
    assert body["text"].startswith("[SignalForge][weekly-monitor]")
    assert body["attachments"][0]["blocks"][0]["type"] == "header"
    assert "1주 모니터 2026-06-06" in body["attachments"][0]["blocks"][0]["text"]["text"]
    # voc_24h 와 active 가 요약에 포함
    text_block = body["attachments"][0]["blocks"][1]["text"]["text"]
    assert "voc24h=4,514" in text_block
    assert "active=67" in text_block
    # URL 포함
    ctx = body["attachments"][0]["blocks"][2]["elements"][0]["text"]
    assert "대시보드 열기" in ctx


def test_post_slack_digest_failed_on_http_error(monkeypatch):
    """5xx 응답 → status=failed, http_status 보존."""
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.test/xyz")

    class _Resp500:
        status = 500
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _opener(req, timeout=5.0):
        return _Resp500()

    result = post_slack_digest(_sample_payload(), _opener=_opener)
    assert result["status"] == "failed"
    assert result["http_status"] == 500


def test_build_slack_digest_payload_summary_fields():
    """payload → block kit dict 의 요약 텍스트가 필수 KPI 5개를 포함."""
    p = _sample_payload()
    payload = build_slack_digest_payload(p)
    # text fallback
    assert "voc24h=4,514" in payload["text"]
    summary_block = payload["attachments"][0]["blocks"][1]["text"]["text"]
    assert "voc24h=4,514" in summary_block
    assert "active=67" in summary_block
    assert "alerts=0" in summary_block
    assert "new_sites_active=2/3" in summary_block
    assert "regression 9/9" in summary_block
