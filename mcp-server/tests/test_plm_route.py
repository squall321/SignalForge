# PLM 프로젝트 · 과제를 코어 거울로 풀고 못 풀면 넓히지 않는지(SFO-1 · 2), 응답의 푼 길 칸을 가짜 거울 · DB 로 고정한다
"""왜 이 시험인가 (2026-11-25, cae00 11-24 실측).

프로젝트 P1 + 증상을 search_voc 에 주면 범위 없이 전체 1만 건이 나왔고 notes 는 비었다. resolve_product_keyword(P1) 는
no_catalog_match 였다. 제품에 이어지지 않은 과제 + 증상은 말없이 낱말 · 의미 검색으로 물러나고 같은 줄이 두 번 나왔다.
입력은 요청서의 자리표시(프로젝트 P1 · 과제코드 C1 …) 꼴로 지어낸 거울이다 — 실제 이름은 쓰지 않는다.
"""
import asyncio
import contextlib
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in ("/shared", str(ROOT.parent / "shared"), "/crawler", str(ROOT.parent / "crawler")):
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(ROOT))
pytest.importorskip("ontology", reason="crawler 사전을 import 할 수 없음 — /crawler 바인드 필요")

import ontology as O  # noqa: E402
from base import plm_mirror as P  # noqa: E402


def _mirror():
    data = {
        "plm_project": [
            {"key": "PRJ-P1", "label": "피원 프로젝트", "previous_keys": ["PRJ-P0"],
             "properties": {"aliases": ["Galaxy S25 Ultra"]}},
            {"key": "PRJ-P9", "label": "피나인", "properties": {}}],
        "plm_task": [
            {"key": "과제A", "label": "과제A", "properties": {"task_code": "TK-C1", "project": "PRJ-P1"}},
            {"key": "과제B", "label": "과제B", "properties": {"task_code": "TK-C2", "project": "PRJ-P0"}},
            {"key": "과제Z", "label": "과제Z", "properties": {"task_code": "TK-C9", "project": "PRJ-P9"}}],
        "plm_model": [
            {"key": "M-A", "properties": {"task": "과제A", "model_code": "SM-S938B"}},
            {"key": "M-B", "properties": {"task": "과제B", "model_code": "SM-F976B"}},
            {"key": "M-Z", "properties": {"task": "과제Z", "model_code": "SM-X100A"}}],
    }
    return P.PlmIndex.from_rows(data)


def _product(pid, code, name):
    return {"id": pid, "code": code, "name_en": name, "name_ko": None, "brand": "samsung", "category": "phone",
            "series_code": None, "is_active": True}


def _catalog(*codes):
    prods = [_product(i + 1, c, f"Product {c}") for i, c in enumerate(codes)]
    return {"at": 0.0, "products": prods, "by_code": {p["code"]: p for p in prods}, "platforms": [], "lexicon": {}}


class _NoSQL:
    async def execute(self, stmt, params=None):
        raise AssertionError(f"범위를 못 걸면 SQL 을 돌리지 않는다: {str(stmt)[:80]}")


def _session(db):
    @contextlib.asynccontextmanager
    async def get_db_session():
        yield db
    return get_db_session


@pytest.fixture
def S(monkeypatch):
    from tools import semantic as S
    monkeypatch.setattr(S.P, "mirror", lambda *a, **k: _mirror())
    monkeypatch.setattr(S, "get_db_session", _session(_NoSQL()))
    monkeypatch.setattr(S.V, "store", lambda: None)

    async def no_llm(q):
        return []
    monkeypatch.setattr(S, "infer_symptoms", no_llm)
    saved = dict(S._catalog)
    yield S
    S._catalog.clear()
    S._catalog.update(saved)


def _use_catalog(S, *codes):
    S._catalog.update(_catalog(*codes), at=time.monotonic())


def _stop(S, q, codes=("GS25U", "GZF8U"), strict=False, product_code=None):
    it = O.interpret(q, plm=_mirror())
    cat = _catalog(*codes)
    return S._scope_stop(it, S._resolve(it, cat, product_code), cat, product_code, strict)


def test_unresolved_project_returns_zero_with_reason_and_runs_no_sql(S):
    _use_catalog(S, "GS25U", "GZF8U")
    r = asyncio.run(S.semantic_search_tool("PRJ-P9 발열"))
    assert r["total"] == 0 and r["results"] == [] and r["reason"] == "in_core_no_local_data", r
    assert r["core_key"] == {"type_slug": "plm_project", "key": "PRJ-P9"} and r["matched_by"] == "project"
    notes = r["interpretation"]["notes"]
    assert sum("넓히지 않고 0건" in n for n in notes) == 1 and any("PRJ-P9(피나인)" in n for n in notes), notes


def test_project_whose_products_are_not_in_this_catalog_is_not_widened(S):
    """거울로는 풀었지만 이 서버 카탈로그에 그 제품이 없다(cae00 seed 가 일부만일 때) — 범위 없는 검색이 되지 않게."""
    _use_catalog(S, "GS24")
    r = asyncio.run(S.semantic_search_tool("PRJ-P1 발열"))
    assert r["total"] == 0 and r["reason"] == "in_core_no_local_data", r
    assert r["core_key"]["key"] == "PRJ-P1" and r["core_matches"][0]["products"] == ["GS25U", "GZF8U"]


def test_resolved_project_scopes_the_search(S):
    stop, soft = _stop(S, "PRJ-P1 발열")
    assert stop is None and soft == []
    it = O.interpret("PRJ-P1 발열", plm=_mirror())
    sc = S._resolve(it, _catalog("GS25U", "GZF8U"), None)
    assert sc["pids"] == [1, 2] and sc["scoped"], sc


