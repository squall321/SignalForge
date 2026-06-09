"""
grounding 모듈 단위 테스트.

실행:
    cd /home/koopark/claude/SignalForge/crawler
    ../.venv/bin/python -m pytest tests/test_grounding.py -v
    또는
    ../.venv/bin/python tests/test_grounding.py
"""
from __future__ import annotations

import os
import sys
import types
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight import grounding as g
from insight import llm_provider as lp


# ──────────────────────────────────────────────────────────────────────────
# metrics_to_markdown — 3 케이스
# ──────────────────────────────────────────────────────────────────────────
def test_metrics_to_markdown_series_payload():
    payload = {
        "series": [
            {"date": "2026-05-30", "count": 14406, "sent_avg": 0.011,
             "neg_rate": 7.27, "pos_rate": 9.19},
            {"date": "2026-05-31", "count": 13592, "sent_avg": 0.066,
             "neg_rate": 8.48, "pos_rate": 18.38},
        ],
        "events": [{"date": "2026-05-30", "type": "release",
                    "title": "Galaxy S25 출시", "product_code": "S25"}],
        "changepoints": [{"date": "2026-05-30", "metric": "count",
                          "direction": "up", "magnitude": 4532.66}],
        "meta": {"from_date": "2026-05-25", "to_date": "2026-05-31"},
    }
    md = g.metrics_to_markdown(payload, schema_desc="series desc")
    assert "series desc" in md, "schema_desc 표시"
    assert "14,406" in md, "peak count 포함"
    assert "2026-05-30" in md
    assert "변곡점" in md, "changepoints 표 헤더"
    assert "### 시계열" in md, "시계열 섹션"
    assert "Galaxy S25 출시" in md
    print("  [PASS] metrics_to_markdown: series payload")


def test_metrics_to_markdown_daily_payload():
    payload = {
        "target_date": "2026-05-31",
        "total": 13592,
        "sentiment_score_avg": 0.123,
        "by_sentiment": {"positive": 5000, "negative": 4000, "neutral": 4592},
        "by_product": [
            {"code": "S25U", "name_ko": "Galaxy S25 Ultra",
             "n": 300, "neg": 60, "pos": 150}
        ],
        "by_category": [
            {"code": "price", "name_ko": "가격/가성비", "n": 1141}
        ],
        "top_negative": [
            {"product": "S25U", "platform": "reddit", "score": -0.6,
             "text": "배터리가 너무 빨리 닳습니다"}
        ],
    }
    md = g.metrics_to_markdown(payload)
    assert "13,592" in md
    assert "Galaxy S25 Ultra" in md
    assert "가격/가성비" in md
    assert "배터리가 너무 빨리 닳습니다" in md
    # 표 형식인지
    assert "|" in md and "---" in md
    print("  [PASS] metrics_to_markdown: daily payload")


def test_metrics_to_markdown_empty_dict():
    md = g.metrics_to_markdown({})
    # 빈 dict 는 unknown shape → key 목록 (비어있음 → '(없음)' 또는 placeholder)
    assert isinstance(md, str)
    assert "key" in md or "없음" in md
    print("  [PASS] metrics_to_markdown: 빈 dict 안전")


def test_metrics_to_markdown_non_dict():
    out = g.metrics_to_markdown("not a dict")  # type: ignore[arg-type]
    assert "형식 오류" in out
    print("  [PASS] metrics_to_markdown: 비-dict 보호")


# ──────────────────────────────────────────────────────────────────────────
# extract_key_numbers — 3 케이스
# ──────────────────────────────────────────────────────────────────────────
def test_extract_key_numbers_series():
    payload = {
        "series": [
            {"date": "2026-05-30", "count": 14406, "sent_avg": 0.0},
            {"date": "2026-05-31", "count": 13592, "sent_avg": 0.0},
        ],
    }
    nums = g.extract_key_numbers(payload)
    # total=27998, peak=14406, trough=13592, peak_date=2026-05-30
    assert "27,998" in nums or "27998" in nums
    assert "14,406" in nums or "14406" in nums
    assert "2026-05-30" in nums
    print("  [PASS] extract_key_numbers: series total/peak/date")


def test_extract_key_numbers_daily():
    payload = {
        "total": 13592,
        "by_sentiment": {"positive": 5000, "negative": 4000},
        "by_product": [
            {"code": "S25U", "n": 300},
            {"code": "Z7F", "n": 200},
        ],
    }
    nums = g.extract_key_numbers(payload)
    assert "13,592" in nums or "13592" in nums
    assert "5,000" in nums or "5000" in nums
    assert "300" in nums
    print("  [PASS] extract_key_numbers: daily metrics")


def test_extract_key_numbers_empty():
    assert g.extract_key_numbers({}) == []
    assert g.extract_key_numbers(None) == []  # type: ignore[arg-type]
    print("  [PASS] extract_key_numbers: 빈 입력")


# ──────────────────────────────────────────────────────────────────────────
# validate_response — 3 케이스
# ──────────────────────────────────────────────────────────────────────────
def test_validate_response_good_grounding():
    payload = {
        "series": [
            {"date": "2026-05-30", "count": 14406, "sent_avg": 0.0},
            {"date": "2026-05-31", "count": 13592, "sent_avg": 0.0},
        ],
    }
    text = (
        "2026-05-30 에 14,406 건으로 정점을 기록했고 "
        "다음날은 13,592 건으로 줄었습니다. 총량은 27,998 건이었습니다. "
        "전반적으로 수량이 안정화되는 추세를 보이고 있습니다."
    )
    score = g.validate_response(text, payload)
    assert score >= 0.5, f"잘 grounded 된 응답 — score={score}"
    print(f"  [PASS] validate_response: good grounding (score={score})")


