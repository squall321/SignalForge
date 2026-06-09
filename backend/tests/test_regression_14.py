"""Data Grow R5 Track L5 — 회귀 baseline 14개 항목 검증.

배경:
    Data Grow R5 (2026-06-09) 에서 회귀 baseline 11 → 14 로 확장.
    신규 3개:
      - mx_match_pct  >= 50.0  (Data Clean 효과 회귀 보호)
      - mx_rich_pct   >= 40.0  (리치 신호 비율 회귀 보호)
      - archive_pct   >= 50.0  (한국 5사 + 노이즈 누적 정리분 회귀 보호)

검증:
    1. /api/v1/_internal/regression-baseline 응답에 14개 check 존재
    2. 신규 3 check (mx_match_pct / mx_rich_pct / archive_pct) status=ok
    3. summary.total == 15 (14 checks + alembic)
    4. 응답 키 스키마 보존 (label / current / threshold / ok / baseline_data_grow_r5)

실행::

    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_regression_14.py -v
"""
import os

import httpx
import pytest


BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:18000")


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


# ── L5 신규 14 check 존재성 ──────────────────────────────────────────────────


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_fourteen_checks_present():
    """14개 check name 이 모두 존재 (11 기존 + L5 신규 3)."""
    body = _fetch()
    names = {c["name"] for c in body["checks"]}
    expected = {
        "note7_voc", "fold1_voc", "s22_voc", "s25_voc", "buds3_voc",
        "hn_linked_pct", "topics_filled", "products_count",
        "hn_total", "voc_total",
        "hardware_fr_voc",
        # L5 신규
        "mx_match_pct", "mx_rich_pct", "archive_pct",
    }
    assert names == expected, names ^ expected


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_summary_total_15():
    """summary.total == 15 (14 checks + alembic)."""
    body = _fetch()
    s = body["summary"]
    assert s["total"] == 15, s


# ── L5 신규 3 check threshold ────────────────────────────────────────────────


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_mx_match_pct_ok():
    """mx_match_pct >= 50.0 — ok=True. Data Clean 효과 회귀 보호."""
    c = _check(_fetch(), "mx_match_pct")
    assert c["ok"] is True, c
    assert c["current"] >= 50.0, c
    assert c["threshold"] == 50.0
    assert c["baseline_data_grow_r5"] == 75.8
    assert "active" in c and "mx_match" in c


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_mx_rich_pct_ok():
    """mx_rich_pct >= 40.0 — ok=True. 리치 신호 비율 회귀 보호."""
    c = _check(_fetch(), "mx_rich_pct")
    assert c["ok"] is True, c
    assert c["current"] >= 40.0, c
    assert c["threshold"] == 40.0
    assert c["baseline_data_grow_r5"] == 51.1
    assert "active" in c and "mx_rich" in c


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_archive_pct_ok():
    """archive_pct >= 50.0 — ok=True. 누적 archive 정리분 회귀 보호."""
    c = _check(_fetch(), "archive_pct")
    assert c["ok"] is True, c
    assert c["current"] >= 50.0, c
    assert c["threshold"] == 50.0
    assert c["baseline_data_grow_r5"] == 59.8
    assert "archived" in c and "voc_records_total" in c
