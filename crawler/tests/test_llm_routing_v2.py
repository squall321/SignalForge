"""
LLM tier 라우팅 v2 — P3.7 (auto/fast/high + None 케이스) 단위 테스트.

검증:
  1) tier='high' + cloud 키 있음        → AnthropicProvider
  2) tier='high' + cloud 키 0           → None (폴백 안 함, 호출자 책임)
  3) tier='fast' + ollama dummy key     → OpenAIProvider (base_url=Ollama 라우팅)
  4) tier='auto' + cloud 키 0 + ollama  → OpenAIProvider (auto → fast 폴백)

실행:
    cd crawler && python -m pytest tests/test_llm_routing_v2.py -v
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


def test_high_with_anthropic_key_returns_anthropic():
    with mock.patch.object(lp.AnthropicProvider, "__init__", return_value=None):
        with mock.patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "sk-ant-test", "OPENAI_API_KEY": ""},
            clear=False,
        ):
            prov = lp.get_provider(tier="high")
    assert isinstance(prov, lp.AnthropicProvider)
    print("  [PASS] high + ANTHROPIC → AnthropicProvider")


def test_high_with_no_cloud_keys_returns_shared_provider():
    """P4.1: high 는 클라우드 키 없을 때 fast 와 동일 ollama 서버를 공유한다.

    이전(P3.7): high → None (호출자가 fast 폴백).
    이후(P4.1): high → OpenAIProvider(ollama base_url, qwen2.5:7b, max_tokens=8192, temp=0.0).
              tier_label='high-shared'.
    """
    with mock.patch.object(lp.OpenAIProvider, "__init__", return_value=None):
        with mock.patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "ollama",
                "OPENAI_BASE_URL": "http://127.0.0.1:11434/v1",
            },
            clear=False,
        ):
            prov = lp.get_provider(tier="high")
    assert isinstance(prov, lp.OpenAIProvider), (
        f"P4.1: high 는 클라우드 키 없어도 shared OpenAIProvider 반환 — got {type(prov).__name__}"
    )
    print("  [PASS] high + no cloud key → shared OpenAIProvider")


def test_fast_with_ollama_dummy_returns_openai():
    """fast 는 ollama dummy 도 인정 (현행 prod 설정)."""
    with mock.patch.object(lp.OpenAIProvider, "__init__", return_value=None):
        with mock.patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "ollama",
                "OPENAI_BASE_URL": "http://127.0.0.1:11434/v1",
            },
            clear=False,
        ):
            prov = lp.get_provider(tier="fast")
    assert isinstance(prov, lp.OpenAIProvider)
    print("  [PASS] fast + ollama dummy → OpenAIProvider")


def test_auto_falls_back_to_fast_when_no_high_keys():
    """auto = high 시도 → 실패 시 fast 폴백."""
    with mock.patch.object(lp.OpenAIProvider, "__init__", return_value=None):
        with mock.patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "ollama",  # high 거부, fast 통과
                "OPENAI_BASE_URL": "http://127.0.0.1:11434/v1",
            },
            clear=False,
        ):
            prov = lp.get_provider(tier="auto")
    assert isinstance(prov, lp.OpenAIProvider), (
        f"auto + ollama dummy 는 fast 폴백 → OpenAIProvider 여야 — "
        f"got {type(prov).__name__}"
    )
    print("  [PASS] auto + ollama dummy → fast 폴백 → OpenAIProvider")


if __name__ == "__main__":
    tests = [
        test_high_with_anthropic_key_returns_anthropic,
        test_high_with_no_cloud_keys_returns_shared_provider,
        test_fast_with_ollama_dummy_returns_openai,
        test_auto_falls_back_to_fast_when_no_high_keys,
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
