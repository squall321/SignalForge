# 한국어 원문 경로를 한 번에·시간 상한 안에 훑고 본 SQL 은 그 행 id 를 받는지, resolve 건수가 상한을 지키는지(SG-19) 고정한다
import asyncio
import pathlib
import sys

import pytest
from sqlalchemy.exc import DBAPIError

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in ("/shared", str(ROOT.parent / "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(ROOT))
pytest.importorskip("ontology", reason="crawler 사전을 import 할 수 없음 — /crawler 바인드 필요")

SC = {"pids": [], "groups": [], "labels": [], "compare_all": False, "text_groups": [],
      "platform_ids": [], "notes": [], "scoped": False, "broad": False, "scope_n": 0}
TIMEOUT = DBAPIError("SELECT …", {}, Exception("canceling statement due to statement timeout"))


class _Res:
    def __init__(self, rows=None, scalar=None):
        self._rows, self._scalar = rows or [], scalar

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class SeqDB:
    """문장마다 차례로 결과(또는 예외)를 준다 — SET LOCAL 은 늘 빈 결과."""

    def __init__(self, *answers):
        self.answers, self.seen, self.rollbacks = list(answers), [], 0

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.seen.append(sql)
        if sql.startswith("SET LOCAL"):
            return _Res()
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    async def rollback(self):
        self.rollbacks += 1


def _candidates(sql: str) -> str:
    return sql.split("ranked AS")[0]                # 후보 경로(hits·cand)만 — 순위 가산은 뺀다


def test_ko_paths_take_prescanned_ids_and_fts_stays_on_the_index():
    import ontology as O
    import tools.semantic as S
    it = O.interpret("폴드 힌지 삐걱")
    groups = S._ko_groups(it, None)
    assert groups["TC"][0] == "any" and any("힌지" in pat for pat in groups["TC"][1])
    sql, params = S._build_sql(it, SC, limit=5, days=None, order="relevance", ko_ids={"TC": [11, 12]})
    assert "unnest(CAST(:ko_TC AS bigint[]))" in sql and params["ko_TC"] == [11, 12]
    assert "content_original ILIKE" not in _candidates(sql), "원문 훑기는 미리 훑기로 옮겼다"
    assert "@@" in _candidates(sql), "번역문 FTS 경로는 색인으로 그대로"
    skipped, _ = S._build_sql(it, SC, limit=5, days=None, order="relevance", ko_ids={})
    assert "ko_TC" not in skipped and "content_original ILIKE" not in _candidates(skipped)
    legacy, _ = S._build_sql(it, SC, limit=5, days=None, order="relevance")
    assert "content_original ILIKE ANY" in _candidates(legacy), "ko_ids 를 안 주면 예전처럼 경로에서 훑는다"


def test_prescan_is_one_scan_split_by_path_cached_and_gives_up_on_budget():
    import tools.semantic as S
    S._ko_cache.clear()
    groups = {"TS": ("any", ["%스웰링%"]), "TC": ("any", ["%배터리%"]), "K": ("all", ["%삼성페이%", "%결제%"])}
    db = SeqDB(_Res(rows=[(1, True, False, False), (2, False, True, False), (3, True, True, True)]))
    out, skipped = asyncio.run(S._korean_prescan(db, groups, 4))
    assert out == {"TS": [1, 3], "TC": [2, 3], "K": [3]} and skipped == []
    scans = [q for q in db.seen if "FROM voc_active" in q]
    assert len(scans) == 1 and "language_detected = 'ko'" in scans[0], "경로 셋을 한 번에 훑는다"
    assert any(q.startswith("SET LOCAL statement_timeout = '4000ms'") for q in db.seen)
    # 같은 묶음은 캐시에서 — 표기만 다른 질의('발열'·'과열')가 다시 훑지 않고 같은 결과를 낸다
    db = SeqDB()
    assert asyncio.run(S._korean_prescan(db, {"X": ("any", ["%스웰링%"])}, 4)) == ({"X": [1, 3]}, [])
    assert not db.seen, "캐시에 있으면 DB 에 묻지 않는다"
    # 상한을 넘기면 못 훑은 꼬리표만 빠지고, 캐시에 있던 것은 그대로 쓴다
    db = SeqDB(TIMEOUT)
    out, skipped = asyncio.run(S._korean_prescan(db, {"TC": ("any", ["%배터리%"]), "TS": ("any", ["%부풀%"])}, 4))
    assert out == {"TC": [2, 3]} and skipped == ["TS"] and db.rollbacks == 1
    assert asyncio.run(S._korean_prescan(SeqDB(), {}, 4)) == ({}, []), "한국어 낱말이 없으면 훑지 않는다"
    S._ko_cache.clear()


def test_k_path_ands_prescanned_korean_tokens_with_english_fts():
    import ontology as O
    import tools.semantic as S
    it = O.interpret("삼성페이 결제 timeout")
    sql, params = S._build_sql(it, SC, limit=5, days=None, order="relevance",
                               kw_tokens=["삼성페이", "timeout"], ko_ids={"K": [7]})
    assert "v.id = ANY(CAST(:ko_K AS bigint[])) AND" in sql and params["ko_K"] == [7]
    none_k, _ = S._build_sql(it, SC, limit=5, days=None, order="relevance", kw_tokens=["삼성페이"], ko_ids={})
    assert "'K' AS src" not in none_k, "한국어 낱말을 못 훑었으면 그 경로는 뺀다(AND 라 반만 걸 수 없다)"


def test_resolve_keyword_count_keeps_its_budget():
    from tools import query as Q
    n, exact = asyncio.run(Q._keyword_hits(SeqDB(_Res(scalar=42)), "TRUE", {}))
    assert (n, exact) == (42, True)
    db = SeqDB(TIMEOUT, _Res(scalar=7))
    n, exact = asyncio.run(Q._keyword_hits(db, "TRUE", {}))
    assert (n, exact) == (7 * 100 // Q._SAMPLE_PCT, False) and db.rollbacks == 1
    assert any("TABLESAMPLE SYSTEM" in q for q in db.seen)
    assert asyncio.run(Q._keyword_hits(SeqDB(TIMEOUT, TIMEOUT), "TRUE", {})) == (None, False)


def test_hangul_keywords_do_not_read_translations():
    """번역문은 영어라 한글 키워드가 번역문에만 있는 글이 0건이었다(dev 실측) — 원문만 읽는다."""
    from keyword_match import hangul_like_patterns, keyword_clause, token_like_clause
    cl, params, mode = keyword_clause("폴드8")
    assert mode == "substring" and "content_translated" not in cl and params["kw_like"] == "%폴드8%"
    assert "content_translated" in keyword_clause("écran")[0], "한글이 아닌 비ASCII 는 예전대로 둘 다"
    assert "content_translated" not in token_like_clause("힌지", "r0")[0]
    assert "content_translated" in token_like_clause("hinge", "r0")[0]
    assert hangul_like_patterns(["힌지", "hinge", "힌지", "경 첩"]) == ["%힌지%", "%경 첩%"]
