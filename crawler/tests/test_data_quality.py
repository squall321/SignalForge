"""insight/data_quality 단위 테스트 — R12 트랙 E2 (2026-06-04).

검증:
  1. compute_length_dist 분포 정확성 (avg/p10/p90)
  2. compute_duplicate_rate 중복 식별 (SHA1 기반)
  3. evaluate_alerts — 임계 미달 시 alert 생성, 정상 시 없음
  4. evaluate_alerts — new_voc_count=0 → 단일 warning + early return
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from insight.data_quality import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    DataQualityReport,
    LengthDist,
    compute_duplicate_rate,
    compute_length_dist,
    evaluate_alerts,
)


# ── compute_length_dist ─────────────────────────────────────────────────
def test_length_dist_basic():
    """1..10 → avg=5.5, p10≈1.9, p90≈9.1."""
    d = compute_length_dist(list(range(1, 11)))
    assert d.n == 10
    assert d.avg == 5.5
    # linear percentile: p10 = 1.9, p90 = 9.1
    assert d.p10 == 1.9
    assert d.p90 == 9.1


def test_length_dist_empty():
    """빈 입력 → 전 필드 None."""
    d = compute_length_dist([])
    assert d.n == 0
    assert d.avg is None
    assert d.p10 is None
    assert d.p90 is None


def test_length_dist_single():
    """단일 값 → avg = p10 = p90 = 그 값."""
    d = compute_length_dist([42])
    assert d.n == 1
    assert d.avg == 42.0
    assert d.p10 == 42.0
    assert d.p90 == 42.0


# ── compute_duplicate_rate ──────────────────────────────────────────────
def test_duplicate_rate_no_duplicates():
    rate, n = compute_duplicate_rate(["a", "b", "c"])
    assert rate == 0.0
    assert n == 0


def test_duplicate_rate_with_duplicates():
    """5건 중 'a'가 3번 → 중복분 2 (5-2=3 unique). rate = 2/5 = 0.4."""
    rate, n = compute_duplicate_rate(["a", "a", "a", "b", "c"])
    assert n == 2
    assert rate == 0.4


def test_duplicate_rate_empty():
    rate, n = compute_duplicate_rate([])
    assert rate == 0.0
    assert n == 0


def test_duplicate_rate_unicode_safe():
    """한글/이모지 본문도 정상 hash."""
    rate, n = compute_duplicate_rate(["갤럭시 🔥", "갤럭시 🔥", "iPhone"])
    assert n == 1
    assert abs(rate - 1/3) < 1e-9


# ── evaluate_alerts ─────────────────────────────────────────────────────
def _good_report() -> DataQualityReport:
    """모든 metric 이 임계 안에 들어오는 정상 보고서."""
    return DataQualityReport(
        hours=24,
        window_start="2026-06-03T00:00:00+00:00",
        window_end="2026-06-04T00:00:00+00:00",
        new_voc_count=1000,
        length_dist=LengthDist(n=1000, avg=120.0, p10=20.0, p90=300.0),
        duplicate_rate=0.01,
        product_match_rate=0.30,
        sentiment_null_rate=0.10,
        topic_classified_rate=0.18,
        active_platforms=25,
    )


def test_alerts_none_when_all_pass():
    r = _good_report()
    alerts = evaluate_alerts(r, DEFAULT_THRESHOLDS)
    assert alerts == [], alerts


def test_alerts_zero_voc_short_circuit():
    """new_voc_count=0 → 단일 warning + return (다른 metric 평가 X)."""
    r = DataQualityReport(
        hours=24, window_start="x", window_end="y", new_voc_count=0,
    )
    alerts = evaluate_alerts(r, DEFAULT_THRESHOLDS)
    assert len(alerts) == 1
    assert alerts[0]["metric"] == "new_voc_count"
    assert alerts[0]["value"] == 0


def test_alerts_content_length_under_threshold():
    r = _good_report()
    r.length_dist = LengthDist(n=10, avg=5.0, p10=1.0, p90=10.0)
    alerts = evaluate_alerts(r, DEFAULT_THRESHOLDS)
    names = {a["metric"] for a in alerts}
    assert "content_length_avg" in names


def test_alerts_duplicate_over_threshold():
    r = _good_report()
    r.duplicate_rate = 0.10  # 5% 초과
    alerts = evaluate_alerts(r, DEFAULT_THRESHOLDS)
    assert any(a["metric"] == "duplicate_rate" for a in alerts)


def test_alerts_product_match_under_threshold():
    r = _good_report()
    r.product_match_rate = 0.02  # 5% 미만
    alerts = evaluate_alerts(r, DEFAULT_THRESHOLDS)
    assert any(a["metric"] == "product_match_rate" for a in alerts)


def test_alerts_sentiment_null_over_threshold():
    r = _good_report()
    r.sentiment_null_rate = 0.50  # 30% 초과
    alerts = evaluate_alerts(r, DEFAULT_THRESHOLDS)
    assert any(a["metric"] == "sentiment_null_rate" for a in alerts)


def test_alerts_topic_under_threshold_info_level():
    r = _good_report()
    r.topic_classified_rate = 0.05  # 10% 미만
    alerts = evaluate_alerts(r, DEFAULT_THRESHOLDS)
    topic_alerts = [a for a in alerts if a["metric"] == "topic_classified_rate"]
    assert topic_alerts
    assert topic_alerts[0]["level"] == "info"  # warning 아닌 info


def test_alerts_active_platforms_under_threshold():
    r = _good_report()
    r.active_platforms = 5  # 임계 10 미만
    alerts = evaluate_alerts(r, DEFAULT_THRESHOLDS)
    assert any(a["metric"] == "active_platforms" for a in alerts)


def test_alerts_custom_thresholds():
    """사용자 정의 임계 — 더 엄격하면 정상도 alert."""
    r = _good_report()
    strict = {**DEFAULT_THRESHOLDS, "topic_classified_rate_min": 0.25}
    alerts = evaluate_alerts(r, strict)
    assert any(a["metric"] == "topic_classified_rate" for a in alerts)


def test_report_to_dict_is_serializable():
    """report.to_dict() → JSON 직렬화 가능."""
    import json

    r = _good_report()
    r.alerts = evaluate_alerts(r, DEFAULT_THRESHOLDS)
    payload = r.to_dict()
    # round-trip 통과해야 함
    s = json.dumps(payload, ensure_ascii=False)
    parsed = json.loads(s)
    assert parsed["new_voc_count"] == 1000
    assert parsed["length_dist"]["avg"] == 120.0