def test_unresolved_task_falls_back_with_one_line_unless_strict(S):
    stop, soft = _stop(S, "TK-C9 과제Z 발열")
    assert stop is None and [u["core_key"]["key"] for u in soft] == ["과제Z"]
    line = S._fallback_note(soft, scoped=False)
    assert line.count("과제Z") == 1 and "낱말 검색으로 바꿨다" in line and "strict=True" in line
    stop, _ = _stop(S, "TK-C9 과제Z 발열", strict=True)
    assert stop and stop[0] == "in_core_no_local_data" and "(strict)" in stop[1]


def test_keyword_fallback_keeps_the_plm_lines(S, monkeypatch):
    """개념 · 벡터가 없어 키워드 검색으로 갈 때 해석 줄을 갈아 끼우지 않는다 — 예전에는 PLM 줄이 사라졌다."""
    from tools import query as Q
    _use_catalog(S, "GS25U", "GZF8U")

    async def fake_detail(q, product_code, limit, days, order, match):
        return {"results": [], "match_mode": "phrase", "notes": [], "total": 0, "truncated": False}
    monkeypatch.setattr(Q, "search_voc_detail", fake_detail)
    r = asyncio.run(S.semantic_search_tool("TK-C9"))
    notes = r["interpretation"]["notes"]
    assert r["strategy"] == "keyword" and "reason" not in r, r
    assert sum("제품에 이어지지 않는다" in n for n in notes) == 1 and sum("낱말 검색으로 바꿨다" in n for n in notes) == 1, notes
    assert r["core_unresolved"][0]["core_key"] == {"type_slug": "plm_task", "key": "과제Z"}
    r = asyncio.run(S.semantic_search_tool("TK-C9", strict=True))
    assert r["total"] == 0 and r["reason"] == "in_core_no_local_data"


def test_unresolved_project_beside_a_product_proceeds_unless_strict(S):
    stop, soft = _stop(S, "PRJ-P9 Galaxy S25 Ultra 발열")
    assert stop is None and [u["core_key"]["key"] for u in soft] == ["PRJ-P9"]
    assert "범위는 다른 말로 건 것만" in S._fallback_note(soft, scoped=True)
    stop, _ = _stop(S, "PRJ-P9 Galaxy S25 Ultra 발열", strict=True)
    assert stop and stop[0] == "in_core_no_local_data"


def test_strict_stops_for_a_product_missing_from_the_catalog(S):
    assert _stop(S, "Galaxy S25 Ultra 발열", codes=("GS24",)) == (None, []), "기본은 예전처럼 '범위에서 뺐다' 줄"
    stop, _ = _stop(S, "Galaxy S25 Ultra 발열", codes=("GS24",), strict=True)
    assert stop and stop[0] == "product_not_in_catalog" and "GS25U" in stop[1]
    assert _stop(S, "Galaxy S25 Ultra 발열", strict=True) == (None, []), "카탈로그에 있으면 strict 도 그대로"


# ── resolve_product_keyword (SFO-1 · 3) ─────────────────────────────────
class _Res:
    def __init__(self, rows=None, scalar=None):
        self._rows, self._scalar = rows or [], scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class FakeDB:
    """SQL 조각 → 결과. 순서대로 처음 맞는 것(더 구체적인 조각을 앞에)."""

    def __init__(self, script):
        self.script = script

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        for key, res in self.script:
            if key in sql:
                return res(params) if callable(res) else res
        raise AssertionError(f"예상 못 한 SQL: {sql[:120]}")

    async def rollback(self):
        pass


def _by_codes(params):
    return _Res(rows=[{"code": c, "name_en": f"Product {c}", "name_ko": None, "brand": "samsung", "category": "phone",
                       "tagged_count": 10, "exact": False} for c in params["codes"] if c in ("GS25U", "GZF8U")])


@pytest.fixture
def Q(monkeypatch):
    from tools import query as Q
    monkeypatch.setattr(P, "mirror", lambda *a, **k: _mirror())
    db = FakeDB([("SET LOCAL", _Res()), ("false AS exact", _by_codes), ("AS exact", _Res(rows=[])),
                 ("FROM voc_records v", _Res(scalar=3))])
    monkeypatch.setattr(Q, "get_db_session", _session(db))
    return Q


def test_resolve_project_key_through_plm(Q):
    out = asyncio.run(Q.resolve_product_keyword_tool("PRJ-P1"))
    assert out["status"] == "resolved" and out["resolved_via"] == "plm" and out["resolved_code"] == "GS25U", out
    assert out["resolved_codes"] == ["GS25U", "GZF8U"]
    assert out["core_key"] == {"type_slug": "plm_project", "key": "PRJ-P1"} and out["matched_by"] == "project"
    assert out["via"] == "project → task → model → model_code" and "PLM 으로 풀었다" in out["note"]


def test_resolve_task_code_through_plm(Q):
    out = asyncio.run(Q.resolve_product_keyword_tool("TK-C1"))
    assert out["resolved_code"] == "GS25U" and out["resolved_via"] == "plm", out
    assert out["core_key"] == {"type_slug": "plm_task", "key": "과제A"} and out["matched_by"] == "task_code"


def test_resolve_unlinked_project_gives_reason_not_a_guess(Q):
    out = asyncio.run(Q.resolve_product_keyword_tool("PRJ-P9"))
    assert out["status"] == "no_catalog_match" and out["resolved_code"] is None, out
    assert out["reason"] == "in_core_no_local_data" and out["core_key"]["key"] == "PRJ-P9"
    assert out["core_unresolved"][0]["matched_by"] == "project" and "잇지 못했다" in out["note"]
