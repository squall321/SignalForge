"""Track C — R6/R7/R8 회귀 검증 (핵심 매칭이 손실되지 않는지 자동 감시).

라이브 backend (http://127.0.0.1:8000) 의 ``/api/v1/_internal/regression-baseline``
응답에서 8개 metric + alembic head 를 검증한다.  test_collection_status.py 와
동일한 live-server pattern (autouse engine.dispose fixture 의 cross-loop 충돌
회피).

검증 케이스 (instructions Track C 기준):
  1. Note 7 voc        >= 300 (R7 352)
  2. Fold 1 voc        >= 280 (R7 302)
  3. S22 voc           >= 350 (R7 414)
  4. S25 voc           >= 1800
  5. Buds 3 voc        >= 450 (R8 511)
  6. HN linked %       >= 15% (R8 22.67%)
  7. topics_filled     >= 35,000 (R8 42,935)
  8. products count    >= 380 (R8 389)
  +  alembic head      >= 0013

실행:
    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_regression_coverage.py -v
"""
import os

import httpx
import pytest


BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")

_TOP_KEYS = {
    "generated_at", "checks", "summary",
    "alembic_head", "alembic_min_head", "alembic_ok",
}
# 모든 check 가 공통으로 가져야 하는 필수 키 (R12 신설 check 는 R8 baseline 없음).
_CHECK_KEYS = {"name", "label", "current", "threshold", "ok"}
# R8 baseline 이 의미 있는 (1-8) check 는 baseline_r8 + delta_vs_baseline 보유
_R8_LEGACY_NAMES = {
    "note7_voc", "fold1_voc", "s22_voc", "s25_voc", "buds3_voc",
    "hn_linked_pct", "topics_filled", "products_count",
}
_EXPECTED_CHECK_NAMES = {
    "note7_voc", "fold1_voc", "s22_voc", "s25_voc", "buds3_voc",
    "hn_linked_pct", "topics_filled", "products_count",
    # R12 신설
    "hn_total", "voc_total",
    # Harvest3 Polish 신설 (Track H5)
    "hardware_fr_voc",
}
# baseline_r12 + delta_vs_baseline_r12 보유 대상 (Harvest3p 신설은 별도 baseline 명명).
_R12_LINEAGE_NAMES = _EXPECTED_CHECK_NAMES - {"hardware_fr_voc"}


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
    raise AssertionError(f"check '{name}' not in response. names={[c['name'] for c in body['checks']]}")


