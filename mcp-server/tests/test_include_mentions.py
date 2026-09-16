# 비교글에서 언급만 된 제품을 세는 모드가 기존 수치를 바꾸지 않는지 검증한다.
import pathlib
import sys

for _p in ("/shared", str(pathlib.Path(__file__).resolve().parents[2] / "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import stats_sql  # noqa: E402


def test_default_is_primary_based():
    """기본값이 바뀌면 기존 대시보드·리포트 수치가 조용히 달라진다."""
    import inspect
    sig = inspect.signature(stats_sql.voc_breakdown)
    assert sig.parameters["include_mentions"].default is False


def test_joins_switch_on_flag():
    assert "voc_product_links" in stats_sql._joins(True)
    assert "voc_product_links" not in stats_sql._joins(False)
    # 기본 조인은 primary 만 본다
    assert "p.id = v.product_id" in stats_sql._joins(False)
    # 언급 조인은 링크의 product_id 를 본다
    assert "p.id = l.product_id" in stats_sql._joins(True)


def test_mention_join_keeps_platform_axis():
    """언급 모드에서도 플랫폼·국가 축이 살아 있어야 한다 — 슬라이스 필터가 v 를 쓴다."""
    j = stats_sql._joins(True)
    assert "platforms pl" in j
    assert "voc_records v" in j


def test_slice_specs_reference_valid_aliases():
    """필터 조건이 두 조인 모두에서 해석돼야 한다."""
    for _, cond, _ in stats_sql._SLICE_SPECS:
        alias = cond.split(".", 1)[0].strip()
        assert alias in ("p", "v", "pl"), cond
