"""test_yc_noise — Track B (R7): Y Combinator batch 및 일반 단어 노이즈 필터.

목적: "YC S20" / "YC W11" / "YC F25" 같은 Y Combinator batch 표기가
Galaxy 모델 키 ("s20", "f25", ...) 와 충돌해 잘못 매칭되는 것 방지.

검증 대상:
1) YC batch 단독: 매칭 안 됨 (None)
2) Galaxy 정상 키워드와 공존: 정상 매칭 (YC 마스킹 후에도 잔존 키 유효)
3) notebook (laptop) 단어: Galaxy Note 와 충돌 안 함
4) 'app store note' 일반 구문: 매칭 안 됨
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.relink_products import match_product_code, _mask_noise, normalize  # noqa: E402


def test_yc_batch_alone_does_not_match():
    """'YC S20' 단독 표기는 GS20 으로 매칭되지 않아야 한다."""
    # 현재 MODEL_MAP 에 's20' 단독 키가 없어도, R7 확장 시 추가될 것을 대비한 방어 테스트.
    # 직접 마스킹 결과를 확인해 's20' 토큰이 사라졌는지 검증.
    masked = _mask_noise(normalize("Launch HN: Foo (YC S20) – AI inference"))
    assert "s20" not in masked, f"YC S20 가 마스킹되지 않음: {masked!r}"

    masked2 = _mask_noise(normalize("Cactus (YC S25) – AI inference"))
    assert "s25" not in masked2, f"YC S25 가 마스킹되지 않음: {masked2!r}"

    masked3 = _mask_noise(normalize("Gecko Security (YC F24) – Vulnerabilities"))
    assert "f24" not in masked3, f"YC F24 가 마스킹되지 않음: {masked3!r}"

    masked4 = _mask_noise(normalize("AppHarbor (YC W11) with Twilio"))
    assert "w11" not in masked4, f"YC W11 가 마스킹되지 않음: {masked4!r}"


def test_yc_with_real_galaxy_term_still_matches():
    """YC batch 와 Galaxy 정상 키워드가 같이 있으면 Galaxy 매칭은 유지."""
    # YC S25 는 마스킹되지만 'Galaxy S21' 명시 표현은 GS21 로 정상 매칭.
    code = match_product_code(
        "Launch HN: Cactus (YC S25) – AI inference on smartphones. "
        "16-20 toks/sec on Pixel 6a / Galaxy S21 / iPhone 11 Pro"
    )
    assert code == "GS21", f"Galaxy S21 명시 시 GS21 매칭 기대, 실제 {code!r}"


def test_notebook_does_not_match_note():
    """'notebook' / 'notebook-style' 은 Galaxy Note 시리즈와 매칭 금지."""
    # 'notebook-style foldable' 만 있고 다른 Galaxy 단서 없음 → None.
    assert match_product_code("Razr Fold battery test, notebook-style design") is None
    assert match_product_code("My notebook is heavy") is None
    # notebooks 복수형도 마스킹.
    masked = _mask_noise(normalize("All my notebooks broke"))
    assert "note" not in masked


def test_app_store_note_excluded():
    """'app store note' 일반 구문은 Note 시리즈로 매칭되지 않아야 한다."""
    masked = _mask_noise(normalize("Check the App Store note on this app"))
    # 'note' 가 마스킹되어 GN 시리즈로 매칭 안 됨.
    assert "note" not in masked
    assert match_product_code("Check the App Store note today") is None


def test_yc_does_not_break_legacy_note7_match():
    """YC 마스킹이 다른 정상 Note 7 매칭에 영향을 주면 안 된다."""
    # 'Note 7' 가 있고 YC 표기가 없으면 정상적으로 GN7 매칭.
    assert match_product_code("My old Note 7 still works") == "GN7"
    # YC 가 있어도 Note 7 자체는 보존.
    assert match_product_code("YC S23 demo using Note 7 battery") == "GN7"


def test_mask_preserves_length():
    """마스킹은 길이를 보존 (디버깅/오프셋 안정성)."""
    src = normalize("Launch HN: Cactus (YC S25) – AI")
    masked = _mask_noise(src)
    assert len(masked) == len(src)
