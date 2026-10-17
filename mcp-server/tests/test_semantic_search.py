# 시맨틱 검색이 실DB 에서 표기와 무관하게 같은 결과를 내고, 범위·근거·시간을 지키는지 고정한다.
"""실측 기준(2026-10-17, 활성 99만 행).

  'S25 울트라 발열' · 'Galaxy S25 Ultra overheating'   → 같은 251건, 같은 순위
  응답시간 — 좁은 범위 0.4~0.7초, 증상 전역 0.6초, 넓은 범위(브랜드·기기) 1~4초
고친 함정 셋 —
  · 불용어 구절('too hot' → 'hot')이 동의어에 섞여 '발열' 이 20초 시간 초과
  · 범위 CTE 에 통계가 없어 3만 행 범위에서 행마다 to_tsvector (17초)
  · 브랜드 전체를 CTE 로 만들어 수십만 행 (8초)
"""
import asyncio
import os
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in ("/shared", str(ROOT.parent / "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(ROOT))

_DB = os.getenv("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not _DB, reason="DATABASE_URL 없음")
pytest.importorskip("ontology", reason="crawler 사전을 import 할 수 없음 — /crawler 바인드 필요")


def _run(q, **kw):
    from tools.semantic import semantic_search_tool
    return asyncio.run(semantic_search_tool(q, **kw))


def _sql_rows(sql, params):
    from sqlalchemy import text
    from db import get_db_session

    async def go():
        async with get_db_session() as db:
            return [tuple(r) for r in (await db.execute(text(sql), params)).all()]
    return asyncio.run(go())


def test_spelling_and_language_do_not_change_results():
    """이게 시맨틱 검색의 존재 이유다 — 글자가 아니라 뜻으로 찾는다."""
    variants = ["S25 울트라 발열", "Galaxy S25 Ultra overheating", "s25u 발열", "갤럭시 S25 울트라 과열"]
    runs = [_run(v, limit=30) for v in variants]
    ids = [[r["id"] for r in x["results"]] for x in runs]
    totals = {x["total"] for x in runs}
    assert len(totals) == 1, f"건수가 표기마다 다르다: {dict(zip(variants, [x['total'] for x in runs]))}"
    assert all(i == ids[0] for i in ids), "같은 뜻인데 순위가 다르다"
    assert runs[0]["total"] > 50, "결과가 너무 적다 — 해석이나 범위가 깨졌다"


def test_product_scope_is_respected():
    r = _run("S25 울트라 발열", limit=30)
    ids = [x["id"] for x in r["results"]]
    linked = {row[0] for row in _sql_rows(
        "SELECT l.voc_id FROM voc_product_links l JOIN products p ON p.id = l.product_id "
        "WHERE p.code = 'GS25U' AND l.voc_id = ANY(CAST(:ids AS bigint[]))", {"ids": ids})}
    assert set(ids) <= linked, f"GS25U 에 링크되지 않은 글이 섞였다: {set(ids) - linked}"
    evid = [x for x in r["results"] if any("증상" in m for m in x["matched"])]
    assert len(evid) >= 0.9 * len(ids), "상위 결과 대부분이 발열 근거(결함 주석·동의어)를 가져야 한다"


def test_primary_product_outranks_mentions():
    """'폴드7' 을 찾는데 폴드8 분해 기사(폴드7 은 언급만)가 위로 오던 것을 막는다."""
    r = _run("폴드7 힌지 먼지", limit=10)
    primary = sum(1 for x in r["results"] if x["product_code"] == "GZF7")
    assert primary >= 7, [x["product_code"] for x in r["results"]]


def test_comparison_needs_both_products():
    r = _run("S25 vs 아이폰 16 배터리", limit=30)
    assert r["interpretation"]["compare_all"]
    ids = [x["id"] for x in r["results"]]
    both = {row[0] for row in _sql_rows(
        "SELECT l.voc_id FROM voc_product_links l JOIN products p ON p.id = l.product_id "
        "WHERE p.code IN ('GS25', 'AP16') AND l.voc_id = ANY(CAST(:ids AS bigint[])) "
        "GROUP BY l.voc_id HAVING count(DISTINCT p.code) = 2", {"ids": ids})}
    assert set(ids) <= both, "비교 의도인데 한쪽만 언급한 글이 섞였다"


def test_channel_filter():
    r = _run("레딧에서 픽셀 10 발열", limit=20)
    assert r["results"] and all("Reddit" in (x["platform_name"] or "") for x in r["results"])


def test_no_concept_falls_back_to_keyword():
    r = _run("카카오톡 알림", limit=5)
    assert r["strategy"] == "keyword"


@pytest.mark.parametrize("q,limit_s", [
    ("S25 울트라 발열", 5), ("발열", 6), ("태블릿 화면 깜빡임", 8), ("삼성 발열", 10),
])
def test_response_time(q, limit_s):
    """대화형 도구라 수 초 안이어야 한다. 실측 0.4~4초 — 한계는 부하를 감안해 넉넉히 둔다."""
    t0 = time.monotonic()
    r = _run(q, limit=30)
    dt = time.monotonic() - t0
    assert r["strategy"] == "semantic"
    assert dt < limit_s, f"{q!r} {dt:.1f}초 (한계 {limit_s}초)"


def test_fast_plan_drops_text_branches():
    """시간 초과 재시도는 본문 동의어 없이 주석만 써야 한다."""
    import ontology as O
    import tools.semantic as S
    it = O.interpret("발열")
    sc = {"pids": [], "groups": [], "labels": [], "compare_all": False, "text_groups": [],
          "platform_ids": [], "notes": [], "scoped": False, "broad": False, "scope_n": 0}
    full, _ = S._build_sql(it, sc, limit=5, days=None, order="relevance")
    fast, _ = S._build_sql(it, sc, limit=5, days=None, order="relevance", fast=True)
    branch = "SELECT v.id, 'TS'"          # 점수식에도 'TS' 가 있으니 후보 경로 자체를 본다
    assert branch in full and branch not in fast and "FROM voc_defects d " in fast
