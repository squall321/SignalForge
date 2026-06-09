"""
LLM v3 프롬프트 + few-shot 빌더 + 강화 validate_response 단위 테스트 (P4.1 Track A).

검증 대상:
  1) SYSTEM_PROMPT_KO_V3 가 v2 보다 강화된 제약을 포함하고 PROMPT_VERSION 이 v3-fewshot-grounded.
  2) build_fewshot_examples 가 daily payload 에서 2개 예시(서로 다른 카테고리)를 생성.
  3) build_fewshot_examples 가 series payload 에서도 동작 (peak 인용).
  4) validate_response 강화 — 숫자만 인용 vs 숫자+키워드 인용 시 후자가 더 높은 점수.

실행:
    cd /home/koopark/claude/SignalForge/crawler
    ../.venv/bin/python -m pytest tests/test_llm_v3_prompts.py -v
    또는
    ../.venv/bin/python tests/test_llm_v3_prompts.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CRAWLER = os.path.dirname(HERE)
if CRAWLER not in sys.path:
    sys.path.insert(0, CRAWLER)

from insight import grounding as g  # noqa: E402
from insight import llm_provider as lp  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────
# 1) SYSTEM_PROMPT_KO_V3 + PROMPT_VERSION
# ──────────────────────────────────────────────────────────────────────────
def test_system_prompt_v3_is_stronger_than_v2():
    """v3 SYSTEM_PROMPT 가 v2 의 제약을 모두 포함 + few-shot/bold 규칙.

    R12 (2026-06-04): PROMPT_VERSION 은 v4-must-cite-5 로 승격됐으나
    SYSTEM_PROMPT_KO_V3 본문 자체는 그대로 유지 (must_cite 헤더는
    daily_insight 의 instructions 에서 동적 구성).
    """
    # v4 로 승격 — must-cite 5종 강제
    assert lp.PROMPT_VERSION.startswith("v4"), lp.PROMPT_VERSION
    assert lp.SYSTEM_PROMPT_KO is lp.SYSTEM_PROMPT_KO_V3, "alias 일치"
    v2 = lp.SYSTEM_PROMPT_KO_V2
    v3 = lp.SYSTEM_PROMPT_KO_V3
    # v2 핵심 규칙 보존
    assert "grounding 규칙" in v3
    assert "한자" in v3
    assert "데이터 없음" in v3
    # v3 신규 규칙
    assert "few-shot" in v3 or "예시" in v3, "few-shot 예시 블록"
    assert "**bold**" in v3 or "굵게" in v3, "bold 강제"
    assert "건수" in v3 or "비율(%)" in v3, "한국어 컬럼명 가이드"
    # v3 가 v2 보다 길어야 (강화)
    assert len(v3) > len(v2), f"v3 길이 {len(v3)} > v2 길이 {len(v2)}"
    print(f"  [PASS] SYSTEM_PROMPT_KO_V3 강화 + PROMPT_VERSION={lp.PROMPT_VERSION}")


# ──────────────────────────────────────────────────────────────────────────
# 2) build_fewshot_examples — daily payload
# ──────────────────────────────────────────────────────────────────────────
def test_build_fewshot_examples_daily_two_categories():
    """daily payload: total + by_product + by_category_neg → 2개 이상 예시."""
    payload = {
        "target_date": "2026-06-01",
        "total": 13487,
        "by_sentiment": {"negative": 1167, "positive": 3061, "neutral": 9259},
        "by_product": [
            {"code": "GS26U", "name_ko": "Galaxy S26 Ultra", "n": 480, "neg": 67, "pos": 120},
        ],
        "by_category_neg": [
            {"code": "price", "name_ko": "가격/가성비", "n": 188},
        ],
    }
    block = g.build_fewshot_examples(payload)
    assert block, "예시 블록 비어있지 않음"
    # 카테고리/제품 두 종류가 모두 포함되어야
    assert "GS26U" in block or "Galaxy S26 Ultra" in block, block
    # 표 수치 bold 인용
    assert "**13,487" in block or "**480" in block or "**188" in block, block
    # 두 개 이상의 bullet
    bullets = [l for l in block.splitlines() if l.lstrip().startswith("-")]
    assert len(bullets) >= 2, f"bullets={bullets}"
    print(f"  [PASS] build_fewshot_examples daily: {len(bullets)} bullets")


# ──────────────────────────────────────────────────────────────────────────
# 3) build_fewshot_examples — series payload
# ──────────────────────────────────────────────────────────────────────────
def test_build_fewshot_examples_series():
    """series payload 도 동작: peak 인용 + changepoint 인용."""
    payload = {
        "series": [
            {"date": "2026-05-30", "count": 14406},
            {"date": "2026-05-31", "count": 13592},
        ],
        "changepoints": [
            {"date": "2026-05-30", "metric": "count", "direction": "up", "magnitude": 4532.66}
        ],
    }
    block = g.build_fewshot_examples(payload)
    assert block
    assert "14,406" in block, "peak count 인용"
    assert "2026-05-30" in block, "peak date 인용"
    # changepoint 도 후보 — magnitude 또는 변곡점 키워드.
    assert "4,533" in block or "변곡" in block or "magnitude" in block.lower(), block
    print("  [PASS] build_fewshot_examples series: peak + changepoint")


# ──────────────────────────────────────────────────────────────────────────
# 4) validate_response 강화 — 숫자+키워드 인용 > 숫자만 인용
# ──────────────────────────────────────────────────────────────────────────
def test_validate_response_numbers_and_terms():
    """v3 강화: payload 의 키워드(code/name_ko) 인용도 점수에 반영."""
    payload = {
        "target_date": "2026-06-01",
        "total": 13487,
        "by_sentiment": {"negative": 1167, "positive": 3061, "neutral": 9259},
        "by_product": [
            {"code": "GS26U", "name_ko": "Galaxy S26 Ultra", "n": 480, "neg": 67, "pos": 120},
            {"code": "GS25U", "name_ko": "Galaxy S25 Ultra", "n": 320, "neg": 40, "pos": 100},
        ],
        "by_category_neg": [
            {"code": "price", "name_ko": "가격/가성비", "n": 188},
        ],
    }
    # A: 숫자만 인용 (키워드 미포함)
    text_nums_only = (
        "수집 총량은 13,487 건이며, 부정 1,167 건, 긍정 3,061 건, 중립 9,259 건이었습니다. "
        "상위 제품 건수는 480, 320 이었고 부정은 67, 40 이었습니다. "
        "최상위 카테고리 부정 건수는 188 이었습니다."
    )
    # B: 숫자 + 키워드 둘 다 인용
    text_full = (
        "수집 총량은 13,487 건이며, 부정 1,167 건, 긍정 3,061 건, 중립 9,259 건이었습니다. "
        "제품 TOP 은 Galaxy S26 Ultra(GS26U) 480 건(부정 67), Galaxy S25 Ultra(GS25U) 320 건(부정 40). "
        "부정 카테고리 1위는 가격/가성비(price) 188 건이었습니다."
    )
    s_nums = g.validate_response(text_nums_only, payload)
    s_full = g.validate_response(text_full, payload)
    assert s_full > s_nums, f"키워드 인용 추가 시 점수 상승 — full={s_full} nums={s_nums}"
    assert s_full >= 0.5, f"full grounded 응답 → 0.5 이상 — {s_full}"
    print(f"  [PASS] validate_response: nums={s_nums} < full={s_full}")


# ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        test_system_prompt_v3_is_stronger_than_v2,
        test_build_fewshot_examples_daily_two_categories,
        test_build_fewshot_examples_series,
        test_validate_response_numbers_and_terms,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  [FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  [ERROR] {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    total = len(tests)
    print(f"\n결과: {total - failed}/{total} 통과")
    sys.exit(0 if failed == 0 else 1)
