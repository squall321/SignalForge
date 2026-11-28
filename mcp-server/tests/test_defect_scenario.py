# 결함 시나리오 도구 — 필터 축 조합·직렬화·SQL 안전성 회귀
"""
결함 시나리오 도구 테스트.

시나리오는 숫자 하나로 서지 않는다. 무엇이(profile) · 언제부터(timeline) ·
누가(breakdown) · 무엇과 함께(cooccurrence) · 얼마나 일찍(onset) ·
실제로 뭐라고(evidence) 가 **같은 필터 축으로 조합**돼야 한다.
DB 없이 돌 수 있는 것만 여기서 본다(쿼리 결과는 실 DB 통합에서 확인).
"""
import inspect
import re


def _d():
    from tools import defects
    return defects


# ── 필터 축 ──────────────────────────────────────────────────────────
def test_all_filter_axes_present():
    """시나리오를 '자유자재로' 자르려면 축이 빠짐없어야 한다."""
    names = {n for n, _, _ in _d()._FILTER_SPECS}
    assert names == {"product_code", "component", "symptom", "severity",
                     "modality", "brand", "category", "country", "platform"}


def test_filters_are_parameterized_not_interpolated():
    """필터 값이 SQL 에 문자열로 끼어들면 인젝션이다 — 전부 바인드 파라미터여야 한다."""
    for name, clause, _ in _d()._FILTER_SPECS:
        assert f":{name}" in clause, f"{name} 이 바인드 파라미터가 아니다: {clause}"


def test_none_filters_are_skipped():
    conds, params = _d()._build_filters(90, product_code=None, component="hinge")
    assert "d.component = :component" in conds
    assert not any("p.code" in c for c in conds)
    assert params["component"] == "hinge"
    assert "product_code" not in params


def test_product_code_and_brand_normalized():
    _, params = _d()._build_filters(None, product_code="gzf8", brand="APPLE",
                                    country="kr")
    assert params["product_code"] == "GZF8"
    assert params["brand"] == "apple"
    assert params["country"] == "KR"


def test_period_filter_excludes_future():
    """미래 발행일(0042 참조)이 시계열 끝을 왜곡하면 안 된다."""
    conds, _ = _d()._build_filters(90)
    assert any("published_at <= NOW()" in c for c in conds)


def test_no_period_means_no_date_filter():
    conds, params = _d()._build_filters(None, component="hinge")
    assert not any("make_interval" in c for c in conds)
    assert "days" not in params


# ── 도구 시그니처 ─────────────────────────────────────────────────────
def test_scenario_tools_exist():
    d = _d()
    for fn in ("defect_timeline_tool", "defect_breakdown_tool",
               "defect_cooccurrence_tool", "defect_onset_tool",
               "defect_evidence_tool"):
        assert hasattr(d, fn), fn


def test_evidence_defaults_to_firsthand():
    """전언·기사·구매고민은 고장 서술이 아니다 — 근거 기본값은 1인칭이어야 한다."""
    sig = inspect.signature(_d().defect_evidence_tool)
    assert sig.parameters["modality"].default == "firsthand"


def test_breakdown_axis_whitelisted():
    """by 가 SQL 에 그대로 들어가면 인젝션이다 — 화이트리스트여야 한다."""
    src = inspect.getsource(_d().defect_breakdown_tool)
    assert "axes.get(by" in src, "by 가 화이트리스트 조회를 거치지 않는다"
    assert not re.search(r'f"""[^"]*\{by\}', src), "by 가 SQL 에 직접 보간된다"


def test_timeline_interval_whitelisted():
    src = inspect.getsource(_d().defect_timeline_tool)
    assert '{"day": "day", "week": "week", "month": "month"}' in src


# ── 직렬화 ────────────────────────────────────────────────────────────
def test_percentages_cast_to_float():
    """numeric 은 pydantic 이 문자열로 직렬화해 에이전트가 계산에 못 쓴다."""
    src = inspect.getsource(_d())
    for frag in ("AS firsthand_pct", "AS effective_platforms", "AS avg_days"):
        idx = src.index(frag)
        head = src[max(0, idx - 220):idx]
        assert "::float" in head, f"{frag} 가 float 캐스트를 거치지 않는다"


# ── 글 단위 셈 · 원문 순서 (2026-11-28) ──────────────────────────────────
def test_counts_are_per_document_not_per_joined_row():
    """결함 · 제품 링크 행이 글마다 여러 겹이라 count(*) 는 글 수가 아니다 — 1인칭 수가 docs 보다 컸고 세대 비교의 부정 ·
    결함 비율이 결함 행 수로 기울었다. 1인칭 · 주차 수는 DISTINCT 글로, 출시 후 경과일은 (글, 제품) 쌍으로 센다."""
    src = inspect.getsource(_d())
    assert "count(*) FILTER (WHERE d.modality" not in src
    assert "count(DISTINCT lc.voc_id)::int AS total" in src
    assert "count(DISTINCT d.voc_id)::int AS with_defect" in src
    assert "SELECT DISTINCT p.code AS product_code" in src


def test_evidence_is_newest_first_not_url_order():
    """DISTINCT ON 은 URL 순서로 정렬해야 해서, 바깥에서 다시 최근 글부터 줄 세우지 않으면 사전순 앞 URL 이 나온다."""
    src = inspect.getsource(_d().defect_evidence_tool)
    assert "ORDER BY s.published_at DESC" in src


def test_anomalies_read_the_current_detector_fields():
    """탐지기는 독립 출처 수(indep_sources)를 쓴다 — 옛 eff_platforms 만 읽어 새 기록은 늘 null 이었다."""
    src = inspect.getsource(_d().defect_anomalies_tool)
    assert "payload->>'indep_sources'" in src and "SELECT id," in src
