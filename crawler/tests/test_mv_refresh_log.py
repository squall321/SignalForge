# MV 갱신 기록과 품질 보고서의 신선도 판정 — 기록이 근사(pg_stat)보다 앞서고, 근사는 기록이 없을 때만 쓴다
from datetime import datetime, timedelta, timezone
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base import mv_refresh_log  # noqa: E402
from insight.quality_report import MVStats, _mv_stat, render_markdown  # noqa: E402

NOW = datetime(2026, 11, 7, 0, 30, tzinfo=timezone.utc)


def test_record_round_trips_and_leaves_no_temp_file(tmp_path):
    t = datetime(2026, 11, 7, 0, 9, 8, tzinfo=timezone.utc)
    mv_refresh_log.record("kg_edges_daily", t, "concurrently", 15948, base=tmp_path)
    mv_refresh_log.record("kg_edges_daily", t + timedelta(minutes=30), "concurrently", 18236, base=tmp_path)
    assert mv_refresh_log.last_refreshed("kg_edges_daily", base=tmp_path) == t + timedelta(minutes=30)
    assert [p.name for p in tmp_path.iterdir()] == ["kg_edges_daily.json"]


def test_missing_or_broken_record_is_none(tmp_path):
    assert mv_refresh_log.last_refreshed("country_daily", base=tmp_path) is None
    (tmp_path / "country_daily.json").write_text("{not json", encoding="utf-8")
    assert mv_refresh_log.last_refreshed("country_daily", base=tmp_path) is None
    (tmp_path / "country_daily.json").write_text('{"view": "country_daily"}', encoding="utf-8")
    assert mv_refresh_log.last_refreshed("country_daily", base=tmp_path) is None


def test_record_beats_stale_analyze_time():
    """2026-11-07 오탐 — 30분마다 갱신되는데 analyze 시각으로는 3,440분 묵어 보였다."""
    approx = datetime(2026, 11, 4, 15, 9, 50, tzinfo=timezone.utc)
    got = _mv_stat("kg_edges_daily", approx, NOW - timedelta(minutes=21), NOW)
    assert got.basis == "refresh_log" and round(got.age_minutes) == 21


def test_recent_analyze_does_not_hide_a_stalled_refresh():
    """갱신이 멈췄는데 누가 ANALYZE 를 돌렸다 — 근사가 더 최근이어도 기록(멈춘 시각)을 믿는다."""
    got = _mv_stat("country_daily", NOW - timedelta(minutes=5), NOW - timedelta(days=2), NOW)
    assert got.basis == "refresh_log" and got.age_minutes == 2 * 24 * 60


def test_without_a_record_falls_back_to_pg_stat():
    got = _mv_stat("platform_health", datetime(2026, 11, 6, 23, 39, 7), None, NOW)   # naive → UTC
    assert got.basis == "pg_stat" and round(got.age_minutes) == 51
    assert _mv_stat("platform_health", datetime(1970, 1, 1, tzinfo=timezone.utc), None, NOW).error == "no stats"


def test_report_marks_only_the_approximated_rows():
    from insight.quality_report import CacheStats, GroundingStats, PerfStats, QualityReport
    from datetime import date
    rep = QualityReport(target_date=date(2026, 11, 7), cache=CacheStats(), grounding=GroundingStats(),
                        mvs=[MVStats(name="kg_edges_daily", last_refresh=NOW, age_minutes=21.0, basis="refresh_log"),
                             MVStats(name="platform_health", last_refresh=NOW, age_minutes=90.0, basis="pg_stat")],
                        perf=PerfStats())
    rows = {ln.split("`")[1]: ln for ln in render_markdown(rep).splitlines() if ln.startswith("| `")}
    assert "근사" not in rows["kg_edges_daily"] and "⚠" not in rows["kg_edges_daily"]
    assert "⚠ 1시간 초과 (pg_stat 근사)" in rows["platform_health"]
