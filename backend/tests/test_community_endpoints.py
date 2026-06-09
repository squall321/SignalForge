"""
T3 커뮤니티 비교 6 endpoint 서비스-레벨 단위 테스트 (P3-3).

실행:
    cd backend && .venv/bin/python tests/test_community_endpoints.py
    cd backend && .venv/bin/pytest tests/test_community_endpoints.py -v

데이터셋:
- platform_health MV: 72 행 (P3-1)
- country_daily   MV: ~1000 행 (P3-1)
- voc_records:        115k+ 행 (~ 2026-05-16 ~ 2026-06-01)
"""
import asyncio
import os
import sys
import time
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.community_service import CommunityService, _kmeans  # noqa: E402


# ────────────────────────────────────────────────────────────────────
# 0) 순수 함수 단위 — KMeans
# ────────────────────────────────────────────────────────────────────
def test_kmeans_pure():
    """2 개의 명확히 분리된 클러스터 → 두 그룹으로 분리."""
    cluster_a = [(1.0, 1.0), (1.2, 0.9), (0.9, 1.1), (1.0, 1.2)]
    cluster_b = [(10.0, 10.0), (10.2, 9.8), (9.9, 10.1), (10.0, 10.3)]
    samples = cluster_a + cluster_b
    assigns, centroids, iters = _kmeans(samples, k=2, seed=42)
    assert len(assigns) == len(samples)
    assert len(centroids) == 2
    # a 그룹과 b 그룹이 서로 다른 cluster id 인지
    a_ids = set(assigns[: len(cluster_a)])
    b_ids = set(assigns[len(cluster_a):])
    assert len(a_ids) == 1
    assert len(b_ids) == 1
    assert a_ids != b_ids
    assert iters >= 1
    print(f"[ok] kmeans pure: iters={iters} a_ids={a_ids} b_ids={b_ids}")

    # k > n → degenerate (N=4, K=10 → K=N=4 로 강등)
    assigns2, _, _ = _kmeans(cluster_a, k=10, seed=42)
    assert len(set(assigns2)) == len(cluster_a)
    print(f"[ok] kmeans degenerate K>N: assignments={assigns2}")


# ────────────────────────────────────────────────────────────────────
# 1) /platforms/health
# ────────────────────────────────────────────────────────────────────
async def _test_health(svc: CommunityService):
    r = await svc.health()
    assert r.total == len(r.items)
    assert r.total >= 1, "platform_health MV 비어있음 — alembic upgrade + refresh 필요"
    assert r.active + r.idle + r.dead == r.total
    for it in r.items:
        assert it.platform_id > 0
        assert it.status in {"active", "idle", "dead"}
        assert it.posts_24h >= 0
        assert it.posts_7d >= 0
    print(f"[ok] health all: total={r.total} a={r.active} i={r.idle} d={r.dead}")

    # region 필터 — KR 만 (있으면)
    r_kr = await svc.health(region="KR")
    for it in r_kr.items:
        assert it.region == "KR"
    print(f"[ok] health region=KR: total={r_kr.total}")


# ────────────────────────────────────────────────────────────────────
# 2) /platforms/product-matrix (성능 ≤ 700ms)
# ────────────────────────────────────────────────────────────────────
async def _test_product_matrix(svc: CommunityService):
    t0 = time.perf_counter()
    r = await svc.product_matrix(since=None)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    # 검증
    for c in r.cells:
        assert c.n > 0
        assert -1.0 <= c.sent_avg <= 1.0
        assert 0.0 <= c.neg_rate <= 100.0
    print(f"[ok] product-matrix default: cells={len(r.cells)} "
          f"plats={len(r.platforms)} prods={len(r.products)} "
          f"elapsed={elapsed_ms:.1f}ms")
    assert elapsed_ms <= 700.0, (
        f"product-matrix 700ms 초과: {elapsed_ms:.1f}ms — 인덱스 점검 필요"
    )

    # 제품 필터
    r2 = await svc.product_matrix(since=None, products=["GS25"])
    for c in r2.cells:
        assert c.product == "GS25"
    print(f"[ok] product-matrix GS25: cells={len(r2.cells)}")

    # since 명시
    r3 = await svc.product_matrix(since="2026-05-20")
    assert r3.since == "2026-05-20"
    print(f"[ok] product-matrix since=2026-05-20: cells={len(r3.cells)}")


