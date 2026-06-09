"""
Insight 모듈 단위 테스트.

실행:
    cd /home/koopark/claude/SignalForge/crawler
    ../.venv/bin/python -m pytest tests/test_insight.py -v
    또는
    ../.venv/bin/python tests/test_insight.py
"""
from __future__ import annotations

import os
import sys
import types
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight import daily_insight as di
from insight import llm_provider as lp


# ────────────────────────────────────────────────────────────────────────────
# 1) get_provider — P4.1: 키 없어도 shared ollama 폴백
# ────────────────────────────────────────────────────────────────────────────
def test_get_provider_no_keys_falls_back_to_shared_ollama():
    """P4.1: high tier 가 shared ollama 로 폴백하면 기본 호출도 None 이 아니다.

    (P3.x 까지: 키 없으면 None. P4.1: high 가 shared OpenAIProvider 로 폴백.)
    """
    with mock.patch.object(lp.OpenAIProvider, "__init__", return_value=None):
        with mock.patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": ""},
            clear=False,
        ):
            prov = lp.get_provider()
        assert isinstance(prov, lp.OpenAIProvider), (
            f"P4.1: shared ollama 폴백 — got {type(prov).__name__}"
        )
    print("  [PASS] get_provider: 키 없음 → shared OpenAIProvider")


# ────────────────────────────────────────────────────────────────────────────
# 2) get_provider — Anthropic 키만 있으면 Anthropic 선택
# ────────────────────────────────────────────────────────────────────────────
def test_get_provider_prefers_anthropic():
    # SDK 호출은 막아두고 (실제 네트워크 없음) factory 만 검증
    with mock.patch.object(lp.AnthropicProvider, "__init__", return_value=None) as init:
        with mock.patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "sk-ant-xxx", "OPENAI_API_KEY": "sk-oai-yyy"},
            clear=False,
        ):
            prov = lp.get_provider()
            assert isinstance(prov, lp.AnthropicProvider), \
                f"Anthropic 우선 — got {type(prov).__name__}"
            init.assert_called_once()
    print("  [PASS] get_provider: 둘 다 있으면 Anthropic 우선")


# ────────────────────────────────────────────────────────────────────────────
# 3) get_provider — Anthropic 빈 키 + OpenAI 키만 있으면 OpenAI 선택
# ────────────────────────────────────────────────────────────────────────────
def test_get_provider_falls_back_to_openai():
    with mock.patch.object(lp.OpenAIProvider, "__init__", return_value=None) as init:
        with mock.patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "sk-oai-yyy"},
            clear=False,
        ):
            prov = lp.get_provider()
            assert isinstance(prov, lp.OpenAIProvider), \
                f"OpenAI 폴백 — got {type(prov).__name__}"
            init.assert_called_once()
    print("  [PASS] get_provider: Anthropic 없으면 OpenAI 폴백")


# ────────────────────────────────────────────────────────────────────────────
# 4) AnthropicProvider.summarize — SDK 응답 mock → 텍스트 합치기
# ────────────────────────────────────────────────────────────────────────────
def test_anthropic_summarize_concatenates_text_blocks():
    # __init__ 자체를 mock 하여 client 만 주입
    prov = lp.AnthropicProvider.__new__(lp.AnthropicProvider)
    prov.model = "test"
    prov.max_tokens = 100
    prov.timeout = 5

    block_a = types.SimpleNamespace(text="첫 번째 블록. ")
    block_b = types.SimpleNamespace(text="두 번째 블록.")
    resp = types.SimpleNamespace(content=[block_a, block_b])

    prov._client = mock.MagicMock()
    prov._client.messages.create.return_value = resp

    out = prov.summarize("prompt")
    assert out == "첫 번째 블록. 두 번째 블록.", f"text 합성 결과: {out!r}"
    print("  [PASS] AnthropicProvider: 다중 text 블록 합성")


