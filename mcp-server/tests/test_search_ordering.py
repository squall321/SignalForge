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
