# 제품 키워드 검색이 토큰 AND 로 되돌아가 엉뚱한 제품을 섞지 않는지 고정한다.
"""막는 고장(2026-10-15 실측).

`plainto_tsquery('english','S25 Ultra')` 는 `'s25' & 'ultra'` 로 바뀌어 글
**전체에서 따로** 찾는다. 그래서 search_voc('S25 Ultra') 상위 10건 중 5건이
"S25 Ultra" 를 포함하지 않았다 — 아이폰 16 글, 픽셀 10 Pro 글, 독일어 지원중단
기사, 케이스 브랜드 "Ultra Hybrid" 글. 정밀도 50%.

'Galaxy Watch 8' 은 더 심했다 — 'watch' 가 흔한 동사라 plainto 5,984건 vs
구절 2,727건으로 2.2배 과다였다.

구절 검색(phraseto_tsquery)은 `'s25' <-> 'ultra'` 로 인접을 요구한다.
정밀도 99%대, 재현율 95~99%, FTS 인덱스 그대로 사용.
"""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in ("/shared", str(ROOT.parent / "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(ROOT))

from keyword_match import (  # noqa: E402
    is_multi_token,
    keyword_clause,
    resolve_mode,
)


# ── 경로 판정 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kw,expected", [
    ("S25 Ultra", "phrase"),        # ASCII 여러 토큰 → 구절
    ("Galaxy Watch 8", "phrase"),
    ("battery drain", "phrase"),
    ("overheating", "fts"),         # ASCII 한 토큰 → FTS(어간 처리 이점)
    ("S25", "fts"),
    ("발열", "substring"),           # 비ASCII → 부분일치
    ("갤럭시 S25", "substring"),      # 비ASCII 가 섞이면 부분일치
    ("カメラ", "substring"),
])
def test_mode_routing(kw, expected):
    assert resolve_mode(kw) == expected, f"{kw!r} → {resolve_mode(kw)}"


def test_multi_token_detection():
    assert is_multi_token("S25 Ultra")
    assert is_multi_token("  Galaxy   S25  ")
    assert not is_multi_token("S25")
    assert not is_multi_token("")


# ── 생성되는 SQL ─────────────────────────────────────────────────────────
def test_multiword_ascii_uses_phrase_not_plainto():
    """이게 이 테스트의 핵심이다 — plainto 로 되돌아가면 오염이 재발한다."""
    clause, params, mode = keyword_clause("S25 Ultra")
    assert mode == "phrase"
    assert "phraseto_tsquery" in clause
    assert "plainto_tsquery" not in clause, "토큰 AND 로 되돌아갔다 — 다른 제품이 섞인다"
    assert params == {"keyword": "S25 Ultra"}


def test_single_token_keeps_plainto():
    """한 토큰이면 plainto·phraseto 가 같은 쿼리를 만든다. 어간 처리를 위해 유지."""
    clause, _, mode = keyword_clause("overheating")
    assert mode == "fts" and "plainto_tsquery" in clause


def test_non_ascii_uses_both_columns():
    """한국어는 content_original 을 봐야 한다 — 96%가 영어로 번역돼 있다."""
    clause, params, mode = keyword_clause("발열")
    assert mode == "substring"
    assert "content_original" in clause and "content_translated" in clause
    assert params["kw_like"] == "%발열%"


@pytest.mark.parametrize("raw,esc", [
    ("100%", "%100\\%%"),
    ("a_b", "%a\\_b%"),
    ("back\\slash", "%back\\\\slash%"),
])
def test_ilike_metacharacters_escaped(raw, esc):
    """'100%' 가 와일드카드로 해석되면 전체 스캔이 된다.

    이스케이프는 부분일치 경로에서만 의미가 있다 — ASCII 단일 토큰은 FTS 로
    가고 거기서는 %·_ 가 무해하므로, 모드를 명시해 검증한다.
    """
    _, params, mode = keyword_clause(raw, "substring")
    assert mode == "substring"
    assert params["kw_like"] == esc


def test_metachars_in_korean_keyword_are_escaped():
    """실제 경로 — 한국어에 %가 섞이면 자동으로 부분일치이고 이스케이프돼야 한다."""
    _, params, mode = keyword_clause("배터리 100%")
    assert mode == "substring"
    assert params["kw_like"] == "%배터리 100\\%%"


# ── 명시 모드 ────────────────────────────────────────────────────────────
def test_explicit_modes_win():
    assert keyword_clause("S25 Ultra", "fts")[2] == "fts"
    assert keyword_clause("S25 Ultra", "substring")[2] == "substring"
    assert keyword_clause("발열", "phrase")[2] == "phrase"


def test_unknown_mode_falls_back_to_auto():
    assert keyword_clause("S25 Ultra", "nonsense")[2] == "phrase"


# ── 복제 방지 ────────────────────────────────────────────────────────────
def test_no_module_builds_its_own_fts_clause():
    """조건 생성이 복제되면 REST·MCP 가 갈라진다 — 전례가 있다."""
    offenders = []
    roots = [ROOT / "tools", ROOT.parent / "shared", ROOT.parent / "backend" / "app"]
    for root in roots:
        if not root.is_dir():
            continue
        for py in root.rglob("*.py"):
            if py.name == "keyword_match.py" or ".venv" in py.parts:
                continue
            src = py.read_text()
            if "plainto_tsquery" in src or "phraseto_tsquery" in src:
                offenders.append(str(py.relative_to(ROOT.parent)))
    assert not offenders, (
        "tsquery 를 직접 만드는 모듈(공용 정본을 쓰지 않는다): " + ", ".join(offenders))


