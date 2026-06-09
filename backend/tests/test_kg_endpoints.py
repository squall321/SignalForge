"""
KG API service-level unit tests (P2 T1).

직접 실행:
    cd backend && .venv/bin/python tests/test_kg_endpoints.py

pytest:
    cd backend && .venv/bin/pytest tests/test_kg_endpoints.py -v
"""
import asyncio
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.kg_service import KGService  # noqa: E402


async def _test_graph(svc: KGService):
    """1) /kg/graph 기본 응답 무결성 + min_weight 동작."""
    end = date.today()
    start = end - timedelta(days=60)

    # min_weight=1 — 적어도 일부 엣지/노드 반환
    r = await svc.get_graph(
        start=start, end=end, edge_types=["all"], top_n=80, min_weight=1
    )
    assert r.stats.edges_count == len(r.edges)
    assert r.stats.nodes_count == len(r.nodes)
    assert r.stats.period["start"] == str(start)
    assert r.stats.period["end"] == str(end)
    assert len(r.edges) > 0, "kg_edges_daily 데이터가 비어있다"
    for ed in r.edges:
        assert ed.source.startswith("product:")
        assert ed.weight >= 1
        assert -1.0 <= ed.sent_avg <= 1.0
    # node 합집합이 정확히 양 끝점 union 이어야 함
    node_ids = {n.id for n in r.nodes}
    for ed in r.edges:
        assert ed.source in node_ids
        assert ed.target in node_ids
    print(f"[ok] graph all: nodes={r.stats.nodes_count} edges={r.stats.edges_count}")

    # edge_types 단일 (product_category) — target 이 category 만
    r2 = await svc.get_graph(
        start=start, end=end, edge_types=["product_category"], top_n=40, min_weight=1
    )
    for ed in r2.edges:
        assert ed.type == "product_category"
        assert ed.target.startswith("category:")
    print(f"[ok] graph product_category: nodes={r2.stats.nodes_count} edges={r2.stats.edges_count}")

    # top_n 제한
    r3 = await svc.get_graph(
        start=start, end=end, edge_types=["all"], top_n=5, min_weight=1
    )
    assert len(r3.edges) <= 5
    print(f"[ok] graph top_n=5: edges={len(r3.edges)} (≤5)")


async def _test_node_samples(svc: KGService):
    """2) /kg/node/{id}/samples — product / category / 잘못된 id."""
    # 잘못된 형식
    r = await svc.get_node_samples("invalid_no_colon", limit=3)
    assert r == []

    # 알 수 없는 타입
    r = await svc.get_node_samples("unknown:foo", limit=3)
    assert r == []

    # product 샘플 — 그래프에 등장한 product 1개를 골라 검증
    end = date.today()
    start = end - timedelta(days=60)
    g = await svc.get_graph(
        start=start, end=end, edge_types=["all"], top_n=10, min_weight=1
    )
    product_node = next((n for n in g.nodes if n.type == "product"), None)
    if product_node:
        samples = await svc.get_node_samples(product_node.id, limit=5)
        assert isinstance(samples, list)
        assert len(samples) <= 5
        # 샘플이 있으면 필수 필드 확인
        for s in samples:
            assert s.voc_id > 0
            assert isinstance(s.snippet, str)
        print(f"[ok] samples {product_node.id}: n={len(samples)}")
    else:
        print("[skip] no product node in graph")

    # category 샘플 (battery)
    samples = await svc.get_node_samples("category:battery", limit=3)
    assert isinstance(samples, list)
    print(f"[ok] samples category:battery: n={len(samples)}")


async def _test_search(svc: KGService):
    """3) /kg/search — 한국어/영어/빈 쿼리."""
    # 빈 쿼리
    r = await svc.search("", limit=5)
    assert r == []

    # 영어 — samsung 키워드는 voc_keywords 최상위
    r = await svc.search("samsung", limit=10)
    assert len(r) >= 1
    # 응답 정렬 검증: score 내림차순
    for i in range(1, len(r)):
        assert r[i - 1].score >= r[i].score
    print(f"[ok] search samsung: n={len(r)} top={r[0].type}/{r[0].label} score={r[0].score}")

    # 한국어 — 배터리 → voc_keywords 에 충분히 존재
    r = await svc.search("배터리", limit=10)
    assert len(r) >= 3, f"배터리 검색 결과가 부족하다: {len(r)}"
    types_seen = {h.type for h in r}
    assert "keyword" in types_seen
    print(f"[ok] search 배터리: n={len(r)} types={types_seen}")


async def _run_all():
    async with AsyncSessionLocal() as db:
        svc = KGService(db)
        await _test_graph(svc)
        await _test_node_samples(svc)
        await _test_search(svc)


def test_kg_graph():
    asyncio.run(_test_graph_pytest())


def test_kg_node_samples():
    asyncio.run(_test_node_samples_pytest())


def test_kg_search():
    asyncio.run(_test_search_pytest())


async def _test_graph_pytest():
    async with AsyncSessionLocal() as db:
        await _test_graph(KGService(db))


async def _test_node_samples_pytest():
    async with AsyncSessionLocal() as db:
        await _test_node_samples(KGService(db))


async def _test_search_pytest():
    async with AsyncSessionLocal() as db:
        await _test_search(KGService(db))


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nAll KG service tests passed.")
