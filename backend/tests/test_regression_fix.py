"""R20 D — regression baseline failed 5건 fix 단위 테스트.

R8/R12 threshold 는 *dedup 전* 모집단 기준.  R14 dedup (168,112 → 113,557, -32%)
이후 안정 상태 (R20 117,958) 와 직접 비교가 부정확.  R20 fix:

  - GS22  threshold 350 → 200  (current 218, baseline R20)
  - GS25  threshold 1800 → 800 (current 847, baseline R20)
  - GB3   threshold 450 → 200  (current 210, baseline R20)
  - GZF1  threshold 280 → 250  (안전 마진)
  - voc_total threshold 150,000 → 110,000 (current 117,958)

라이브 backend 없이 _internal 모듈의 상수만 검증한다.  엔드포인트 응답 회귀는
test_regression_coverage.py 의 라이브 케이스가 담당.
"""
import importlib


_internal = importlib.import_module("app.api._internal")


def test_r20_product_thresholds_lowered_after_dedup():
    """R20 — dedup 으로 줄어든 product voc 에 맞춰 threshold 갱신."""
    baselines = _internal._REGRESSION_PRODUCT_BASELINES
    assert baselines["GS22"]["threshold"] == 200
    assert baselines["GS25"]["threshold"] == 800
    assert baselines["GB3"]["threshold"] == 200
    assert baselines["GZF1"]["threshold"] == 250
    # GN7 은 dedup 영향 거의 없음 (300 유지)
    assert baselines["GN7"]["threshold"] == 300


def test_r20_baseline_present_for_all_products():
    """R20 baseline 이 모든 핵심 product 에 추가됐는지."""
    for code, meta in _internal._REGRESSION_PRODUCT_BASELINES.items():
        assert "baseline_r20" in meta, f"{code} missing baseline_r20"
        assert isinstance(meta["baseline_r20"], int)
        # baseline_r20 >= threshold (정상 운영 상태)
        assert meta["baseline_r20"] >= meta["threshold"], (
            f"{code}: baseline_r20={meta['baseline_r20']} < threshold={meta['threshold']}"
        )


def test_r20_voc_total_threshold_lowered():
    """R20 — voc_total threshold 150k → 110k (post-dedup 117,958)."""
    assert _internal._VOC_TOTAL_THRESHOLD == 110_000
    assert _internal._VOC_TOTAL_BASELINE_R20 == 117_958
    # baseline R12 (167,701) 는 추이 정보로 유지
    assert _internal._VOC_TOTAL_BASELINE_R12 == 167_701
    # R20 baseline 이 threshold 위에 있어야 OK
    assert _internal._VOC_TOTAL_BASELINE_R20 >= _internal._VOC_TOTAL_THRESHOLD


def test_r20_baseline_consistency_with_r12():
    """R20 baseline 이 R12 (dedup 전) 보다 낮거나 같아야 함 (dedup 으로 감소)."""
    # voc_total: R12 167k → R20 117k (dedup -32%)
    assert _internal._VOC_TOTAL_BASELINE_R20 < _internal._VOC_TOTAL_BASELINE_R12
    # products: dedup 영향 없음 (389 유지)
    assert _internal._PRODUCTS_BASELINE_R20 == _internal._PRODUCTS_BASELINE_R12
    # topics_filled: R13 백필 후 회복 (R12 25k → R20 104k)
    assert _internal._TOPICS_FILLED_BASELINE_R20 > _internal._TOPICS_FILLED_BASELINE_R12
    # HN linked %: ±0.5pp 미세 변동만 허용
    delta_pct = abs(
        _internal._HN_LINKED_PCT_BASELINE_R20 - _internal._HN_LINKED_PCT_BASELINE_R12
    )
    assert delta_pct < 0.5, delta_pct