# ── 실DB 정밀도 (DB 없으면 skip) ─────────────────────────────────────────
_DB = os.getenv("DATABASE_URL", "")


@pytest.mark.skipif(not _DB, reason="DATABASE_URL 없음")
def test_phrase_beats_plainto_on_real_corpus():
    """실측으로 개선을 증명한다 — 절 생성만 보면 효과를 알 수 없다."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    kw = "Galaxy Watch 8"

    async def run():
        eng = create_async_engine(_DB)
        try:
            async with eng.connect() as c:
                q = text("""
                    SELECT
                      count(*) FILTER (WHERE tv @@ plainto_tsquery('english', :k))  AS plain,
                      count(*) FILTER (WHERE tv @@ phraseto_tsquery('english', :k)) AS phrase,
                      count(*) FILTER (WHERE tv @@ phraseto_tsquery('english', :k)
                                         AND (co ILIKE :like OR ct ILIKE :like))    AS phrase_ok
                    FROM (SELECT content_original co, COALESCE(content_translated,'') ct,
                                 to_tsvector('english', COALESCE(content_translated,'')) tv
                          FROM voc_records WHERE archived_at IS NULL) s
                """)
                return (await c.execute(q, {"k": kw, "like": f"%{kw}%"})).one()
        finally:
            await eng.dispose()

    r = asyncio.run(run())
    assert r.phrase > 0, "구절 검색이 아무것도 못 찾는다 — 코퍼스나 인덱스 문제"
    precision = r.phrase_ok / r.phrase
    assert precision >= 0.95, f"구절 정밀도 {precision:.1%} — 기대 95% 이상"
    assert r.plain > r.phrase * 1.3, (
        f"plainto({r.plain}) 가 구절({r.phrase})보다 과하게 많지 않다 — "
        "오염을 재현하지 못하면 이 테스트가 아무것도 막지 못한다")


# ── 제품명 해석 (표기 흔들림 방어) ───────────────────────────────────────
@pytest.mark.skipif(not _DB, reason="DATABASE_URL 없음")
def test_spelling_variants_converge_to_one_product():
    """표기가 달라도 같은 제품 코드로 수렴해야 한다.

    구절 검색으로 정밀도는 99%대가 됐지만 **재현율의 표기 의존**은 남는다.
    실측(2026-10-15) 키워드 검색 건수 —
      'S25 Ultra' 9,680 · 'Galaxy S25 Ultra' 2,296 · 'S25Ultra' 315 ·
      '갤럭시 S25 울트라' 88     (제품 태깅 기준 GS25U = 9,502)
    100배 차이다. 제품을 묻는 질문은 코드로 확정해야 흔들리지 않는다.
    """
    import asyncio
    import sys as _s
    _s.path.insert(0, str(ROOT))
    from tools.query import resolve_product_keyword_tool

    variants = ["S25 Ultra", "Galaxy S25 Ultra", "갤럭시 S25 울트라", "S25Ultra", "GS25U"]

    async def run():
        return [await resolve_product_keyword_tool(v) for v in variants]

    results = asyncio.run(run())
    codes = {r["resolved_code"] for r in results}
    assert codes == {"GS25U"}, f"표기마다 다른 제품으로 해석된다: {codes}"
    counts = {r["tagged_count"] for r in results}
    assert len(counts) == 1, f"같은 제품인데 태깅 건수가 다르다: {counts}"
    # 흔들림이 실재함을 함께 고정 — 재현 못 하면 이 테스트가 무의미해진다
    hits = [r["keyword_hits"] for r in results]
    assert max(hits) > min(hits) * 5, (
        f"키워드 검색 흔들림을 재현하지 못했다: {hits} — 해석 도구의 존재 이유가 사라진다")


@pytest.mark.skipif(not _DB, reason="DATABASE_URL 없음")
def test_non_product_keyword_resolves_to_none():
    """일반 키워드를 제품으로 억지 해석하면 안 된다."""
    import asyncio
    import sys as _s
    _s.path.insert(0, str(ROOT))
    from tools.query import resolve_product_keyword_tool

    r = asyncio.run(resolve_product_keyword_tool("발열"))
    assert r["resolved_code"] is None, r["candidates"][:2]
    assert r["keyword_hits"] > 0, "일반 키워드 건수는 세야 한다"
    assert r["match_mode"] == "substring"


@pytest.mark.skipif(not _DB, reason="DATABASE_URL 없음")
def test_note_states_the_gap():
    """호출자가 '왜 숫자가 다른가'를 바로 알 수 있어야 한다."""
    import asyncio
    import sys as _s
    _s.path.insert(0, str(ROOT))
    from tools.query import resolve_product_keyword_tool

    r = asyncio.run(resolve_product_keyword_tool("Galaxy S25 Ultra"))
    assert "GS25U" in r["note"]
    assert "product_code" in r["note"], "정확한 경로를 알려주지 않는다"
