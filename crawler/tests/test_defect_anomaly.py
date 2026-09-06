# 결함 이상탐지 판단 로직 단위 테스트 — 임계·집중도 가드·세대 baseline 을 DB 없이 검증
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight import defect_anomaly as da  # noqa: E402


class _Row(dict):
    """asyncpg Record 처럼 [] 접근이 되는 최소 스텁."""


class _StubConn:
    """SQL 문자열로 분기해 미리 정한 결과를 돌려주는 스텁 커넥션."""

    def __init__(self, combos, product_total, baseline, released_days=None):
        self.combos = combos
        self.product_total = product_total
        self.baseline = baseline          # {"cnt": n, "total": n}
        self.released_days = released_days

    async def fetch(self, sql, *args):
        if "count(DISTINCT v.id) AS total" in sql and "voc_defects" not in sql:
            return [_Row(product_code="GX", total=self.product_total)]
        if "released_at IS NOT NULL" in sql and "products" in sql:
            return ([] if self.released_days is None
                    else [_Row(id=1, days=self.released_days)])
        return [_Row(**c) for c in self.combos]

    async def fetchrow(self, sql, *args):
        return _Row(cnt=self.baseline["cnt"], total=self.baseline["total"])


def _combo(cnt, hhi, component="battery", symptom="drain", severity="degraded"):
    return {"product_code": "GX", "product_id": 1, "component": component,
            "symptom": symptom, "severity": severity, "cnt": cnt, "hhi": hhi}


def _run(conn, thr=None):
    return asyncio.run(da.evaluate(conn, thr))


# ── 급등이면 잡는다 ───────────────────────────────────────────────────
def test_detects_clear_spike():
    # 최근 60/1000 = 6% vs baseline 10/1000 = 1% → 6배
    conn = _StubConn([_combo(60, hhi=0.15)], 1000, {"cnt": 10, "total": 1000})
    out = _run(conn)
    assert len(out) == 1
    assert out[0]["ratio"] >= da.RATIO_THRESHOLD
    assert out[0]["z"] >= da.Z_THRESHOLD
    assert out[0]["metric"] == "defect:GX:battery:drain"


# ── 단일 커뮤니티 쏠림은 배제한다 (이 탐지기의 핵심 가드) ─────────────
def test_single_community_concentration_suppressed():
    # hhi=0.9 → 유효 플랫폼 1.1개. 급등해도 알리지 않아야 한다.
    conn = _StubConn([_combo(60, hhi=0.9)], 1000, {"cnt": 10, "total": 1000})
    assert _run(conn) == []


def test_diverse_sources_pass():
    conn = _StubConn([_combo(60, hhi=0.2)], 1000, {"cnt": 10, "total": 1000})   # 5.0 플랫폼
    assert len(_run(conn)) == 1


# ── 표본이 적으면 잡지 않는다 ─────────────────────────────────────────
def test_below_min_count_not_evaluated():
    # _RECENT_SQL 이 min_count 로 이미 걸러주지만, 방어적으로 적은 건수는 z 가 못 넘는다
    conn = _StubConn([_combo(2, hhi=0.1)], 1000, {"cnt": 10, "total": 1000})
    assert _run(conn) == []


# ── 급등이 아니면 잡지 않는다 ─────────────────────────────────────────
def test_flat_not_flagged():
    conn = _StubConn([_combo(50, hhi=0.1)], 1000, {"cnt": 50, "total": 1000})
    assert _run(conn) == []


def test_decline_not_flagged():
    conn = _StubConn([_combo(20, hhi=0.1)], 1000, {"cnt": 100, "total": 1000})
    assert _run(conn) == []


# ── baseline 이 없으면(모수 0) 평가하지 않는다 ────────────────────────
def test_no_baseline_skipped():
    conn = _StubConn([_combo(60, hhi=0.1)], 1000, {"cnt": 0, "total": 0})
    assert _run(conn) == []


# ── 심각도 승격 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("defect_sev,expected", [
    ("safety", "critical"),
    ("non_functional", "critical"),
    ("degraded", "warning"),
    ("cosmetic", "warning"),
])
def test_severity_escalation(defect_sev, expected):
    conn = _StubConn([_combo(60, hhi=0.1, severity=defect_sev)], 1000,
                     {"cnt": 10, "total": 1000})
    out = _run(conn)
    assert out and out[0]["severity"] == expected


# ── 운영자가 임계를 올리면 덜 잡힌다 (alert_rules.threshold 배선) ─────
def test_operator_threshold_respected():
    conn = _StubConn([_combo(30, hhi=0.1)], 1000, {"cnt": 10, "total": 1000})  # 3배
    assert len(_run(conn, thr=2.0)) == 1
    assert _run(conn, thr=10.0) == []


# ── 신제품은 세대 baseline 을 쓴다 ────────────────────────────────────
def test_new_product_uses_lifecycle_baseline():
    conn = _StubConn([_combo(60, hhi=0.1)], 1000, {"cnt": 10, "total": 1000},
                     released_days=30)          # NEW_PRODUCT_DAYS 이내
    out = _run(conn)
    assert out and out[0]["baseline_mode"] == "lifecycle"


def test_mature_product_uses_own_history():
    conn = _StubConn([_combo(60, hhi=0.1)], 1000, {"cnt": 10, "total": 1000},
                     released_days=900)         # 오래된 제품
    out = _run(conn)
    assert out and out[0]["baseline_mode"] == "history"


# ── baseline 0 이어도 배수가 무한대로 튀지 않는다 ─────────────────────
def test_zero_baseline_is_bounded():
    conn = _StubConn([_combo(60, hhi=0.1)], 1000, {"cnt": 0, "total": 500})
    out = _run(conn)
    assert out and out[0]["ratio"] < 1e6 and out[0]["ratio"] > 0