# ── 라이브 응답 스키마 + 8 회귀 케이스 ────────────────────────────────────


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_response_schema():
    body = _fetch()
    assert set(body.keys()) >= _TOP_KEYS, body.keys()
    assert isinstance(body["checks"], list)
    # 8 + alembic 합산이 summary.total
    assert body["summary"]["total"] == len(body["checks"]) + 1
    # 모든 check 공통 필수 키 보유
    for c in body["checks"]:
        assert set(c.keys()) >= _CHECK_KEYS, c
        # R12 신설 외 (R8 lineage) 는 baseline_r8 + delta_vs_baseline 필수
        if c["name"] in _R8_LEGACY_NAMES:
            assert "baseline_r8" in c, c
            assert "delta_vs_baseline" in c, c
        # R12 lineage 는 baseline_r12 + delta_vs_baseline_r12 보유 (H5 신설은 별도)
        if c["name"] in _R12_LINEAGE_NAMES:
            assert "baseline_r12" in c, c
            assert "delta_vs_baseline_r12" in c, c
    # 8개 metric 이름이 모두 존재
    names = {c["name"] for c in body["checks"]}
    assert names == _EXPECTED_CHECK_NAMES, names ^ _EXPECTED_CHECK_NAMES


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_note7_voc():
    """R6/R7 — Note 7 매칭 >= 300 (R7 352 유지 확인)."""
    c = _check(_fetch(), "note7_voc")
    assert c["ok"], c
    assert c["current"] >= 300, c
    assert c["threshold"] == 300
    assert c["baseline_r8"] == 352


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_fold1_voc():
    """R6/R7 — Fold 1 매칭 >= 250 (R8 302, R20 281 post-dedup)."""
    c = _check(_fetch(), "fold1_voc")
    assert c["ok"], c
    assert c["current"] >= 250, c
    assert c["threshold"] == 250
    assert c["baseline_r8"] == 302
    assert c["baseline_r20"] == 281


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_s22_voc():
    """R20 — S22 매칭 >= 200 (R8 414 → R20 218 post-dedup, R14 dedup 으로 진정한 중복 제거)."""
    c = _check(_fetch(), "s22_voc")
    assert c["ok"], c
    assert c["current"] >= 200, c
    assert c["threshold"] == 200
    assert c["baseline_r8"] == 414
    assert c["baseline_r20"] == 218


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_s25_voc():
    """R20 — S25 voc >= 800 (R8 2082 → R20 847 post-dedup, R14 dedup 영향)."""
    c = _check(_fetch(), "s25_voc")
    assert c["ok"], c
    assert c["current"] >= 800, c
    assert c["threshold"] == 800
    assert c["baseline_r20"] == 847


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_buds3_voc():
    """R20 — Buds 3 voc >= 200 (R8 511 → R20 210 post-dedup)."""
    c = _check(_fetch(), "buds3_voc")
    assert c["ok"], c
    assert c["current"] >= 200, c
    assert c["threshold"] == 200
    assert c["baseline_r8"] == 511
    assert c["baseline_r20"] == 210


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_hn_linked_pct():
    """R8 — HN linked >= 15% (R8 22.67%).  HN 사전 확장 효과 보호."""
    c = _check(_fetch(), "hn_linked_pct")
    assert c["ok"], c
    assert c["current"] >= 15.0, c
    assert c["threshold"] == 15.0
    # 추가 정합성: hn_total/hn_linked 노출
    assert "hn_total" in c and "hn_linked" in c
    assert c["hn_total"] > 0
    assert c["hn_linked"] <= c["hn_total"]


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_topics_filled():
    """R12 — topic 분류 채워진 voc >= 20,000 (R8 42,935; R12 25,410).

    R8→R12 사이 voc 모집단이 167k 로 늘었고 신규 25k 는 reprocess 전이라
    절대수가 줄어든 것이 정상.  R12 threshold 는 *현실적 절대 하한* 20,000.
    """
    c = _check(_fetch(), "topics_filled")
    assert c["ok"], c
    assert c["current"] >= 20_000, c
    assert c["threshold"] == 20_000
    assert c["baseline_r8"] == 42_935
    assert c["baseline_r12"] == 25_410


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_products_count():
    """R8 — products count >= 380 (R8 389)."""
    c = _check(_fetch(), "products_count")
    assert c["ok"], c
    assert c["current"] >= 380, c
    assert c["threshold"] == 380
    assert c["baseline_r8"] == 389


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_alembic_head():
    """R12 — alembic head revision >= 0014 (galaxy_master_timeline MV 보장)."""
    body = _fetch()
    assert body["alembic_head"] >= "0014", body["alembic_head"]
    assert body["alembic_ok"] is True


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_hn_total_r12():
    """R12 신설 — HN total voc >= 30,000 (R12 33,911 backfill 회귀 방지)."""
    c = _check(_fetch(), "hn_total")
    assert c["ok"], c
    assert c["current"] >= 30_000, c
    assert c["threshold"] == 30_000
    assert c["baseline_r12"] == 33_911


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_voc_total_r12():
    """R20 — voc_records 전체 >= 110,000 (R12 167,701 → R14 dedup → R20 117,958 post-dedup).

    R14 dedup (168k → 113k, -32%) 로 *진정한* 중복이 제거된 corpus.  hreshold 는
    post-dedup 안정 상태 (R20 117,958) 의 ~6% 안전 마진을 둔 절대 하한.
    """
    c = _check(_fetch(), "voc_total")
    assert c["ok"], c
    assert c["current"] >= 110_000, c
    assert c["threshold"] == 110_000
    assert c["baseline_r12"] == 167_701
    assert c["baseline_r20"] == 117_958


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_regression_summary_all_green():
    """10 개 metric + alembic 전체 ok — failed=0 일 때만 통과."""
    body = _fetch()
    s = body["summary"]
    assert s["failed"] == 0, (s, [c for c in body["checks"] if not c["ok"]])
    assert s["ok"] == s["total"]
