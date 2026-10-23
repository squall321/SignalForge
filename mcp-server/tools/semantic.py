# 온톨로지 해석을 SQL 로 옮겨 VOC 를 뜻(개념)으로 찾는 시맨틱 검색
"""시맨틱 검색 — 질의를 개념으로 바꿔 말뭉치의 개념 주석과 맞춘다.

  질의 ──interpret──▶ 개념(제품·세대·브랜드·기기·부품·증상·주제·시장·채널)
       ──resolve───▶ 범위(제품 id) · 후보 경로 · 가산 항목
       ──SQL───────▶ 후보 = 결함 주석(정확) ∪ 동의어 본문 일치(재현) ∪ 주제 주석
                      점수 = 증상 주석 3 · 부품 주석 2 · 증상 동의어 1.5 · 주제 1 · 부품 동의어 0.75
                             + 가산(함의 부품 주석 1 · 관련 주제 0.5 · 대표 제품 0.5 · 감성 0.5 · 잔여어 0.75)

같은 뜻이면 같은 개념이 되고, 같은 개념이면 같은 SQL 이 된다 — 표기·언어가 달라도
결과가 같다(test_semantic_search 가 고정한다). 개념이 하나도 없으면 기존 키워드 검색이다.

성능 근거(2026-10-17 실측, 활성 99만 행): 결함 주석 9ms · 영어 동의어 tsquery 1.3초 ·
한국어 동의어 원문 부분일치 0.4초. 제품 범위가 있으면 그 안에서만 돈다.
"""
import asyncio
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from db import get_db_session

