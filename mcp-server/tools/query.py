"""MCP Query Tools — VOC 조회"""
import re

from typing import Optional, List
from datetime import datetime
from db import get_db_session
from sqlalchemy import text

# 키워드 매칭 판단은 shared/keyword_match.py 가 **정본**이다 — 예전엔 이 파일과
# shared/stats_sql.py 에 복제돼 있어 REST·MCP 가 갈라질 수 있었다.
# 경로 해결은 tools/stats.py 와 같은 방식(절대경로를 박지 않는다).
import pathlib as _pathlib  # noqa: E402
import sys as _sys  # noqa: E402
for _p in ("/shared", str(_pathlib.Path(__file__).resolve().parents[2] / "shared")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
from keyword_match import all_tokens_clause, keyword_clause  # noqa: E402

# 이슈가 아닌 메타 태그 — model_mention 은 모델 이름만 나와도, comparison 은 그때마다 가산돼(categorizer)
# 비교가 없는 글에도 붙는다. 이슈 순위 1·2위를 차지해 '비교' 가 최대 이슈로 읽혔다(SG-10). 이슈를 줄 세우는
# 곳(차트·상위 이슈·요약·브리핑)에서는 뺀다.
META_CATEGORIES = ["comparison", "model_mention"]


def _parse_dt(s: str):
    """'YYYY-MM-DD' 또는 ISO 타임스탬프 → datetime. asyncpg 는 str 를 안 받으므로 객체로 변환."""
    return datetime.fromisoformat(s)


async def query_voc_tool(
    product_code: Optional[str] = None,
    country: Optional[str] = None,
    category: Optional[str] = None,
    sentiment: Optional[str] = None,
    platform: Optional[str] = None,
    keyword: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 20,
) -> List[dict]:
    # 모든 필터가 선택 — product_code 없이도 전체 VOC 를 자유 조회(제품 태깅은 일부라
    # LEFT JOIN 으로 미태깅 VOC 도 포함). 날짜구간(published_at)·플랫폼·키워드까지 조합 가능.
    conditions: List[str] = ["TRUE"]
    params: dict = {"limit": limit}

    if product_code:
        conditions.append("p.code = :product_code")
        params["product_code"] = product_code.upper()
    if country:
        conditions.append("v.country_code = :country")
        params["country"] = country.upper()
    if sentiment:
        conditions.append("v.sentiment_label = :sentiment")
        params["sentiment"] = sentiment
    if category:
        conditions.append(":category = ANY(v.categories)")
        params["category"] = category
    if platform:
        conditions.append("pl.code = :platform")
        params["platform"] = platform
    if keyword:
        _c, _pp, _ = keyword_clause(keyword)
        conditions.append(_c)
        params.update(_pp)
    if start_date:
        conditions.append("v.published_at >= :start_date")
        params["start_date"] = _parse_dt(start_date)
    if end_date:
        conditions.append("v.published_at < :end_date")
        params["end_date"] = _parse_dt(end_date)

    where = " AND ".join(conditions)
    stmt = text(f"""
        SELECT
            v.id, v.external_id, v.source_url, v.author_name,
            v.content_original, v.content_translated,
            v.language_detected, v.country_code,
            v.sentiment_score, v.sentiment_label, v.categories,
            v.likes_count, v.comments_count, v.engagement_score,
            v.published_at, pl.name AS platform_name,
            p.code AS product_code, p.name_ko AS product_name
        FROM voc_active v
        LEFT JOIN products p ON p.id = v.product_id
        LEFT JOIN platforms pl ON pl.id = v.platform_id
        WHERE {where}
        ORDER BY v.published_at DESC NULLS LAST
        LIMIT :limit
    """)

    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
        return [dict(r) for r in rows]


async def get_top_issues_tool(
    product_code: str, period_days: int = 30, top_n: int = 10
) -> List[dict]:
    stmt = text("""
        SELECT
            cat AS category,
            COUNT(*) AS total_count,
            ROUND(
                SUM(CASE WHEN v.sentiment_label = 'negative' THEN 1 ELSE 0 END)::numeric
                / NULLIF(COUNT(*), 0) * 100, 1
            ) AS negative_rate
        FROM voc_active v
        JOIN products p ON p.id = v.product_id,
             unnest(v.categories) AS cat
        WHERE p.code = :product_code
          AND v.collected_at >= NOW() - make_interval(days => :period_days)
          AND v.categories IS NOT NULL
          AND cat <> ALL(:meta)
        GROUP BY cat
        ORDER BY total_count DESC
        LIMIT :top_n
    """)

    async with get_db_session() as db:
        rows = (await db.execute(stmt, {
            "product_code": product_code.upper(),
            "period_days": period_days,
            "top_n": top_n,
            "meta": META_CATEGORIES,
        })).mappings().all()
        return [dict(r) for r in rows]



# ── 키워드 매칭 전략 ─────────────────────────────────────────────────────
# FTS 인덱스는 to_tsvector('english', content_translated) 하나뿐이라 **한국어
# 키워드가 사실상 안 먹는다.** 실측(2026-09-12) — '발열' FTS 15건 vs
# content_original 실제 포함 1,346건 = 재현율 1.1%.
# 원인은 두 겹이다 — (1) 한국어 행의 96.1%(118,513/123,322)가 영어로 번역돼
# content_translated 에 '발열'이 없고, (2) 검색이 content_original 을 아예 안 본다.
#
# 'simple' 설정으로 content_original 을 색인해도 한국어는 교착어라 조사 결합형
# ('발열이')을 못 잡아 재현율이 49.8%(670/1,346)에 그친다.
#
# 그래서 언어에 따라 경로를 나눈다 —
#   · ASCII 키워드  → FTS (인덱스 사용, 어간 처리로 overheating↔overheat 매칭)
#   · 비ASCII 키워드 → 두 컬럼 부분일치 (재현율 100%, 실측 1.0~1.4초)
# pg_trgm 인덱스를 깔면 부분일치도 빨라지지만 본문 203MB 에 수백 MB 가 더 붙는다.
# 1.2초가 대화형 호출에 견딜 만하므로 스키마는 건드리지 않는다.
# 전략 전문과 실측 근거는 shared/keyword_match.py 에 있다.
def _keyword_clause(keyword: str, match: str = "auto"):
    """(clause, params, 적용된 모드) — 공용 정본에 위임한다."""
    return keyword_clause(keyword, match)


# 정렬 옵션 — 기본은 최신순이다.
# 이전에는 날짜 필터 없이 `ORDER BY engagement_score DESC` 뿐이었다. 그러면 "최근
# 이슈"를 물어도 **역대 최고 참여도 글**이 나온다. 오래된 글일수록 수년간 좋아요·
# 댓글이 쌓여 상위를 독식하기 때문이다. 실측(2026-09-12) — search_voc('발열') 의
# 1위가 2019년 뽐뿌 갤럭시 노트10+ 리뷰였고, engagement 상위 30의 제품이 GN9·GN4·
# GN5·GS10·GW4 같은 구형으로 채워졌다(21건은 아예 미태깅).
# 관련성 점수가 따로 없는 FTS 검색에서 "가장 바이럴했던 글"보다 "가장 최근 글"이
# 기본값으로 훨씬 유용하다. 바이럴 발굴은 order='engagement' 또는 전용 도구
# get_engagement_leaders(period_days 필터 내장)를 쓴다.
_SEARCH_ORDERS = {
    "recent": "v.published_at DESC NULLS LAST",
    "engagement": "v.engagement_score DESC NULLS LAST",
}


async def search_voc_tool(
    keyword: str, product_code: Optional[str] = None, limit: int = 30,
    days: Optional[int] = None, order: str = "recent", match: str = "auto",
) -> List[dict]:
    return (await search_voc_detail(keyword, product_code, limit, days, order, match))["results"]


async def search_voc_detail(
    keyword: str, product_code: Optional[str] = None, limit: int = 30,
    days: Optional[int] = None, order: str = "recent", match: str = "auto",
) -> dict:
    """키워드 검색 — {total, truncated, results, match_mode, notes}.

    auto 로 0건이면 더 느슨한 경로로 다시 찾는다(SG-07). 한국어 여러 낱말은 auto 가 통째 부분일치라
    '폴드 힌지 먼지' 처럼 낱말 순서·띄어쓰기가 조금만 달라도 0건이었다(match='fts' 면 2건).
    차례 — 낱말 모두 일치(순서 무관) → 번역문 FTS(AND). 빈 결과에는 왜 비었는지 notes 를 단다.
    """
    rows, used = await _search_rows(keyword, product_code, limit, days, order, match)
    tried, notes = [used], []
    if not rows and (match or "auto").lower() == "auto" and len((keyword or "").split()) > 1:
        for alt in ("all_tokens", "fts"):
            if alt == used:
                continue
            rows, alt_used = await _search_rows(keyword, product_code, limit, days, order, alt)
            tried.append(alt_used)
            if rows:
                notes.append(f"'{used}' 로 0건이라 '{alt_used}'(" + ("낱말 모두 · 순서 무관" if alt == "all_tokens"
                             else "영어 번역문 FTS · 낱말 모두") + ")로 찾았다")
                used = alt_used
                break
    if not rows:
        notes.append(f"키워드 '{keyword}' 에 맞는 글이 없다(시도한 경로: {', '.join(tried)})"
                     + (f" — 제품 {product_code} 범위 안에서" if product_code else "")
                     + (f" · 최근 {days}일" if days else ""))
    return {"total": len(rows), "truncated": len(rows) >= limit, "results": rows,
            "match_mode": used, "notes": notes}


async def _search_rows(keyword, product_code, limit, days, order, match):
    # products 는 LEFT JOIN — 제품 태깅은 일부라 INNER JOIN 시 미태깅 VOC 가 조용히
    # 누락된다. 검색은 전체 voc_active 를 대상으로 해야 한다(product_code 지정 시만 좁힘).
    if match == "all_tokens":
        cl = all_tokens_clause((keyword or "").split(), prefix="kt")
        if not cl:
            return [], "all_tokens"
        kw_clause, kw_params, used_match = cl[0], cl[1], "all_tokens"
    else:
        kw_clause, kw_params, used_match = _keyword_clause(keyword, match)
    conditions = [kw_clause]
    params: dict = {"limit": limit, **kw_params}

    if product_code:
        conditions.append("p.code = :product_code")
        params["product_code"] = product_code.upper()

    order_key = (order or "recent").lower()
    order_sql_is_recent = order_key not in _SEARCH_ORDERS or order_key == "recent"
    order_sql = _SEARCH_ORDERS.get(order_key, _SEARCH_ORDERS["recent"])

    if days:
        # published_at 기준 — collected_at 은 백필 때문에 옛 글도 최근값이라 못 쓴다
        conditions.append("v.published_at >= NOW() - make_interval(days => :days)")
        params["days"] = int(days)

    # 미래 발행일 방어. 한국 커뮤니티의 연도 없는 'MM-DD' 표기를 올해로 가정하면
    # 연말 글이 미래가 된다. 최신순 정렬은 그런 행을 **맨 위로** 끌어올리므로
    # (실측: 오늘이 09-12 인데 12-27·12-22 가 1위) 정렬 기준에서 배제한다.
    # 기존 32건은 alembic 0042 로 보정했고, 이건 재발 방어다.
    if order_sql_is_recent:
        conditions.append("(v.published_at IS NULL OR v.published_at <= NOW())")

    where = " AND ".join(conditions)
    stmt = text(f"""
        SELECT
            v.id, v.source_url, v.author_name,
            v.content_translated, v.sentiment_label,
            v.categories, v.published_at,
            pl.name AS platform_name,
            p.name_en AS product_name
        FROM voc_active v
        LEFT JOIN products p ON p.id = v.product_id
        LEFT JOIN platforms pl ON pl.id = v.platform_id
        WHERE {where}
        ORDER BY {order_sql}
        LIMIT :limit
    """)

    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
        return [dict(r) for r in rows], used_match


# ── 제품명 → 제품 코드 해석 ───────────────────────────────────────────────
# 왜 필요한가. 자유 텍스트로 제품을 묻는 것은 구조적으로 불안정하다.
# 같은 제품인데 표기에 따라 결과가 100배 차이난다(2026-10-15 실측) —
#   'S25 Ultra' 9,680 · 'Galaxy S25 Ultra' 2,296 · 'S25Ultra' 315 ·
#   '갤럭시 S25 울트라' 88     (제품 태깅 기준 GS25U = 9,502)
# 구절 검색으로 정밀도는 99%대가 됐지만 **재현율의 표기 의존**은 남는다.
# 그래서 "이 키워드는 어느 제품인가"를 카탈로그(products)로 확정해 주고,
# 키워드 검색이 몇 건을 놓치는지 같이 돌려준다. 호출자가 선택할 수 있게 한다.
# 정규화는 파이썬에서 한다 — SQL 안에서 CTE 로 하면 GROUP BY 에 끌려 들어간다.
_NORM_RE = re.compile(r"[^A-Za-z0-9가-힣]")


def _norm_name(s: str) -> str:
    return _NORM_RE.sub("", s or "").lower()


_RESOLVE_SQL = text("""
    SELECT p.code, p.name_en, p.name_ko, p.brand, p.category,
           count(v.id)::int AS tagged_count,
           (lower(regexp_replace(COALESCE(p.name_en,''), '[^A-Za-z0-9가-힣]', '', 'g')) = :k
            OR lower(regexp_replace(COALESCE(p.name_ko,''), '[^A-Za-z0-9가-힣]', '', 'g')) = :k
            OR lower(p.code) = :k) AS exact
    FROM products p
    LEFT JOIN voc_records v ON v.product_id = p.id AND v.archived_at IS NULL
    WHERE p.is_active
      AND (
        lower(regexp_replace(COALESCE(p.name_en,''), '[^A-Za-z0-9가-힣]', '', 'g')) = :k
        OR lower(regexp_replace(COALESCE(p.name_ko,''), '[^A-Za-z0-9가-힣]', '', 'g')) = :k
        OR lower(p.code) = :k
        OR lower(regexp_replace(COALESCE(p.name_en,''), '[^A-Za-z0-9가-힣]', '', 'g')) LIKE :klike
        OR lower(regexp_replace(COALESCE(p.name_ko,''), '[^A-Za-z0-9가-힣]', '', 'g')) LIKE :klike
      )
    GROUP BY p.code, p.name_en, p.name_ko, p.brand, p.category
    ORDER BY exact DESC, count(v.id) DESC
    LIMIT 5
""")


def _matcher_codes(keyword: str) -> List[str]:
    """제품 매처(태깅의 정본 별칭 사전 — 한국어·마케팅명·약칭)가 키워드에서 읽는 제품 코드.

    카탈로그 이름(name_en·name_ko)은 '갤럭시 Z 폴드8' 한 표기뿐이라 'S26U'·'폴드 와이드'·'z폴드8' 같은
    말은 못 잡는다. 매처는 /crawler 바인드에 있다 — 없으면 이 폴백만 건너뛴다.
    """
    for _p in ("/crawler", str(_pathlib.Path(__file__).resolve().parents[2] / "crawler")):
        if _pathlib.Path(_p).is_dir() and _p not in _sys.path:
            _sys.path.append(_p)
    try:
        from base import product_match as _pm
    except Exception:  # noqa: BLE001
        return []
    return [c for c, _role in _pm.infer_all_product_codes(keyword)]


_BY_CODES_SQL = text("""
    SELECT p.code, p.name_en, p.name_ko, p.brand, p.category,
           count(v.id)::int AS tagged_count, false AS exact
    FROM products p
    LEFT JOIN voc_records v ON v.product_id = p.id AND v.archived_at IS NULL
    WHERE p.code = ANY(:codes)
    GROUP BY p.code, p.name_en, p.name_ko, p.brand, p.category
""")


def no_tagged_note(code: str, keyword_hits: int, keyword: str, match_mode: str) -> str:
    """태깅 0 · 키워드 >0 — '데이터 없음' 이 아니라 '이 서버의 제품 태깅이 비었다' 이다(SG-02)."""
    return (f"no_tagged_records — 제품 {code} 로 태깅된 글이 이 서버에 0건이지만 키워드 '{keyword}' 로는 "
            f"{keyword_hits:,}건이 있다({match_mode}). 'VOC 없음' 이 아니라 제품 태깅이 비었거나 카탈로그 id 가 "
            f"어긋난 서버다 — 제품 축 통계 대신 search_voc(keyword='{keyword}', semantic=False) 로 본문을 보라")


async def resolve_product_keyword_tool(keyword: str) -> dict:
    """키워드가 가리키는 제품을 카탈로그로 확정하고, 키워드 검색과의 차이를 보여준다."""
    kw_clause, kw_params, used_match = _keyword_clause(keyword)
    _nk = _norm_name(keyword)
    via = "catalog_name"
    async with get_db_session() as db:
        matches = [dict(r) for r in (await db.execute(
            _RESOLVE_SQL, {"k": _nk, "klike": f"%{_nk}%"})).mappings().all()]
        if not matches or not matches[0]["exact"]:
            # 카탈로그 이름에 없거나 부분일치뿐이면 태깅과 같은 별칭 사전으로 다시 본다(SG-04)
            codes = _matcher_codes(keyword)
            if codes:
                found = {r["code"]: dict(r) for r in (await db.execute(
                    _BY_CODES_SQL, {"codes": codes})).mappings().all()}
                alias = [found[c] for c in codes if c in found]
                if alias:
                    matches = alias + [m for m in matches if m["code"] not in found]
                    via = "alias"
        kw_count = (await db.execute(text(f"""
            SELECT count(*)::int FROM voc_records v
            WHERE v.archived_at IS NULL AND {kw_clause}
        """), kw_params)).scalar_one()

    best = matches[0] if matches else None
    out = {
        "keyword": keyword,
        "match_mode": used_match,
        "keyword_hits": kw_count,
        "candidates": matches[:5],
        "resolved_code": best["code"] if best else None,
        "resolved_via": via if best else None,
        "tagged_count": best["tagged_count"] if best else None,
        "status": "resolved" if best else "no_catalog_match",
    }
    if best and best["tagged_count"] == 0 and kw_count > 0:
        out["status"] = "no_tagged_records"
        out["note"] = no_tagged_note(best["code"], kw_count, keyword, used_match)
    elif best:
        gap = best["tagged_count"] - kw_count
        out["note"] = (
            f"키워드 '{keyword}' 는 제품 {best['code']}({best['name_en']}) 로 해석된다"
            + (" (별칭 사전)" if via == "alias" else "") + ". "
            f"키워드 검색 {kw_count}건 vs 제품 태깅 {best['tagged_count']}건"
            f"(차이 {gap:+d}). 제품 단위 질문이면 "
            f"search_voc(product_code='{best['code']}') 또는 제품 축 통계를 쓰는 것이 "
            f"표기에 흔들리지 않는다."
        )
    else:
        out["note"] = (
            f"'{keyword}' 와 일치하는 카탈로그 제품이 없다(이름·별칭 사전 모두). 일반 키워드로 보고 "
            f"{used_match} 경로로 {kw_count}건을 찾았다."
        )
    return out


async def product_zero_diagnosis(db, product_code: str, days: Optional[int] = None) -> dict:
    """제품 단위 도구가 0건일 때 — 카탈로그에 없나 · 태깅이 비었나(키워드로는 있나) · 기간에만 없나."""
    code = product_code.upper()
    p = (await db.execute(text("SELECT id, name_en, name_ko FROM products WHERE code = :c"),
                          {"c": code})).mappings().first()
    if not p:
        return {"status": "unknown_product",
                "note": f"{code} 는 이 서버 카탈로그에 없는 제품 코드다 — resolve_product_keyword 로 이름에서 코드를 찾는다"}
    tagged_all, last = (await db.execute(text(
        "SELECT count(*), max(collected_at) FROM voc_active WHERE product_id = :pid"), {"pid": p["id"]})).one()
    period = "AND v.collected_at >= NOW() - make_interval(days => :days)" if days else ""
    hits, used, name = 0, "-", None
    for nm in (p["name_ko"], p["name_en"]):
        if not nm:
            continue
        cl, prm, mode = _keyword_clause(nm)
        n = (await db.execute(text(f"SELECT count(*) FROM voc_active v WHERE {cl} {period}"),
                              {**prm, **({"days": days} if days else {})})).scalar_one()
        if n > hits:
            hits, used, name = n, mode, nm
    if not tagged_all and hits:
        return {"status": "no_tagged_records", "keyword_hits": hits, "tagged_all": 0,
                "note": no_tagged_note(code, hits, name, used)}
    if tagged_all:
        return {"status": "none_in_period", "tagged_all": tagged_all, "keyword_hits": hits,
                "note": (f"{code} 로 태깅된 글은 전 기간 {tagged_all:,}건(마지막 수집 "
                         f"{last.date().isoformat() if last else '-'})인데 이 기간에는 없다")}
    return {"status": "no_records", "tagged_all": 0, "keyword_hits": 0,
            "note": f"{code} 는 태깅·이름 키워드 모두 0건이다"}