def test_validate_response_bad_grounding():
    payload = {
        "series": [
            {"date": "2026-05-30", "count": 14406, "sent_avg": 0.0},
            {"date": "2026-05-31", "count": 13592, "sent_avg": 0.0},
        ],
    }
    # payload 수치를 인용하지 않은 응답 — 환각
    text = (
        "전반적으로 안정적인 흐름이 관측되었고 특별한 변화는 없었습니다. "
        "사용자 반응은 미온적입니다. 모니터링을 지속해야 합니다."
    )
    score = g.validate_response(text, payload)
    assert score < 0.5, f"grounding 부족한 응답 — score={score}"
    print(f"  [PASS] validate_response: bad grounding (score={score})")


def test_validate_response_hanzi_penalty():
    payload = {"series": [{"date": "2026-05-30", "count": 14406, "sent_avg": 0.0}]}
    text_clean = "2026-05-30 에 14,406 건이 수집되었습니다. 충분한 데이터 입니다."
    text_hanzi = "2026-05-30 에 14,406 件 收集 되었습니다. 데이터 분석 결과 입니다."
    s1 = g.validate_response(text_clean, payload)
    s2 = g.validate_response(text_hanzi, payload)
    assert s2 < s1, f"한자 포함 시 페널티 적용 — clean={s1} hanzi={s2}"
    print(f"  [PASS] validate_response: hanzi penalty (clean={s1}, hanzi={s2})")


def test_validate_response_empty_text():
    payload = {"series": [{"date": "2026-05-30", "count": 14406, "sent_avg": 0.0}]}
    assert g.validate_response("", payload) == 0.0
    assert g.validate_response("ㅁ", payload) == 0.0
    print("  [PASS] validate_response: 빈/너무 짧은 입력 → 0")


def test_contains_hanzi():
    assert g.contains_hanzi("分析") is True
    assert g.contains_hanzi("今天") is True
    assert g.contains_hanzi("안녕하세요 Galaxy S25") is False
    assert g.contains_hanzi("") is False
    print("  [PASS] contains_hanzi: 검출/비검출")


# ──────────────────────────────────────────────────────────────────────────
# summarize_json — provider 통합 (mock)
# ──────────────────────────────────────────────────────────────────────────
def test_summarize_json_uses_markdown_table():
    """provider.summarize_json 이 metrics_to_markdown 결과를 prompt 로 전달하는지."""
    prov = lp.OpenAIProvider.__new__(lp.OpenAIProvider)
    prov.model = "test"
    prov.max_tokens = 100
    prov.timeout = 5
    prov._client = mock.MagicMock()
    msg = types.SimpleNamespace(content="2026-05-30 에 14,406 건 분석 완료.")
    choice = types.SimpleNamespace(message=msg)
    prov._client.chat.completions.create.return_value = types.SimpleNamespace(
        choices=[choice]
    )

    payload = {
        "series": [
            {"date": "2026-05-30", "count": 14406, "sent_avg": 0.0,
             "neg_rate": 0.0, "pos_rate": 0.0}
        ],
        "meta": {"product": "S25U"},
    }
    out = prov.summarize_json(payload, "테스트 desc", "테스트 지시")
    assert out and "14,406" in out
    # prompt 에 markdown 표(| ... |)가 포함됐는지
    called_kwargs = prov._client.chat.completions.create.call_args.kwargs
    user_msg = next(
        m for m in called_kwargs["messages"] if m["role"] == "user"
    )
    # P4.1: 표 헤더가 한국어 별칭과 함께 표기됨 (예: '| date(일자) |').
    content = user_msg["content"]
    assert (
        "| date" in content or "| count" in content or "| date(일자)" in content
    ), content[:200]
    assert "14,406" in content
    assert "테스트 지시" in content
    print("  [PASS] summarize_json: markdown table 이 prompt 에 포함")


def test_summarize_json_retries_on_hanzi():
    """첫 응답에 한자 → 두 번째 호출로 재요청."""
    prov = lp.OpenAIProvider.__new__(lp.OpenAIProvider)
    prov.model = "test"
    prov.max_tokens = 100
    prov.timeout = 5
    prov._client = mock.MagicMock()

    # 1차: 한자 포함, 2차: 깨끗
    first = types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="今天 14,406 건 분석.")
        )]
    )
    second = types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="오늘 14,406 건 분석.")
        )]
    )
    prov._client.chat.completions.create.side_effect = [first, second]

    payload = {"series": [{"date": "2026-05-30", "count": 14406, "sent_avg": 0.0,
                           "neg_rate": 0.0, "pos_rate": 0.0}]}
    out = prov.summarize_json(payload, "d", "i")
    assert out == "오늘 14,406 건 분석.", f"재요청 결과 채택: {out!r}"
    assert prov._client.chat.completions.create.call_count == 2
    print("  [PASS] summarize_json: 한자 감지 → 1회 재요청")


# ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        test_metrics_to_markdown_series_payload,
        test_metrics_to_markdown_daily_payload,
        test_metrics_to_markdown_empty_dict,
        test_metrics_to_markdown_non_dict,
        test_extract_key_numbers_series,
        test_extract_key_numbers_daily,
        test_extract_key_numbers_empty,
        test_validate_response_good_grounding,
        test_validate_response_bad_grounding,
        test_validate_response_hanzi_penalty,
        test_validate_response_empty_text,
        test_contains_hanzi,
        test_summarize_json_uses_markdown_table,
        test_summarize_json_retries_on_hanzi,
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
