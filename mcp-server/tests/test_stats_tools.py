# 전체 통계 도구 — 필터 축·SQL 안전성·공유 모듈 일치 회귀
"""통계 도구 테스트.

포털 LLM 이 "감질맛나는" 답을 하던 이유는 도구가 행만 주고 집계를 안 준 것이었다.
이 테스트는 집계 도구가 (1) 어느 축으로든 잘리고 (2) SQL 인젝션에 안전하고
(3) backend REST 와 **같은 모듈**을 쓰는지를 고정한다.
"""
import inspect
import sys


def _s():
    for p in ("/shared", "shared"):
        if p not in sys.path:
            sys.path.insert(0, p)
    import stats_sql
    return stats_sql


# ── 슬라이스 축 ──────────────────────────────────────────────────────
def test_slice_axes_present():
    names = {n for n, _, _ in _s()._SLICE_SPECS}
    assert names == {"product_code", "brand", "category", "country",
                     "platform", "sentiment", "language"}


def test_slice_filters_parameterized():
    """필터 값이 SQL 문자열로 끼어들면 인젝션이다."""
    for name, clause, _ in _s()._SLICE_SPECS:
        assert f":{name}" in clause, f"{name}: {clause}"


def test_period_filter_excludes_future():
    """미래 발행일(alembic 0042)이 기간 끝을 왜곡하면 안 된다."""
    conds, _, _ = _s()._slice_filters(days=30)
    assert any("published_at <= NOW()" in c for c in conds)


def test_no_period_no_date_filter():
    conds, params, _ = _s()._slice_filters(brand="samsung")
    assert not any("make_interval" in c for c in conds)
    assert params["brand"] == "samsung"


# ── 키워드 매칭 (query.py 와 같은 판단이어야 한다) ──────────────────
def test_korean_uses_substring():
    _, _, mode = _s()._keyword_cond("발열")
    assert mode == "substring"


def test_ascii_uses_fts():
    _, _, mode = _s()._keyword_cond("overheating")
    assert mode == "fts"


def test_like_wildcards_escaped():
    _, params, _ = _s()._keyword_cond("발열%")
    assert params["kw_like"] == r"%발열\%%", params


def test_substring_searches_both_columns():
    clause, _, _ = _s()._keyword_cond("발열")
    assert "content_original" in clause and "content_translated" in clause


# ── 축 화이트리스트 ──────────────────────────────────────────────────
def test_breakdown_axis_whitelisted():
    src = inspect.getsource(_s().voc_breakdown)
    assert "axes.get(by" in src
    assert "{by}" not in src, "by 가 SQL 에 직접 보간된다"


def test_period_compare_axis_whitelisted():
    src = inspect.getsource(_s().period_compare)
    assert "axes.get(by" in src
    assert "{by}" not in src


def test_keyword_interval_whitelisted():
    src = inspect.getsource(_s().keyword_stats)
    assert '{"day": "day", "week": "week", "month": "month"}' in src


# ── 단일 스캔 (한국어는 스캔당 1초대라 집계마다 돌리면 10초가 된다) ──
def test_keyword_stats_single_scan():
    src = inspect.getsource(_s().keyword_stats)
    assert src.count("await execute(") == 1, (
        "집계마다 따로 스캔하면 한국어 키워드에서 10초가 된다(실측)")
    assert "WITH hit AS" in src


# ── MCP 어댑터가 공유 모듈을 쓰는지 ─────────────────────────────────
def test_mcp_adapter_delegates_to_shared():
    from tools import stats
    src = inspect.getsource(stats)
    assert "stats_sql" in src
    # SQL 이 어댑터에 복제돼 있으면 안 된다 — 그래야 REST 와 갈라지지 않는다
    assert "SELECT" not in src.upper().replace("SELECTOR", "")