# ────────────────────────────────────────────────────────────────────────────
# 5) AnthropicProvider.summarize — 예외 시 None
# ────────────────────────────────────────────────────────────────────────────
def test_anthropic_summarize_returns_none_on_error():
    prov = lp.AnthropicProvider.__new__(lp.AnthropicProvider)
    prov.model = "test"
    prov.max_tokens = 100
    prov.timeout = 5
    prov._client = mock.MagicMock()
    prov._client.messages.create.side_effect = RuntimeError("API down")

    out = prov.summarize("prompt")
    assert out is None, "예외 전파되면 안 됨"
    print("  [PASS] AnthropicProvider: 예외 → None")


# ────────────────────────────────────────────────────────────────────────────
# 6) OpenAIProvider.summarize — choices[0].message.content
# ────────────────────────────────────────────────────────────────────────────
def test_openai_summarize_extracts_content():
    prov = lp.OpenAIProvider.__new__(lp.OpenAIProvider)
    prov.model = "test"
    prov.max_tokens = 100
    prov.timeout = 5
    msg = types.SimpleNamespace(content="OpenAI 응답 텍스트")
    choice = types.SimpleNamespace(message=msg)
    resp = types.SimpleNamespace(choices=[choice])
    prov._client = mock.MagicMock()
    prov._client.chat.completions.create.return_value = resp

    out = prov.summarize("prompt")
    assert out == "OpenAI 응답 텍스트", f"OpenAI 응답: {out!r}"
    print("  [PASS] OpenAIProvider: choices.message.content 추출")


# ────────────────────────────────────────────────────────────────────────────
# 7) render_raw_summary — 빈 데이터에서도 깨지지 않음
# ────────────────────────────────────────────────────────────────────────────
def test_render_raw_summary_empty():
    m = di.DailyMetrics(target_date=date(2026, 5, 31), total=0)
    out = di.render_raw_summary(m)
    assert "2026-05-31" in out
    assert "수집 총량" in out
    assert "**0**" in out and "건" in out
    print("  [PASS] render_raw_summary: 0건도 안전")


# ────────────────────────────────────────────────────────────────────────────
# 8) render_raw_summary — 풍부한 데이터 포함
# ────────────────────────────────────────────────────────────────────────────
def test_render_raw_summary_with_data():
    m = di.DailyMetrics(
        target_date=date(2026, 5, 31),
        total=13592,
        by_sentiment={"positive": 5000, "negative": 4000, "neutral": 4592},
        sentiment_score_avg=0.123,
        avg_engagement=42.5,
        by_category=[
            {"code": "price", "name_ko": "가격/가성비", "n": 1141},
            {"code": "display", "name_ko": "디스플레이", "n": 829},
        ],
        by_category_neg=[
            {"code": "performance", "name_ko": "성능/발열", "n": 161},
        ],
        by_product=[
            {"code": "S25U", "name_ko": "Galaxy S25 Ultra", "n": 300, "neg": 60, "pos": 150},
        ],
        by_platform=[
            {"code": "reddit", "name": "Reddit", "region": "global", "n": 500, "neg": 80},
        ],
        by_country=[{"cc": "US", "n": 4000}],
        new_products_today=[{"code": "ZF8", "name_ko": "Galaxy Z Fold8"}],
        top_negative=[
            {"text": "배터리가 너무 빨리 닳습니다", "product": "S25U", "platform": "reddit", "score": -0.6},
        ],
        top_positive=[
            {"text": "카메라가 진짜 좋네요", "product": "S25U", "platform": "reddit", "score": 0.8},
        ],
    )
    out = di.render_raw_summary(m)
    # 핵심 필드 노출 검증
    assert "13,592" in out, "총량 포맷"
    assert "가격/가성비" in out
    assert "Galaxy S25 Ultra" in out
    assert "부정률 20.0%" in out, "부정률 계산"
    assert "Galaxy Z Fold8" in out, "신규 제품"
    assert "배터리가 너무 빨리 닳습니다" in out
    assert "카메라가 진짜 좋네요" in out
    print("  [PASS] render_raw_summary: 풍부한 데이터 직렬화")