# ────────────────────────────────────────────────────────────────────
# 3) /platforms/dispersion
# ────────────────────────────────────────────────────────────────────
async def _test_dispersion(svc: CommunityService):
    r = await svc.dispersion(product_id=None, since=None)
    assert isinstance(r.boxplot, list)
    for b in r.boxplot:
        assert b.q1 <= b.median <= b.q3 + 1e-9
        assert -1.0 - 1e-6 <= b.q1 <= 1.0 + 1e-6
        assert -1.0 - 1e-6 <= b.q3 <= 1.0 + 1e-6
        assert b.lo <= b.hi
        assert b.n >= 8
    # outlier 는 평균적으로 존재하지만 0 일 수 있음 (모든 분포가 균일하면)
    for o in r.outliers:
        assert -1.0 - 1e-6 <= o.sentiment_score <= 1.0 + 1e-6
    assert len(r.outliers) <= 30
    print(f"[ok] dispersion: boxplot={len(r.boxplot)} outliers={len(r.outliers)}")


# ────────────────────────────────────────────────────────────────────
# 4) /platforms/early-signal
# ────────────────────────────────────────────────────────────────────
async def _test_early_signal(svc: CommunityService):
    r = await svc.early_signal(product_id=None, category=None)
    assert isinstance(r.timeline, list)
    # top 8 platform 까지
    plats_in_tl = set(p.platform for p in r.timeline)
    assert len(plats_in_tl) <= 8
    for p in r.timeline:
        assert p.n >= 1
        assert -1.0 - 1e-6 <= p.sent_avg <= 1.0 + 1e-6
    print(f"[ok] early-signal: timeline_points={len(r.timeline)} "
          f"plats={len(plats_in_tl)} event={'Y' if r.event else 'N'}")

    # 카테고리 필터
    r2 = await svc.early_signal(product_id=None, category="battery")
    assert r2.category == "battery"
    print(f"[ok] early-signal cat=battery: tl_points={len(r2.timeline)}")


# ────────────────────────────────────────────────────────────────────
# 5) /platforms/clusters
# ────────────────────────────────────────────────────────────────────
async def _test_clusters(svc: CommunityService):
    r = await svc.clusters(k=6)
    assert r.k == 6
    assert r.iterations >= 1 or len(r.points) <= 6
    # points 의 cluster id 가 centroid 범위 안에 있는지
    cluster_ids = set(p.cluster for p in r.points)
    centroid_ids = set(c.cluster for c in r.centroids)
    assert cluster_ids.issubset(centroid_ids), (
        f"point cluster id {cluster_ids} 가 centroid {centroid_ids} 범위 밖"
    )
    # 좌표는 (pos_rate, neg_rate) — [0, 100]
    for p in r.points:
        assert 0.0 - 1e-6 <= p.x <= 100.0 + 1e-6
        assert 0.0 - 1e-6 <= p.y <= 100.0 + 1e-6
    total_size = sum(c.size for c in r.centroids)
    assert total_size == len(r.points)
    print(f"[ok] clusters k=6: points={len(r.points)} "
          f"non_empty_centroids={sum(1 for c in r.centroids if c.size > 0)} "
          f"iters={r.iterations}")


# ────────────────────────────────────────────────────────────────────
# 6) /platforms/anomalies
# ────────────────────────────────────────────────────────────────────
async def _test_anomalies(svc: CommunityService):
    r = await svc.anomalies()
    assert isinstance(r, list)
    valid_reasons = {"dead_7d", "idle_24h", "extreme_negative_7d", "drop_rate"}
    for a in r:
        assert a.reason in valid_reasons, f"unknown reason: {a.reason}"
        assert a.code
        assert isinstance(a.detail, dict)
    by_reason = {}
    for a in r:
        by_reason[a.reason] = by_reason.get(a.reason, 0) + 1
    print(f"[ok] anomalies: total={len(r)} by_reason={by_reason}")


# ────────────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────────────
async def _run_all():
    test_kmeans_pure()

    async with AsyncSessionLocal() as db:
        svc = CommunityService(db)
        await _test_health(svc)
        await _test_product_matrix(svc)
        await _test_dispersion(svc)
        await _test_early_signal(svc)
        await _test_clusters(svc)
        await _test_anomalies(svc)


def test_community_endpoints():
    """pytest entry."""
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll 6 community endpoints + kmeans unit passed.")
