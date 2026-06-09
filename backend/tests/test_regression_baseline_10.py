"""Harvest3 Polish Track H5 — 회귀 baseline 10번째 항목 (`hardware_fr_voc`) 정식 추가 검증.

배경:
    Harvest3 Polish (2026-06-06) 에서 Hardware.fr 백필 48 → 206 (4.3× 확장, +158 inserted)
    성과를 동결.  R20 memory 의 9개 + 본 신규 1개 = **10개 baseline metric** 으로 회귀
    감시 범위가 확장됨 (실제 코드는 R12 신설 hn_total/voc_total 포함 11개 + alembic).

검증:
    1. /api/v1/_internal/regression-baseline 응답에 ``hardware_fr_voc`` check 존재
    2. current >= threshold (150) — ok=True
    3. baseline_harvest3p == 206 (Harvest3 Polish 실측값 동결)
    4. summary.total == 12 (11 checks + alembic), summary.failed == 0
    5. 응답 키 스키마 보존 (label / current / threshold / ok)

실행::

    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_regression_baseline_10.py -v
"""
import os

import httpx
import pytest


BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")


def _alive() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=1.5).status_code == 200
    except Exception:
        return False


def _fetch() -> dict:
    with httpx.Client(base_url=BACKEND, timeout=20.0) as c:
        r = c.get("/api/v1/_internal/regression-baseline")
        assert r.status_code == 200, f"status={r.status_code} body={r.text}"
        return r.json()


def _check(body: dict, name: str) -> dict:
    for c in body["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError(
        f"check '{name}' not in response. names={[c['name'] for c in body['checks']]}"
    )


# ── Harvest3 Polish 신규 — hardware_fr_voc ──────────────────────────────────


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_hardware_fr_voc_present():
    """11번째 check `hardware_fr_voc` 가 응답에 포함."""
    body = _fetch()
    names = {c["name"] for c in body["checks"]}
    assert "hardware_fr_voc" in names, names


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_hardware_fr_voc_threshold():
    """current >= threshold (150) — ok=True. Harvest3p 백필 4.3× 확장 보호."""
    c = _check(_fetch(), "hardware_fr_voc")
    assert c["ok"] is True, c
    assert c["current"] >= 150, c
    assert c["threshold"] == 150
    assert c["label"] == "Hardware.fr 전체 voc"


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_hardware_fr_voc_baseline_harvest3p():
    """baseline_harvest3p == 206 (2026-06-06 동결값)."""
    c = _check(_fetch(), "hardware_fr_voc")
    assert c["baseline_harvest3p"] == 206
    # delta_vs_baseline_harvest3p == current - 206 (정합성)
    assert c["delta_vs_baseline_harvest3p"] == c["current"] - 206


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_summary_total_with_h5():
    """summary.total == 12 (11 checks + alembic), failed == 0."""
    body = _fetch()
    s = body["summary"]
    assert s["total"] == 12, s
    assert s["failed"] == 0, (s, [c for c in body["checks"] if not c["ok"]])
    assert s["ok"] == 12


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_all_eleven_checks_present():
    """11개 check name 이 모두 존재 (10 기존 + hardware_fr_voc 신규)."""
    body = _fetch()
    names = {c["name"] for c in body["checks"]}
    expected = {
        "note7_voc", "fold1_voc", "s22_voc", "s25_voc", "buds3_voc",
        "hn_linked_pct", "topics_filled", "products_count",
        "hn_total", "voc_total",
        "hardware_fr_voc",  # H5 신규
    }
    assert names == expected, names ^ expected
