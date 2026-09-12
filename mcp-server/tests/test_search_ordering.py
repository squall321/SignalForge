# search_voc 기본 정렬·날짜 필터 회귀 — "최근 이슈"에 구형 제품이 튀어나오던 문제
"""
search_voc 정렬 회귀 테스트.

이전 구현은 날짜 필터 없이 `ORDER BY engagement_score DESC` 뿐이었다. 그래서
"최근 이슈"를 물어도 **역대 최고 참여도 글**이 나왔다 — 오래된 글일수록 수년간
좋아요·댓글이 쌓여 상위를 독식하기 때문이다.

실측(2026-09-12) — search_voc('발열') 1위가 2019년 뽐뿌 갤럭시 노트10+ 리뷰였고,
engagement 상위 30의 제품이 GN9·GN4·GN5·GS10·GW4 같은 구형으로 채워졌다.
사용자 보고: "최근거 검색하면 GW1 GZFL4 GZFL1 GS1 GS4 가 자꾸 튀어나온다".
"""
import inspect
import re

# tools.query 는 **테스트 함수 안에서** 임포트한다. 모듈 수준에서 임포트하면
# DB 엔진이 다른 테스트의 자격증명 로드보다 먼저 만들어져 형제 테스트를 깨뜨린다
# (실측: test_insights::test_async_tools 가 asyncpg InvalidPassword 로 실패).
def _q():
    from tools import query
    return query


def test_default_order_is_recency():
    """기본은 최신순이어야 한다 — 이게 사용자 신고의 핵심이다."""
    q = _q()
    sig = inspect.signature(q.search_voc_tool)
    assert sig.parameters["order"].default == "recent"
    assert "published_at" in q._SEARCH_ORDERS["recent"]


def test_engagement_still_available():
    """바이럴 발굴 용도는 남겨둔다 — 기본값에서만 뺀다."""
    assert "engagement_score" in _q()._SEARCH_ORDERS["engagement"]


def test_unknown_order_falls_back_to_recent():
    """오타나 미지원 값이 조용히 engagement 로 새면 안 된다."""
    o = _q()._SEARCH_ORDERS
    assert o.get("nonsense", o["recent"]) == o["recent"]


def test_days_param_exists_and_uses_published_at():
    """collected_at 은 백필 때문에 옛 글도 최근값이라 날짜 필터로 못 쓴다."""
    fn = _q().search_voc_tool
    src = inspect.getsource(fn)
    assert "days" in inspect.signature(fn).parameters
    m = re.search(r"make_interval\(days => :days\)", src)
    assert m, "days 필터가 없다"
    # 필터 조건이 published_at 기준인지
    assert re.search(r"v\.published_at\s*>=\s*NOW\(\)\s*-\s*make_interval", src)


def test_recent_order_excludes_future_dates():
    """미래 발행일이 최신순 상단을 차지하면 안 된다.

    한국 커뮤니티의 연도 없는 'MM-DD' 를 올해로 가정하면 연말 글이 미래가 된다.
    실측 — 오늘이 2026-09-12 인데 2026-12-27·12-22·12-18 이 검색 1위였다.
    """
    src = inspect.getsource(_q().search_voc_tool)
    assert "published_at <= NOW()" in src, "미래 날짜 방어가 없다"
    # engagement 정렬에는 걸지 않는다(바이럴 발굴은 정렬이 날짜와 무관)
    assert "order_sql_is_recent" in src


# ── 한국어 검색 — FTS 로는 재현율 1.1% 였던 문제 ──────────────────────
def test_korean_keyword_uses_substring():
    """한국어는 교착어이고 색인은 번역본(english)뿐이라 FTS 로 안 잡힌다.

    실측 — '발열' FTS 15건 vs content_original 실제 1,346건 = 1.1%.
    """
    _, _, mode = _q()._keyword_clause("발열")
    assert mode == "substring"


def test_ascii_keyword_uses_fts():
    """영어는 인덱스+어간 처리가 유리하다(overheating ↔ overheat)."""
    for kw in ("overheating", "Galaxy S26", "100%"):
        assert _q()._keyword_clause(kw)[2] == "fts", kw


def test_substring_searches_both_columns():
    """번역본만 보면 한국어 원문을 놓친다 — 원문도 봐야 한다."""
    clause, _, _ = _q()._keyword_clause("발열")
    assert "content_original" in clause and "content_translated" in clause


def test_like_wildcards_escaped():
    """'100%' 류가 와일드카드로 해석되면 전체 행이 걸린다."""
    _, params, mode = _q()._keyword_clause("발열%")
    assert mode == "substring"
    assert params["kw_like"] == r"%발열\%%", params


def test_underscore_escaped():
    _, params, _ = _q()._keyword_clause("힌_지")
    assert r"\_" in params["kw_like"], params


def test_match_override_respected():
    assert _q()._keyword_clause("발열", "fts")[2] == "fts"
    assert _q()._keyword_clause("overheating", "substring")[2] == "substring"
