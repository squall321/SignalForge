# 빈 결과가 '없음' 인지 '측정 안 함 · 집계 준비 중 · 태깅 비었음' 인지 가르는 응답(SG-02·04·05·06)을 가짜 DB 로 고정한다
import asyncio
import contextlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.exc import DBAPIError  # noqa: E402


class _Res:
    def __init__(self, rows=None, one=None, scalar=None):
        self._rows, self._one, self._scalar = rows or [], one, scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def one(self):
        return self._one

    def scalar(self):
        return self._scalar

    scalar_one = scalar


class FakeDB:
    """SQL 조각 → 결과. 순서대로 처음 맞는 것을 준다(더 구체적인 조각을 앞에)."""

    def __init__(self, script, error=None):
        self.script, self.error, self.seen = script, error, []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.seen.append(sql)
        if self.error is not None:
            raise self.error
        for key, res in self.script:
            if key in sql:
                return res
        raise AssertionError(f"예상 못 한 SQL: {sql[:120]}")


def _session(db):
    @contextlib.asynccontextmanager
    async def get_db_session():
        yield db
    return get_db_session


def test_defect_tools_say_not_run_when_voc_defects_is_empty():
    from tools import defects as D
    D._PIPE["has"] = None
    out = asyncio.run(D.pipeline_status(FakeDB([("EXISTS", _Res(one=(False, True)))])))
    assert out["pipeline_status"] == "not_run" and "voc_defects" in out["pipeline_note"]
    D._PIPE["has"] = None
    out = asyncio.run(D.pipeline_status(FakeDB([("EXISTS", _Res(one=(True, False)))])))
    assert out["pipeline_status"] == "not_run" and "voc_product_links" in out["pipeline_note"]
    D._PIPE["has"] = None
    assert asyncio.run(D.pipeline_status(FakeDB([("EXISTS", _Res(one=(True, True)))]))) == {"pipeline_status": "ok"}
    D._PIPE["has"] = None


def test_unpopulated_matview_becomes_aggregating(monkeypatch):
    from tools import coverage as C
    err = DBAPIError("SELECT ...", {}, Exception('materialized view "kg_edges_daily" has not been populated'))
    monkeypatch.setattr(C, "get_db_session", _session(FakeDB([], error=err)))
    out = asyncio.run(C.get_kg_relations_tool(top_n=3))
    assert out[0]["status"] == "aggregating" and out[0]["view"] == "kg_edges_daily"
    assert "집계 준비 중" in out[0]["note"] and "has not been populated" not in out[0]["note"]
    tl = asyncio.run(C.get_product_timeline_tool("GZF8"))
    assert tl["status"] == "aggregating" and tl["timeline"] == []
    other = DBAPIError("SELECT ...", {}, Exception("relation does not exist"))
    monkeypatch.setattr(C, "get_db_session", _session(FakeDB([], error=other)))
    try:
        asyncio.run(C.get_kg_relations_tool())
        raise AssertionError("다른 DB 오류는 숨기지 않는다")
    except DBAPIError:
        pass


def test_product_zero_is_split_into_unknown_untagged_and_period():
    from tools import query as Q
    db = FakeDB([("FROM products WHERE code", _Res(rows=[{"id": 3, "name_en": "Galaxy Z Fold8",
                                                          "name_ko": "갤럭시 Z 폴드8"}])),
                 ("max(collected_at)", _Res(one=(0, None))),
                 ("FROM voc_active v WHERE", _Res(scalar=120))])
    d = asyncio.run(Q.product_zero_diagnosis(db, "gzf8", 7))
    assert d["status"] == "no_tagged_records" and d["keyword_hits"] == 120 and "no_tagged_records" in d["note"]
    d = asyncio.run(Q.product_zero_diagnosis(FakeDB([("FROM products WHERE code", _Res(rows=[]))]), "NOPE"))
    assert d["status"] == "unknown_product"
    import datetime
    db = FakeDB([("FROM products WHERE code", _Res(rows=[{"id": 3, "name_en": "X", "name_ko": None}])),
                 ("max(collected_at)", _Res(one=(42, datetime.datetime(2026, 10, 1)))),
                 ("FROM voc_active v WHERE", _Res(scalar=0))])
    d = asyncio.run(Q.product_zero_diagnosis(db, "X", 7))
    assert d["status"] == "none_in_period" and "42" in d["note"]


def test_resolve_uses_the_tagging_alias_dictionary_and_flags_untagged(monkeypatch):
    """카탈로그 이름에 없는 말('폴드8')도 태깅과 같은 별칭 사전으로 잇고, 태깅 0 · 키워드 >0 은
    'no_tagged_records' 다(cae00 2026-10-27 — 한국어 마케팅명 substring 1,202건인데 resolved null)."""
    from tools import query as Q
    if not Q._matcher_codes("폴드8"):
        import pytest
        pytest.skip("제품 매처(/crawler)가 없는 환경")
    db = FakeDB([("SET LOCAL", _Res()),                      # 키워드 건수의 시간 상한(SG-19)
                 ("false AS exact", _Res(rows=[{"code": "GZF8", "name_en": "Galaxy Z Fold8", "name_ko": "갤럭시 Z 폴드8",
                                                 "brand": "samsung", "category": "phone", "tagged_count": 0,
                                                 "exact": False}])),
                 ("AS exact", _Res(rows=[])),
                 ("FROM voc_records v", _Res(scalar=1202))])
    monkeypatch.setattr(Q, "get_db_session", _session(db))
    out = asyncio.run(Q.resolve_product_keyword_tool("폴드8"))
    assert out["resolved_code"] == "GZF8" and out["resolved_via"] == "alias"
    assert out["status"] == "no_tagged_records" and "1,202" in out["note"]