import pathlib as _pathlib  # noqa: E402
import sys as _sys  # noqa: E402
for _p in ("/shared", str(_pathlib.Path(__file__).resolve().parents[2] / "shared")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import ontology as O  # noqa: E402
from keyword_match import (  # noqa: E402
    all_tokens_clause, expansion_clauses, fts_groups_clause, like_any_clause, token_like_clause,
)
from tools import vector as V  # noqa: E402
from base import plm_mirror as P  # noqa: E402 — ontology 가 /crawler 경로를 잡은 뒤
from base import model_codes as _MC  # noqa: E402
from tools.llm_concepts import infer_symptoms  # noqa: E402

_CATALOG_TTL = 600
_BROAD_LINKS = 60000      # 이보다 크면 범위를 후보 필터로 (CTE 로 만들면 8초)
_DRIVE_LINKS = 15000      # 이하면 범위가 후보 경로를 몬다 (행마다 FTS 를 돌려도 0.5초대)
_TIMEOUT_S = 15           # 넘으면 주석만으로 재시도
_VEC_K = 300              # 벡터 후보 수 (범위 안에서, 또는 전역)
_VEC_W = 2.0              # 벡터 유사도 가중 — 결함 주석(3)보다 낮게: 은유·짧은 글에서 벡터가 틀렸다
_VEC_MIN_CHARS = 20       # 벡터로만 걸린 글은 이보다 길어야 — 짧은 댓글이 유사도를 독식했다
_catalog: Dict = {"at": 0.0, "products": [], "by_code": {}, "platforms": [], "lexicon": {}}


async def _load_catalog(db) -> Dict:
    if time.monotonic() - _catalog["at"] < _CATALOG_TTL and _catalog["products"]:
        return _catalog
    prods = [dict(r) for r in (await db.execute(text(
        "SELECT id, code, name_en, name_ko, brand, category, series_code, is_active FROM products"
    ))).mappings().all()]
    plats = [dict(r) for r in (await db.execute(text(
        "SELECT id, code, name FROM platforms"))).mappings().all()]
    codes = {p["code"] for p in plats}
    lex: Dict[str, List[str]] = {}
    for p in plats:
        for surface in {p["code"].replace("_rss", "").replace("_", " "),
                        (p["name"] or "").split("(")[0].strip().lower()}:
            if len(surface) >= 3:
                lex.setdefault(surface.lower(), []).append(p["code"])
    for k, v in O.CHANNEL_ALIASES.items():         # 실제로 있는 플랫폼만
        known = [c for c in v if c in codes]
        if known:
            lex[k] = known
    _catalog.update(at=time.monotonic(), products=prods,
                    by_code={p["code"].upper(): p for p in prods},
                    platforms=plats, lexicon=lex)
    return _catalog


def _resolve(it: "O.Interpretation", cat: Dict, product_code: Optional[str]) -> Dict:
    """해석 → 범위. groups 는 제품 묶음(비교 의도면 모두 함께 언급한 글)."""
    by_code, prods = cat["by_code"], cat["products"]
    notes, labels, groups, unknown = [], [], [], []

    def add_group(codes: List[str], label: str):
        ids = [by_code[c]["id"] for c in codes if c in by_code]
        if ids:
            groups.append(ids)
            labels.append(label)

    if product_code:
        pc = product_code.upper()
        if pc in by_code:
            add_group([pc], f"{pc}({by_code[pc]['name_en']})")
        else:
            unknown.append(pc)
    else:
        for code in it.products:
            if code not in by_code:
                unknown.append(code)
                continue
            if it.family:
                fk = O.family_key(code)
                fam = sorted(c for c in by_code if O.family_key(c) == fk)
                add_group(fam, f"{fk} 세대({', '.join(fam)})")
            else:
                add_group([code], f"{code}({by_code[code]['name_en']})")
        for label, codes in it.product_sets:          # 한 묶음 — 출시 전 세대 모델코드·제품 여럿에 걸린 과제
            missing = [c for c in codes if c not in by_code]
            unknown.extend(missing)
            add_group([c for c in codes if c in by_code], f"{label}({', '.join(codes)})")
        for key in it.series:
            rx = re.compile(O.SERIES[key]["code_regex"])
            add_group([c for c in by_code if rx.match(c)], O.label("series", key))
        for key in it.forms:
            rx = re.compile(O.FORMS[key]["name_regex"], re.IGNORECASE)
            add_group([p["code"].upper() for p in prods if rx.search(p["name_en"] or "")],
                      O.label("form", key))
    if unknown:
        notes.append(f"카탈로그에 없는 제품 코드라 범위에서 뺐다: {', '.join(unknown)}")
    if it.channels and not [p for p in cat["platforms"] if p["code"] in set(it.channels)]:
        notes.append(f"수집하지 않는 채널이라 채널 조건을 뺐다: {', '.join(it.channels)}")

    text_groups, broad = [], False
    if not groups and not product_code and (it.categories or it.brands):
        broad = True
        cats = {c for c, _ in it.categories}
        brands = set(it.brands) | {b for _, b in it.categories if b}
        codes = [p["code"].upper() for p in prods
                 if (not cats or p["category"] in cats) and (not brands or p["brand"] in brands)]
        lab = " · ".join([O.label("category", c) for c in sorted(cats)] + sorted(brands))
        add_group(codes, lab or "브랜드·기기")
        # 제품 태그 없는 글도 넣는다 — '삼성 폰이 뜨겁다' 처럼 모델을 안 쓴 글
        if brands:
            text_groups.append([t for b in sorted(brands) for t in O.BRANDS[b][1]])
        if cats:
            text_groups.append([t for c in sorted(cats) for t in O.CATEGORY_TEXT.get(c, [])])

    compare_all = it.compare_all and len(groups) >= 2 and not product_code
    pids = sorted({i for g in groups for i in g})
    plat_ids = [p["id"] for p in cat["platforms"] if p["code"] in set(it.channels)]
    return {"pids": pids, "groups": groups, "labels": labels, "compare_all": compare_all,
            "text_groups": text_groups, "platform_ids": plat_ids, "notes": notes,
            "scoped": bool(pids) or bool(text_groups), "broad": broad}


_ROLE_RANK = "CASE l.role WHEN 'primary' THEN 2 WHEN 'compared' THEN 1 ELSE 0.5 END"


def _build_sql(it: "O.Interpretation", sc: Dict, *, limit: int, days: Optional[int], order: str,
               fast: bool = False, vec: Optional[tuple] = None,
               kw_tokens: Optional[List[str]] = None):
    """해석·범위 → (SQL, params).

    범위는 세 방식으로 건다.
      · 좁은 범위(제품·세대·시리즈·폼팩터)  scope CTE 를 만들고 후보 경로가 그 안에서만 돈다
      · 넓은 범위(브랜드·기기 종류) + 개념   후보를 먼저 모으고 범위는 후보에 필터로 건다 —
        삼성 제품 전체를 CTE 로 만들면 수십만 행이라 8초가 걸렸다(실측)
      · 개념 없이 범위만                     scope CTE 자체가 후보다
    제품 역할 가중 — 대표(primary) 2 · 비교(compared) 1 · 언급 0.5 · 태그 없이 브랜드 이름만 0.25.
    대표가 아닌 언급글이 결함 주석 덕에 위로 올라오던 것('폴드7' 검색에 폴드8 분해 기사)을 막는다.
    fast=True 는 시간 초과 뒤의 재시도용 — 본문 동의어·잔여어를 빼고 주석만으로 찾는다.
    """
    params: Dict = {"limit": limit}
    ctes, hits = [], []
    has_soft = bool(it.symptoms or it.components or it.topics)
    if sc["pids"]:
        params["pids"] = sc["pids"]
    tg = fts_groups_clause(sc["text_groups"], prefix="sg") if sc["text_groups"] else None
    if tg:
        params.update(tg[1])

    # ── 범위 ──
    filter_scope = sc["broad"] and has_soft and not sc["compare_all"]
    if sc["compare_all"]:
        params.update(g_pids=[i for g in sc["groups"] for i in g],
                      g_idx=[gi for gi, g in enumerate(sc["groups"]) for _ in g],
                      g_n=len(sc["groups"]))
        ctes.append(f"""scope AS MATERIALIZED (
            SELECT l.voc_id AS id, max({_ROLE_RANK}) AS prank FROM voc_product_links l
            JOIN (SELECT unnest(CAST(:g_pids AS int[])) AS pid,
                         unnest(CAST(:g_idx AS int[])) AS gi) g ON g.pid = l.product_id
            GROUP BY l.voc_id HAVING count(DISTINCT g.gi) = :g_n)""")
    elif sc["scoped"] and not filter_scope:
        parts = []
        if sc["pids"]:
            parts.append(f"SELECT l.voc_id AS id, {_ROLE_RANK} AS prank FROM voc_product_links l "
                         "WHERE l.product_id = ANY(CAST(:pids AS int[]))")
        if tg:
            parts.append(f"SELECT v.id, 0.25 AS prank FROM voc_active v WHERE v.product_id IS NULL AND {tg[0]}")
        ctes.append("scope AS MATERIALIZED (SELECT id, max(prank) AS prank FROM ("
                    + " UNION ALL ".join(parts) + ") u GROUP BY id)")
    has_scope = bool(ctes)
    # 작은 범위만 후보 경로를 몬다. 큰 범위에서 몰면 행마다 to_tsvector 라 느리다 — 그때는
    # 경로를 인덱스로 먼저 돌리고, 범위는 순위 단계의 조인(s.id IS NOT NULL)으로 건다.
    drive = has_scope and (sc["compare_all"] or sc.get("scope_n", 0) <= _DRIVE_LINKS
                           or not sc["pids"])
    js = lambda col: f" JOIN scope s ON s.id = {col}" if drive else ""  # noqa: E731

    # ── 후보 경로 ──
    # LLM 이 추론한 증상(role=inferred)은 따로 걸어 사전 증상의 절반으로 친다(SI·TSI). 같은 무게면
    # '삼성페이 결제가 자꾸 실패해요' 가 '작동 안 함' 글 1.9만 건 아무거나를 위로 올리고, 질의의
    # 주어(남는 말 '삼성페이'·벡터)는 묻혔다. 추론은 틀릴 수도 있다('하루를 못 버텨요' → 발열).
    inferred = {c.key for c in it.concepts if c.type == "symptom" and c.role == "inferred"}
    for tag, syms in (("S", [x for x in it.symptoms if x not in inferred]),
                      ("SI", [x for x in it.symptoms if x in inferred])):
        if not syms:
            continue
        params[f"syms_{tag}"] = syms
        hits.append(f"SELECT d.voc_id AS id, '{tag}' AS src FROM voc_defects d{js('d.voc_id')} "
                    f"WHERE d.symptom = ANY(CAST(:syms_{tag} AS varchar[]))")
        terms = [] if fast else [t for x in syms for t in O.symptom_terms(x)]
        for clause in expansion_clauses(terms, prefix=f"x{tag.lower()}").values():
            if clause:
                hits.append(f"SELECT v.id, 'T{tag}' AS src FROM voc_active v{js('v.id')} WHERE {clause[0]}")
                params.update(clause[1])
    if it.components:
        params["comps"] = it.components
        hits.append(f"SELECT d.voc_id AS id, 'C' AS src FROM voc_defects d{js('d.voc_id')} "
                    "WHERE d.component = ANY(CAST(:comps AS varchar[]))")
        terms = [] if fast else [t for c in it.components for t in O.component_terms(c)]
        for clause in expansion_clauses(terms, prefix="xc").values():
            if clause:
                hits.append(f"SELECT v.id, 'TC' AS src FROM voc_active v{js('v.id')} WHERE {clause[0]}")
                params.update(clause[1])
    if it.topics:
        params["topics"] = it.topics
        hits.append(f"SELECT v.id, 'T' AS src FROM voc_active v{js('v.id')} "
                    "WHERE v.categories && CAST(:topics AS varchar[])")
    has_vec = bool(vec and len(vec[0])) and not fast
    if has_vec:
        # 벡터 후보 — 순위·유사도는 파이썬(numpy)에서 계산해 넘긴다. rel 은 이 질의 안의 상대값(0~1)
        params["vids"] = [int(i) for i in vec[0]]
        params["vrel"] = [float(x) for x in vec[1]]
        ctes.append("vec AS (SELECT * FROM unnest(CAST(:vids AS bigint[]), "
                    "CAST(:vrel AS float8[])) AS t(id, rel))")
        hits.append("SELECT id, 'V' AS src FROM vec")   # 첫 가지가 열 이름을 정한다 — 전부 AS src
    if kw_tokens and not fast:
        cl = all_tokens_clause(kw_tokens, prefix="kw")
        if cl:
            hits.append(f"SELECT v.id, 'K' AS src FROM voc_active v{js('v.id')} WHERE {cl[0]}")
            params.update(cl[1])

    # ── 필터 ── (후보를 다 모은 뒤 v 에 건다. 필터만 있는 질의는 후보 단계에서 건다)
    where = ["(v.published_at IS NULL OR v.published_at <= now())"]
    if days:
        params["days"] = int(days)
        where.append("v.published_at >= now() - make_interval(days => :days)")
    if it.year:
        params["y0"] = datetime(it.year, 1, 1, tzinfo=timezone.utc)
        params["y1"] = datetime(it.year + 1, 1, 1, tzinfo=timezone.utc)
        where.append("v.published_at >= :y0 AND v.published_at < :y1")
    if it.markets:
        params["countries"] = it.markets
        params["langs"] = it.languages or ["__none__"]
        where.append("(v.country_code = ANY(CAST(:countries AS varchar[])) "
                     "OR v.language_detected = ANY(CAST(:langs AS varchar[])))")
    if sc["platform_ids"]:
        params["plats"] = sc["platform_ids"]
        where.append("v.platform_id = ANY(CAST(:plats AS int[]))")

    if hits:
        ctes.append("hits AS (" + "\n UNION ALL ".join(hits) + ")")
        ctes.append("cand AS (SELECT id, array_agg(DISTINCT src) AS srcs FROM hits GROUP BY id)")
    elif has_scope:
        ctes.append("cand AS (SELECT id, ARRAY['P'] AS srcs FROM scope)")
    else:                                   # 시장·채널만 있는 질의
        ctes.append("cand AS (SELECT v.id, ARRAY['F'] AS srcs FROM voc_active v "
                    f"WHERE {' AND '.join(where)})")

    # ── 제품 역할 ──
    if has_scope:
        prank_sql, scope_join = "s.prank", " JOIN scope s ON s.id = v.id"
    elif filter_scope:
        prank_sql = (f"(SELECT max({_ROLE_RANK}) FROM voc_product_links l "
                     "WHERE l.voc_id = v.id AND l.product_id = ANY(CAST(:pids AS int[])))")
        scope_join = ""
    else:
        prank_sql, scope_join = "NULL::numeric", ""

    # ── 가산 ── 조건은 안쪽에서 한 번만 계산하고(f_*), 점수는 바깥에서 합한다
    flags = [f"{prank_sql} AS prank"]
    score = ["(CASE WHEN 'S' = ANY(z.srcs) THEN 3 ELSE 0 END)",
             "(CASE WHEN 'SI' = ANY(z.srcs) THEN 1.5 ELSE 0 END)",
             "(CASE WHEN 'C' = ANY(z.srcs) THEN 2 ELSE 0 END)",
             "(CASE WHEN 'TS' = ANY(z.srcs) THEN 1.5 ELSE 0 END)",
             "(CASE WHEN 'TSI' = ANY(z.srcs) THEN 0.75 ELSE 0 END)",
             "(CASE WHEN 'T' = ANY(z.srcs) THEN 1 ELSE 0 END)",
             "(CASE WHEN 'TC' = ANY(z.srcs) THEN 0.75 ELSE 0 END)",
             "(CASE WHEN 'K' = ANY(z.srcs) THEN 2 ELSE 0 END)",
             "COALESCE(z.prank, 0)"]
    if has_vec:
        flags.append("COALESCE(vx.rel, 0) AS vrel")
        score.append(f"{_VEC_W} * z.vrel")

    def flag(name: str, cond: str, weight: float):
        flags.append(f"({cond}) AS {name}")
        score.append(f"(CASE WHEN z.{name} THEN {weight} ELSE 0 END)")

    if filter_scope:
        # 후보에 거는 조건이라 ILIKE 로 본다(행마다 to_tsvector 를 돌리면 10초대)
        conds = []
        for gi, terms in enumerate(sc["text_groups"]):
            cl, p = like_any_clause(terms, f"bt{gi}")
            conds.append(cl)
            params.update(p)
        flag("f_brandtext", "v.product_id IS NULL AND " + " AND ".join(conds) if conds else "FALSE", 0.25)
    if it.boost_components:
        params["bcomps"] = it.boost_components
        flag("f_bcomp", "EXISTS (SELECT 1 FROM voc_defects d2 WHERE d2.voc_id = v.id "
                        "AND d2.component = ANY(CAST(:bcomps AS varchar[])))", 1)
    if it.boost_topics:
        params["btopics"] = it.boost_topics
        flag("f_btopic", "v.categories && CAST(:btopics AS varchar[])", 0.5)
    if it.sentiment:
        params["sent"] = it.sentiment
        flag("f_sent", "v.sentiment_label = :sent", 0.5)
    for i, tok in enumerate([] if fast else it.residual):
        cl, p = token_like_clause(tok, f"r{i}")
        params.update(p)
        flag(f"f_r{i}", cl, 0.75)

    order_by = {
        "recent": "{t}.published_at DESC NULLS LAST",
        "engagement": "{t}.engagement_score DESC NULLS LAST",
    }.get(order, "{t}.score DESC, {t}.published_at DESC NULLS LAST")
    # 순위는 좁은 열(id·플래그·날짜)로 매기고 본문은 상위 N 건만 읽는다. 넓은 행째로 정렬하면
    # 후보 7천 건의 본문을 통째로 끌고 다녀 1.7초가 더 든다(실측 2.9초 → 이 구조).
    inner_where = list(where)
    vec_join = ""
    if has_vec:
        vec_join = " LEFT JOIN vec vx ON vx.id = v.id"
        params["vmin"] = _VEC_MIN_CHARS
        inner_where.append("NOT (c.srcs = ARRAY['V']::text[] AND length(v.content_original) < :vmin)")
    inner = (f"SELECT v.id, v.published_at, v.engagement_score, v.content_hash, "
             f"c.srcs{''.join(', ' + f for f in flags)} "
             f"FROM cand c JOIN voc_active v ON v.id = c.id{scope_join}{vec_join} "
             f"WHERE {' AND '.join(inner_where)}")
    if filter_scope:     # 넓은 범위 — 그 브랜드·기기에 링크됐거나, 태그 없이 이름이 나오는 글
        inner = (f"SELECT * FROM ({inner}) z0 WHERE z0.prank IS NOT NULL OR z0.f_brandtext")
    sql = f"""WITH {', '.join(ctes)},
ranked AS (
  SELECT z.*, ({' + '.join(score)}) AS score, count(*) OVER () AS total
  FROM ({inner}) z
  ORDER BY {order_by.format(t='z').replace('z.score', 'score')}
  LIMIT :limit
)
SELECT r.*, v.source_url, v.author_name, v.content_translated,
       LEFT(v.content_original, 300) AS content_original, v.sentiment_label, v.categories,
       pl.name AS platform_name, p.name_en AS product_name, p.code AS product_code
FROM ranked r
JOIN voc_records v ON v.id = r.id
LEFT JOIN products p ON p.id = v.product_id
LEFT JOIN platforms pl ON pl.id = v.platform_id
ORDER BY {order_by.format(t='r')}"""
    return sql, params


async def _scope_ids(db, sc: Dict):
    """제품 범위의 VOC id (오름차순) — 벡터 검색을 범위 안에서만 돌린다."""
    import numpy as np
    if sc["compare_all"]:
        sql = text("""SELECT l.voc_id FROM voc_product_links l
            JOIN (SELECT unnest(CAST(:g_pids AS int[])) AS pid,
                         unnest(CAST(:g_idx AS int[])) AS gi) g ON g.pid = l.product_id
            GROUP BY l.voc_id HAVING count(DISTINCT g.gi) = :g_n ORDER BY 1""")
        p = {"g_pids": [i for g in sc["groups"] for i in g],
             "g_idx": [gi for gi, g in enumerate(sc["groups"]) for _ in g], "g_n": len(sc["groups"])}
    else:
        sql = text("SELECT DISTINCT voc_id FROM voc_product_links "
                   "WHERE product_id = ANY(CAST(:p AS int[])) ORDER BY 1")
        p = {"p": sc["pids"]}
    return np.asarray((await db.execute(sql, p)).scalars().all(), dtype=np.int64)


_SRC_LABEL = {"S": "결함 주석(증상)", "C": "결함 주석(부품)", "TS": "본문(증상 동의어)", "K": "낱말 모두 일치",
              "SI": "결함 주석(추론 증상)", "TSI": "본문(추론 증상 동의어)",
              "TC": "본문(부품 동의어)", "T": "주제 주석", "P": "제품 범위", "F": "필터"}


_PRANK_LABEL = {2.0: "대표 제품", 1.0: "비교 대상 제품", 0.5: "언급된 제품", 0.25: "브랜드·기기 이름(태그 없음)"}


def _explain_row(r: Dict, it: "O.Interpretation") -> List[str]:
    out = [_SRC_LABEL.get(s, s) for s in sorted(r.get("srcs") or []) if s not in ("P", "F", "V")]
    if r.get("vrel"):
        out.append(f"의미 유사({float(r['vrel']):.2f})")
    if r.get("prank") is not None:
        out.append(_PRANK_LABEL.get(float(r["prank"]), "제품 범위"))
    elif r.get("f_brandtext"):
        out.append(_PRANK_LABEL[0.25])
    if r.get("f_bcomp"):
        out.append("결함 주석(관련 부품)")
    if r.get("f_btopic"):
        out.append("주제 주석(관련 주제)")
    if r.get("f_sent"):
        out.append(f"감성 {it.sentiment}")
    for i, tok in enumerate(it.residual):
        if r.get(f"f_r{i}"):
            out.append(f"낱말 '{tok}'")
    return out


def _interpretation_view(it: "O.Interpretation", sc: Dict, days: Optional[int]) -> Dict:
    concepts = []
    for c in it.concepts:
        lab = c.key
        if c.type in ("symptom", "component", "topic", "series", "form"):
            lab = O.label(c.type, c.key)
        elif c.type == "product":
            p = _catalog["by_code"].get(c.key)
            lab = p["name_en"] if p else f"{c.key}(카탈로그 없음)"
        concepts.append({"type": c.type, "key": c.key, "label": lab, "via": c.via, "role": c.role})
    return {
        "concepts": concepts,
        "scope": sc["labels"] + (["(태그 없는 글은 브랜드·기기 이름으로)"] if sc["text_groups"] else []),
        "compare_all": sc["compare_all"],
        "markets": it.markets, "languages": it.languages, "channels": it.channels,
        "days": days, "year": it.year, "sentiment_hint": it.sentiment, "residual": it.residual,
        "expansion": {f"symptom:{s}": O.symptom_terms(s) for s in it.symptoms}
                     | {f"component:{c}": O.component_terms(c) for c in it.components},
        "notes": it.notes + sc["notes"],
    }


def _dedup(rows: List[Dict], limit: int) -> List[Dict]:
    """같은 본문이 여러 id 로 들어온 글(같은 댓글 재수집)을 하나로 접는다."""
    seen, out = set(), []
    for r in rows:
        key = (r.get("content_hash") or " ".join((r.get("content_original") or "").split())[:200]
               or ("id", r.get("id")))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    return out


async def semantic_search_tool(query: str, product_code: Optional[str] = None, limit: int = 30,
                               days: Optional[int] = None, order: str = "relevance",
                               match: str = "auto") -> Dict:
    """질의 해석 + 결과.

    순서 — 사전 해석 → (증상이 없으면) LLM 개념 추론 → 질의 임베딩 → 범위 → SQL.
    개념도 벡터도 없으면 예전 키워드 검색(strategy='keyword')이다.
    """
    limit = max(1, min(int(limit or 30), 100))
    t0 = time.monotonic()
    async with get_db_session() as db:
        cat = await _load_catalog(db)
        it = O.interpret(query, channel_lexicon=cat["lexicon"], plm=P.mirror())
        llm = []
        if not it.symptoms and (it.residual or not it.has_concepts):
            llm = await infer_symptoms(query)
            if llm:
                O.add_symptoms(it, llm, via="LLM 추론")
                it.notes.append("사전에 없는 표현이라 LLM 이 증상을 추론했다: "
                                + ", ".join(O.label("symptom", k) for k in llm))
        # 벡터는 사전이 질의를 다 설명하지 못할 때만 쓴다. 사전 개념만으로 설명되는 질의
        # ('S25 울트라 발열')에 벡터를 섞으면 표기마다 임베딩이 달라 같은 뜻의 결과가 갈린다
        # (실측 251건 → 499건, 표기별 순위 상이). 개념이 없거나·LLM 이 추론했거나·남는 말이
        # 있을 때가 벡터가 이득인 경우다.
        explained = (not it.residual and not llm and any(
            c.type in ("symptom", "component", "topic") and c.role in ("match", "boost")
            for c in it.concepts))
        qvec = None
        if not explained and V.store() is not None:
            qvec = await V.embed_query(query)
        if not it.has_concepts and not product_code and qvec is None:
            from tools.query import search_voc_tool
            rows = await search_voc_tool(query, None, limit, days,
                                         "recent" if order == "relevance" else order, match)
            for r in rows:
                r.setdefault("score", None)
                r.setdefault("matched", ["키워드 일치"])
            return {"query": query, "strategy": "keyword",
                    "interpretation": {"concepts": [], "notes": [
                        "온톨로지 개념도 벡터 색인도 없어 키워드 검색으로 찾았다"], "residual": it.residual},
                    "total": len(rows), "elapsed_ms": int((time.monotonic() - t0) * 1000),
                    "results": rows}
        sc = _resolve(it, cat, product_code)
        sc["scope_n"] = 0
        if sc["pids"]:
            # 범위 크기를 먼저 잰다(인덱스만 읽어 수십 ms). CTE 에는 통계가 없어 플래너가 늘
            # 200행으로 어림하고, 실제 3만 행 범위에서도 행마다 to_tsvector 를 돌렸다(1.8초).
            sc["scope_n"] = n = (await db.execute(text(
                "SELECT count(*) FROM voc_product_links WHERE product_id = ANY(CAST(:p AS int[]))"),
                {"p": sc["pids"]})).scalar_one()
            if sc["broad"]:
                # 넓은 범위라도 링크가 적으면(삼성 워치 등) 범위 CTE 가 낫다. 그 위(삼성 전체
                # 수십만)는 후보에 필터로 건다.
                sc["broad"] = n > _BROAD_LINKS
        vec, vec_info = None, {"used": False}
        if qvec is not None:
            allowed = None
            if sc["pids"] and not sc["broad"] and sc["scope_n"] <= _BROAD_LINKS:
                allowed = await _scope_ids(db, sc)
            # 전역이면 색인 전체(100만 행·4GB)를 훑는다 — 이벤트 루프를 막지 않게 스레드에서
            ids, sims = await asyncio.to_thread(V.search, qvec, _VEC_K, allowed)
            if len(ids):
                top, floor = float(sims[0]), float(sims[-1])
                rel = [(float(x) - floor) / (top - floor + 1e-6) for x in sims]
                vec = (ids, rel)
                vec_info = {"used": True, "candidates": int(len(ids)), "in_scope": allowed is not None,
                            "top_sim": round(top, 3), "floor_sim": round(floor, 3)}
        eff_days = days or it.days
        kw = it.residual if not it.has_concepts else None
        fetch = min(limit * 2, 200)          # 중복 본문을 접고도 limit 를 채우려고 넉넉히
        sql, params = _build_sql(it, sc, limit=fetch, days=eff_days, order=order, vec=vec, kw_tokens=kw)
        await db.execute(text(f"SET LOCAL statement_timeout = '{_TIMEOUT_S}s'"))
        try:
            rows = [dict(r) for r in (await db.execute(text(sql), params)).mappings().all()]
        except DBAPIError as e:
            if "statement timeout" not in str(e):
                raise
            # 넓은 질의가 시간을 넘기면 실패 대신 주석(결함·주제·제품)만으로 다시 찾는다
            await db.rollback()
            sc["notes"].append(f"{_TIMEOUT_S}초 안에 끝나지 않아 본문 동의어 없이 주석만으로 찾았다")
            sql, params = _build_sql(it, sc, limit=fetch, days=eff_days, order=order, fast=True)
            await db.execute(text(f"SET LOCAL statement_timeout = '{_TIMEOUT_S}s'"))
            rows = [dict(r) for r in (await db.execute(text(sql), params)).mappings().all()]
    total = int(rows[0]["total"]) if rows else 0
    results = []
    for r in _dedup(rows, limit):
        r["matched"] = _explain_row(r, it)
        r["score"] = float(r["score"])
        for k in [k for k in r if k.startswith("f_") or k in (
                "srcs", "total", "prank", "engagement_score", "vrel", "content_hash")]:
            r.pop(k, None)
        results.append(r)
    view = _interpretation_view(it, sc, eff_days)
    view["vector"] = vec_info
    view["llm_symptoms"] = llm
    return {"query": query, "strategy": "semantic" if it.has_concepts else "vector",
            "interpretation": view, "total": total,
            "elapsed_ms": int((time.monotonic() - t0) * 1000), "results": results}


def _plm_status() -> Dict:
    """PLM 거울(SP 허브의 과제·개발모델) 상태 — 과제코드·과제명 검색이 켜져 있나."""
    idx = P.mirror()
    if idx is None:
        s = P.settings()
        return {"active": False, "why": "PLM 거울이 없다 — " + ("; ".join(s["problems"]) if s["problems"]
                                                              else "아직 한 번도 받지 않았다(beat plm-sync-6h)")}
    return {"active": True, "tasks": len(idx.tasks), "models": len(idx.models), "projects": len(idx.projects),
            "pulled_at": idx.meta.get("pulled_at"), "host": idx.meta.get("host")}


async def get_ontology_tool(concept: Optional[str] = None) -> Dict:
    """온톨로지 둘러보기. concept 없으면 전체 구조, 있으면 그 개념의 정의·관계·말뭉치 규모."""
    async with get_db_session() as db:
        cat = await _load_catalog(db)
        if not concept:
            d = O.describe()
            sym = (await db.execute(text(
                "SELECT symptom, count(DISTINCT voc_id) n FROM voc_defects GROUP BY 1"))).all()
            comp = (await db.execute(text(
                "SELECT component, count(DISTINCT voc_id) n FROM voc_defects GROUP BY 1"))).all()
            for k, n in sym:
                if k in d["symptoms"]:
                    d["symptoms"][k]["annotated_voc"] = int(n)
            for k, n in comp:
                if k in d["components"]:
                    d["components"][k]["annotated_voc"] = int(n)
            d["catalog"] = {"products": len(cat["products"]), "platforms": len(cat["platforms"])}
            d["model_codes"] = {"exact": len(_MC.EXACT), "family": len(_MC.FAMILY)}
            d["plm"] = _plm_status()
            return d
        key = concept.split(":", 1)[-1].strip()
        low, up = key.lower(), key.upper()
        if low in O.SYMPTOM_LABELS:
            n = (await db.execute(text(
                "SELECT count(DISTINCT voc_id) FROM voc_defects WHERE symptom = :k"), {"k": low})).scalar_one()
            return {"type": "symptom", "key": low, **O.describe()["symptoms"][low], "annotated_voc": int(n)}
        if low in O.COMPONENT_LABELS and low != "device":
            n = (await db.execute(text(
                "SELECT count(DISTINCT voc_id) FROM voc_defects WHERE component = :k"), {"k": low})).scalar_one()
            return {"type": "component", "key": low, **O.describe()["components"][low], "annotated_voc": int(n)}
        if low in O.TOPIC_LABELS:
            n = (await db.execute(text(
                "SELECT count(*) FROM voc_active WHERE categories && CAST(:k AS varchar[])"),
                {"k": [low]})).scalar_one()
            return {"type": "topic", "key": low, "label": O.label("topic", low), "annotated_voc": int(n)}
        if up in cat["by_code"]:
            p = cat["by_code"][up]
            fk = O.family_key(up)
            fam = sorted(c for c in cat["by_code"] if O.family_key(c) == fk)
            n = (await db.execute(text(
                "SELECT count(DISTINCT voc_id) FROM voc_product_links WHERE product_id = :i"),
                {"i": p["id"]})).scalar_one()
            return {"type": "product", "key": up, "name_en": p["name_en"], "name_ko": p["name_ko"],
                    "brand": p["brand"], "category": p["category"], "series": p["series_code"],
                    "family": fk, "family_members": fam, "linked_voc": int(n)}
        it = O.interpret(concept, channel_lexicon=cat["lexicon"])
        return {"type": "query", "note": "개념 키가 아니라 질의로 해석했다",
                "interpretation": _interpretation_view(it, _resolve(it, cat, None), it.days)}
