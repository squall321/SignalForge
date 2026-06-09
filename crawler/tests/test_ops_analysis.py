"""insight.ops_trend_analysis 단위 테스트 (R19 트랙 D).

DB / HTTP 없이 순수 함수 (analyse, recommendations, render_markdown,
_moving_avg, _trend_slope, _count_threshold_breaches) 가 임계 / 변화율 /
이동평균을 올바르게 계산하고, 권고가 위반 패턴에 매핑되며, fallback (파일
직독) 도 안전한지 확인.

요구사항 (R19 트랙 D): 최소 1 케이스.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight import ops_trend_analysis as ota  # noqa: E402


def _series_7day() -> list[dict]:
    """7일치 가짜 시계열 — 일부러 모든 임계를 *어딘가에서* 한 번씩 위반시킨다.

    날짜는 오래된 → 최신 (endpoint 응답 규약).
    """
    base = date(2026, 5, 30)
    return [
        # day 0 — 정상 baseline
        {"date": "2026-05-30", "status": "ok",
         "voc_last": 9529, "voc_delta_pct": None,
         "sentiment_null_rate": 0.01, "topic_rate": 0.83,
         "grounding_last": 0.42, "regression_failed": 0, "violations_count": 0},
        # day 1 — voc 감소 (15%, 임계 미달)
        {"date": "2026-05-31", "status": "ok",
         "voc_last": 7853, "voc_delta_pct": -17.59,
         "sentiment_null_rate": 0.02, "topic_rate": 0.87,
         "grounding_last": 0.40, "regression_failed": 0, "violations_count": 1},
        # day 2 — 정상
        {"date": "2026-06-01", "status": "ok",
         "voc_last": 7998, "voc_delta_pct": 1.84,
         "sentiment_null_rate": 0.03, "topic_rate": 0.87,
         "grounding_last": 0.38, "regression_failed": 0, "violations_count": 0},
        # day 3 — voc 절반 이하 (≥50% 감소 — VIOLATION)
        {"date": "2026-06-02", "status": "warning",
         "voc_last": 3000, "voc_delta_pct": -62.49,
         "sentiment_null_rate": 0.04, "topic_rate": 0.86,
         "grounding_last": 0.35, "regression_failed": 0, "violations_count": 2},
        # day 4 — topic 분류율 30% 급락 (≥20% 감소 — VIOLATION) + grounding < 0.30 (VIOLATION)
        {"date": "2026-06-03", "status": "critical",
         "voc_last": 37135, "voc_delta_pct": 1137.83,
         "sentiment_null_rate": 0.05, "topic_rate": 0.55,
         "grounding_last": 0.25, "regression_failed": 1, "violations_count": 3},
        # day 5 — sentiment NULL 12% (≥10% — VIOLATION)
        {"date": "2026-06-04", "status": "critical",
         "voc_last": 4806, "voc_delta_pct": -87.06,  # voc 50%+ 감소 — VIOLATION
         "sentiment_null_rate": 0.12, "topic_rate": 0.89,
         "grounding_last": 0.36, "regression_failed": 5, "violations_count": 2},
        # day 6 — 회복 추세
        {"date": "2026-06-05", "status": "warning",
         "voc_last": 5315, "voc_delta_pct": 10.59,
         "sentiment_null_rate": 0.04, "topic_rate": 0.88,
         "grounding_last": 0.40, "regression_failed": 0, "violations_count": 1},
    ]


def _trend_payload() -> dict:
    return {
        "days": 7,
        "generated_at": "2026-06-05T12:00:00+00:00",
        "available": [s["date"] for s in _series_7day()][::-1],
        "series": _series_7day(),
    }


# ── 1. 분석 핵심 ───────────────────────────────────────────────────────
def test_analyse_extracts_kpis_and_breaches():
    """analyse() 가 7일 KPI / 임계 위반 / 이동평균을 정확히 계산."""
    a = ota.analyse(_trend_payload())

    # 기간/적재
    assert a["days_requested"] == 7
    assert a["days_available"] == 7

    # voc 통계 (시작 9529, 끝 5315)
    assert a["voc_first"] == 9529
    assert a["voc_last"] == 5315
    # (5315-9529)/9529 = -44.22%
    assert a["voc_change_pct_7d"] == pytest.approx(-44.22, abs=0.05)
    assert a["voc_min"] == 3000
    assert a["voc_max"] == 37135

    # 임계 위반 카운트 — 의도적으로 심어 둔 패턴이 정확히 잡혀야
    b = a["breaches"]
    # day 3 (-62%) + day 5 (-87%) → 2건
    assert b["voc_drop_50pct"] == 2
    # day 5 (0.12) → 1건
    assert b["sentiment_null_breach"] == 1
    # day 4 (0.87→0.55 = 36.78% 감소) → 1건
    assert b["topic_rate_drop_20pct"] == 1
    # day 4 (0.25) → 1건
    assert b["grounding_below_0_30"] == 1
    # day 4 (1) + day 5 (5) → 2건
    assert b["regression_failed_days"] == 2

    # 누적
    assert a["violations_total"] == 9   # 0+1+0+2+3+2+1
    assert a["regression_failed_total"] == 6  # 0+0+0+0+1+5+0

    # 이동평균: 7일 window 라 마지막 1개만 not-None, 앞 6개는 None
    ma_voc = a["moving_avg_7d"]["voc_last"]
    assert len(ma_voc) == 7
    assert ma_voc[:6] == [None, None, None, None, None, None]
    assert ma_voc[6] is not None
    # 평균 = (9529+7853+7998+3000+37135+4806+5315)/7 ≈ 10805.14
    assert ma_voc[6] == pytest.approx(10805.14, abs=0.5)


def test_moving_avg_and_trend_slope_edge_cases():
    """이동평균: window 미만은 None / 빈 입력은 None. slope: <2 개 → None."""
    # 7일 window, 5개 → 모두 None
    assert ota._moving_avg([1, 2, 3, 4, 5], window=7) == [None]*5
    # 3일 window, 5개 → [None, None, 2, 3, 4]
    assert ota._moving_avg([1, 2, 3, 4, 5], window=3) == [None, None, 2.0, 3.0, 4.0]
    # 모두 None → None 유지
    assert ota._moving_avg([None, None, None], window=2) == [None, None, None]

    assert ota._trend_slope([]) is None
    assert ota._trend_slope([1]) is None
    # (10-2)/(3-1) = 4.0
    assert ota._trend_slope([2, 6, 10]) == 4.0
    # None 끼어도 숫자만으로 계산
    assert ota._trend_slope([2, None, 6, None, 10]) == 4.0


def test_recommendations_map_to_breach_patterns():
    """위반 패턴별로 권고가 정확히 매핑된다."""
    a = ota.analyse(_trend_payload())
    recs = ota.recommendations(a)
    joined = "\n".join(recs)

    # 5종 위반 모두 언급
    assert "voc 일일 50%+ 감소" in joined
    assert "sentiment_label NULL" in joined
    assert "topic 분류율" in joined
    assert "grounding" in joined
    assert "regression" in joined
    # 7일 voc -44% 라 30% 임계 추가 권고 발화
    assert "7일 voc 누적 감소" in joined


def test_recommendations_clean_when_no_breaches():
    """위반 0이면 안정 안내 1줄만."""
    a = ota.analyse({
        "days": 7,
        "available": ["2026-06-05"]*7,
        "series": [
            {"date": f"2026-06-{i:02d}", "status": "ok",
             "voc_last": 5000, "voc_delta_pct": 0.0,
             "sentiment_null_rate": 0.01, "topic_rate": 0.88,
             "grounding_last": 0.45, "regression_failed": 0, "violations_count": 0}
            for i in range(1, 8)
        ],
    })
    recs = ota.recommendations(a)
    assert any("임계 위반 없음" in r for r in recs)


def test_analyse_handles_empty_series():
    """series 가 비어도 graceful — 모든 KPI 키가 None / 0."""
    a = ota.analyse({"days": 7, "available": [], "series": []})
    assert a["days_available"] == 0
    assert a["voc_first"] is None
    assert a["voc_last"] is None
    assert a["voc_change_pct_7d"] is None
    assert a["violations_total"] == 0
    assert a["breaches"]["voc_drop_50pct"] == 0


# ── 2. 보고서 ─────────────────────────────────────────────────────────
def test_render_markdown_contains_kpi_table_and_recs():
    """보고서에 핵심 표·임계 표·시계열·권고가 모두 포함."""
    md = ota.render_markdown(ota.analyse(_trend_payload()))
    assert "# 운영 상태 7일 누적 트렌드 분석" in md
    assert "## 1. 핵심 KPI 7일 요약" in md
    assert "## 2. 임계 위반 누적" in md
    assert "## 3. 일별 시계열" in md
    assert "## 4. 7일 이동 평균" in md
    assert "## 5. 권고" in md
    # 표 내용 sanity
    assert "voc_last" in md
    assert "9,529" in md   # 시작 voc 천단위 포맷
    assert "-44.22%" in md  # 7일 누적 변화율


# ── 3. fallback (파일 직독) ────────────────────────────────────────────
def test_fetch_trend_file_fallback(tmp_path: Path, monkeypatch):
    """endpoint 가 죽었을 때 reports/ 직독으로 동일 스키마 생성."""
    # ops_status_*.json 3개 적재 — 오래된 → 최신
    for i, d in enumerate(["2026-06-03", "2026-06-04", "2026-06-05"]):
        (tmp_path / f"ops_status_{d}.json").write_text(json.dumps({
            "target_date": d, "status": "ok",
            "voc_last": 1000 * (i + 1), "voc_prev": None,
            "sentiment_null_rate": 0.0, "topic_rate": 0.9,
            "grounding_last": 0.4, "regression_failed": 0, "violations_count": 0,
        }), encoding="utf-8")

    # use_http=False → endpoint 안 거치고 직독
    trend = ota.fetch_trend(7, use_http=False, report_dir=tmp_path)
    assert len(trend["series"]) == 3
    # 변화율 — endpoint 와 동일 로직: prev_voc 기준
    assert trend["series"][0]["voc_delta_pct"] is None     # 첫날
    assert trend["series"][1]["voc_delta_pct"] == 100.0    # 1000→2000
    assert trend["series"][2]["voc_delta_pct"] == 50.0     # 2000→3000


def test_backfill_dry_run_does_not_write(tmp_path: Path, monkeypatch):
    """--dry-run 은 파일 생성 없이 예정 경로만 반환 + 기존 파일 보호."""
    # 사전에 한 날짜 파일을 둔다 (기존 보호 확인용)
    existing = tmp_path / "ops_status_2026-06-05.json"
    existing.write_text(json.dumps({"target_date": "2026-06-05", "voc_last": 9999}),
                        encoding="utf-8")

    # asyncpg.connect 를 가짜로 — fetch() 가 2개 fixture 를 돌려준다.
    class _FakeRow(dict):
        def __getitem__(self, k):
            return super().__getitem__(k)

    class _FakeConn:
        async def fetch(self, sql, *args):
            if "voc_records" in sql:
                return [
                    _FakeRow({"d": "2026-06-04", "n": 4806,
                              "null_rate": 0.0, "topic_rate": 0.89}),
                    _FakeRow({"d": "2026-06-05", "n": 5315,
                              "null_rate": 0.0, "topic_rate": 0.88}),
                ]
            return [
                _FakeRow({"d": "2026-06-04", "n": 423, "crit": 0}),
                _FakeRow({"d": "2026-06-05", "n": 11, "crit": 0}),
            ]
        async def close(self):
            return None

    import asyncpg as _ap
    async def _fake_connect(*a, **kw):
        return _FakeConn()
    monkeypatch.setattr(_ap, "connect", _fake_connect)

    created = ota.backfill_from_db(7, report_dir=tmp_path, dry_run=True)

    # 06-04 만 생성 예정 (06-05 는 기존 보호)
    assert len(created) == 1
    assert created[0].name == "ops_status_2026-06-04.json"
    # dry_run 이므로 실제로는 만들어지지 않음
    assert not (tmp_path / "ops_status_2026-06-04.json").exists()
    # 기존 파일은 무손상
    body = json.loads(existing.read_text(encoding="utf-8"))
    assert body["voc_last"] == 9999


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