# ────────────────────────────────────────────────────────────────────────────
# 9) build_prompt — raw 요약 + 작성 지시 모두 포함
# ────────────────────────────────────────────────────────────────────────────
def test_build_prompt_contains_directives():
    m = di.DailyMetrics(target_date=date(2026, 5, 31), total=13592)
    p = di.build_prompt(m)
    assert "13,592" in p
    assert "오늘의 SignalForge 인사이트" in p
    assert "권장 액션" in p
    assert "8-12 문단" in p or "8-12" in p
    print("  [PASS] build_prompt: 지시문 포함")


# ────────────────────────────────────────────────────────────────────────────
# 10) render_report — LLM 출력 없으면 fallback 안내 포함
# ────────────────────────────────────────────────────────────────────────────
def test_render_report_fallback_when_no_llm():
    m = di.DailyMetrics(target_date=date(2026, 5, 31), total=100)
    out = di.render_report(m, None)
    assert "LLM 분석은 생략" in out
    assert "Raw 요약" in out
    print("  [PASS] render_report: LLM 없으면 안내 + raw")


def test_render_report_with_llm():
    m = di.DailyMetrics(target_date=date(2026, 5, 31), total=100)
    out = di.render_report(m, "LLM이 작성한 인사이트 본문.")
    assert "LLM이 작성한 인사이트 본문" in out
    assert "Raw 요약" in out
    print("  [PASS] render_report: LLM 결과 통합")


# ────────────────────────────────────────────────────────────────────────────
# 11) _resolve_database_url — env 조합
# ────────────────────────────────────────────────────────────────────────────
def test_resolve_database_url_from_components():
    with mock.patch.dict(
        os.environ,
        {
            "DATABASE_URL": "",
            "POSTGRES_HOST": "h1",
            "POSTGRES_PORT": "5439",
            "POSTGRES_USER": "u1",
            "POSTGRES_PASSWORD": "p1",
            "POSTGRES_DB": "d1",
        },
        clear=False,
    ):
        url = di._resolve_database_url()
    assert url == "postgresql://u1:p1@h1:5439/d1", url
    print("  [PASS] _resolve_database_url: 컴포넌트 조합")


def test_resolve_database_url_direct_override():
    with mock.patch.dict(
        os.environ, {"DATABASE_URL": "postgresql://x:y@z:1/q"}, clear=False
    ):
        url = di._resolve_database_url()
    assert url == "postgresql://x:y@z:1/q"
    print("  [PASS] _resolve_database_url: DATABASE_URL 우선")


# ────────────────────────────────────────────────────────────────────────────
# 12) _short — 문장 자르기
# ────────────────────────────────────────────────────────────────────────────
def test_short_truncation():
    assert di._short(None) == ""
    assert di._short("abc") == "abc"
    s = "x" * 400
    out = di._short(s, maxlen=100)
    assert len(out) == 100 and out.endswith("…")
    # 다중 공백 정리
    assert di._short("a   b\n c") == "a b c"
    print("  [PASS] _short: 길이 제한 + 공백 정규화")


# ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        test_get_provider_no_keys_falls_back_to_shared_ollama,
        test_get_provider_prefers_anthropic,
        test_get_provider_falls_back_to_openai,
        test_anthropic_summarize_concatenates_text_blocks,
        test_anthropic_summarize_returns_none_on_error,
        test_openai_summarize_extracts_content,
        test_render_raw_summary_empty,
        test_render_raw_summary_with_data,
        test_build_prompt_contains_directives,
        test_render_report_fallback_when_no_llm,
        test_render_report_with_llm,
        test_resolve_database_url_from_components,
        test_resolve_database_url_direct_override,
        test_short_truncation,
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
