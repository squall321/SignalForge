# 경쟁사 브랜드 서브레딧의 제품 링크 없는 글이 브랜드 · 대표 제품 검색 범위에 드는지(2026-11-27 사용자 결정) DB 없이 고정한다
"""왜 이 시험인가.

경쟁사 웨어러블 서브레딧 7곳 글의 60% 는 모델 이름이 없어('my watch') 제품 링크가 안 붙는다. 사용자는 데이터는 그대로 두고
검색의 브랜드 범위에만 넣기로 했다 — 브랜드와 기기를 함께 짚은 질의와 브랜드 대표 제품(OURA · WHP · MRB)으로 풀린 질의.
모델을 짚은 질의 · 다른 기기 질의 · 브랜드나 기기 하나만 짚은 질의('아이폰 배터리' 는 브랜드 apple 로만 풀린다)에는 넣지 않는다.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in ("/shared", str(ROOT.parent / "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(ROOT))
pytest.importorskip("ontology", reason="crawler 사전을 import 할 수 없음 — /crawler 바인드 필요")

import ontology as O  # noqa: E402


def test_brand_category_and_product_pick_their_subreddits():
    assert O.subreddit_scope({"apple"}, {"watch"}, ()) == ["AppleWatch"]
    assert O.subreddit_scope({"apple"}, {"buds"}, ()) == ["airpods"]
    assert O.subreddit_scope({"apple"}, (), ()) == [], "브랜드만으로는 넣지 않는다(아이폰도 브랜드 apple 로만 풀린다)"
    assert O.subreddit_scope({"garmin"}, {"watch"}, ()) == ["Garmin"]
    assert O.subreddit_scope((), (), ["OURA"]) == ["ouraring"] and O.subreddit_scope((), (), ["WHP", "MRB"]) == ["whoop", "RaybanMeta"]
    assert O.subreddit_scope((), {"watch"}, ()) == [], "브랜드 없는 기기 질의(스마트워치)에는 넣지 않는다"
    assert O.subreddit_scope({"apple"}, {"phone"}, ()) == [], "다른 기기"
    assert O.subreddit_scope({"samsung"}, (), ["GS25U"]) == []


def _cat(*codes):
    prods = [{"id": i + 1, "code": c, "name_en": c, "name_ko": None, "brand": b, "category": k, "series_code": None,
              "is_active": True} for i, (c, b, k) in enumerate(codes)]
    return {"at": 0.0, "products": prods, "by_code": {p["code"]: p for p in prods}, "lexicon": {},
            "platforms": [{"id": 1, "code": "reddit", "name": "Reddit"}, {"id": 85, "code": "reddit_rss", "name": "Reddit RSS"},
                          {"id": 9, "code": "clien", "name": "Clien"}]}


CAT = _cat(("AW10", "apple", "watch"), ("APP3", "apple", "buds"), ("OURA", "oura", "ring"), ("GS25U", "samsung", "phone"))


def test_resolve_and_sql_carry_the_subreddit_condition():
    from tools import semantic as S
    it = O.interpret("애플워치 배터리")
    sc = S._resolve(it, CAT, None)
    assert sc["broad"] and sc["sub_urls"] == ["%/r/applewatch/%"] and sc["sub_pids"] == [1, 85], sc
    assert any("r/AppleWatch" in lab for lab in sc["labels"])
    sc["scope_n"] = 0
    sql, params = S._build_sql(it, sc, limit=5, days=None, order="relevance")
    assert "f_subreddit" in sql and "OR z0.f_subreddit" in sql, "증상이 있는 넓은 범위 — 후보에 거는 조건"
    assert params["sub_urls"] == ["%/r/applewatch/%"] and params["sub_pids"] == [1, 85]

    it = O.interpret("오우라 링")                          # 대표 제품 코드, 개념 없음 — 범위 CTE 에
    sc = S._resolve(it, CAT, None)
    assert sc["pids"] == [3] and sc["sub_urls"] == ["%/r/ouraring/%"], sc
    sc["scope_n"] = 0
    sql, _ = S._build_sql(it, sc, limit=5, days=None, order="relevance")
    assert "0.2 AS prank FROM voc_active v WHERE v.product_id IS NULL AND v.platform_id" in sql


def test_model_and_other_device_queries_do_not_add_subreddits():
    from tools import semantic as S
    for q in ("S25 울트라 발열", "스마트워치 배터리", "애플 폰 배터리", "아이폰 배터리", "애플 불만"):
        sc = S._resolve(O.interpret(q), CAT, None)
        assert sc["sub_urls"] == [] and not any("서브레딧" in lab for lab in sc["labels"]), (q, sc["sub_urls"])
