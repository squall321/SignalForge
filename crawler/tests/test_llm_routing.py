"""
LLM_QUALITY_TIER 라우팅 단위 테스트 (P3.6 트랙 B).

검증:
  1) tier='high' + ANTHROPIC_API_KEY 있음 → AnthropicProvider
  2) tier='high' + ANTHROPIC 없음 + OPENAI 있음 → OpenAIProvider
  3) tier='high' + 키 둘 다 없음 → OllamaProvider 폴백

실행:
    cd crawler && python -m pytest tests/test_llm_routing.py -v
    또는
    cd crawler && python tests/test_llm_routing.py
"""
from __future__ import annotations

import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
CRAWLER_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, CRAWLER_ROOT)

from insight import llm_provider as lp  # noqa: E402


def test_tier_high_prefers_anthropic_when_key_present():
    with mock.patch.object(lp.AnthropicProvider, "__init__", return_value=None):
        with mock.patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-ant-test",
                "OPENAI_API_KEY": "sk-oai-test",
                "LLM_QUALITY_TIER": "high",
            },
            clear=False,
        ):
            prov = lp.get_provider(tier="high")
    assert isinstance(prov, lp.AnthropicProvider), (
        f"high tier 는 Anthropic 우선 — got {type(prov).__name__}"
    )
    print("  [PASS] high tier: Anthropic 우선")


def test_tier_high_falls_back_to_openai_when_no_anthropic():
    with mock.patch.object(lp.OpenAIProvider, "__init__", return_value=None):
        with mock.patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "sk-oai-test",
                "LLM_QUALITY_TIER": "high",
            },
            clear=False,
        ):
            prov = lp.get_provider(tier="high")
    assert isinstance(prov, lp.OpenAIProvider), (
        f"Anthropic 없으면 OpenAI 폴백 — got {type(prov).__name__}"
    )
    print("  [PASS] high tier: Anthropic 없으면 OpenAI")


def test_tier_high_returns_shared_provider_when_no_cloud_keys():
    """P4.1 신규 계약: 클라우드 키 없을 때 high 는 fast 와 동일 ollama 서버를
    공유하는 OpenAIProvider 를 반환한다 (tier_label='high-shared').

    (P3.7 에서는 None 을 반환했음 — 동작 변경.)
    """
    with mock.patch.object(lp.OpenAIProvider, "__init__", return_value=None):
        with mock.patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
                "LLM_QUALITY_TIER": "high",
                "OPENAI_BASE_URL": "http://127.0.0.1:11434/v1",
            },
            clear=False,
        ):
            prov = lp.get_provider(tier="high")
    assert isinstance(prov, lp.OpenAIProvider), (
        f"클라우드 키 0개여도 P4.1 shared OpenAIProvider 반환 — got {type(prov).__name__}"
    )
    print("  [PASS] high tier: 클라우드 키 없으면 shared OpenAIProvider")


if __name__ == "__main__":
    tests = [
        test_tier_high_prefers_anthropic_when_key_present,
        test_tier_high_falls_back_to_openai_when_no_anthropic,
        test_tier_high_returns_shared_provider_when_no_cloud_keys,
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
