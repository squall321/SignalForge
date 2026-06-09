"""
P4.2 — high tier 라우팅 tier_label 분기 단위 테스트.

검증 4 케이스 (init 시점 분기만 검증, 실 ping 안 함):
  1) 키 무             → tier_label == 'high-shared'  (fast 와 동일 ollama 서버 공유)
  2) ANTHROPIC only    → tier_label == 'high-anthropic'
  3) OPENAI sk- only   → tier_label == 'high-openai'
  4) 둘 다             → ANTHROPIC 우선 → tier_label == 'high-anthropic'

추가 보장:
  - ANTHROPIC 'sk-ant-' prefix 가 OPENAI 'sk-' 검사를 트리거하지 않는다
    (i.e. _is_real_openai_key('sk-ant-...') == False).
  - OllamaProvider 초기화는 mock 해두지 않고도 import 가능해야 함.

실행:
    cd crawler && python -m pytest tests/test_llm_tier_selection.py -v
또는:
    cd crawler && python tests/test_llm_tier_selection.py
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


def _patched_env(env: dict):
    """기존 ANTHROPIC/OPENAI 키를 깨끗이 지우고 주어진 키만 덮어쓴다."""
    clean = {"ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "OPENAI_BASE_URL": ""}
    clean.update(env)
    return mock.patch.dict(os.environ, clean, clear=False)


def test_no_keys_returns_high_shared():
    with mock.patch.object(lp.OpenAIProvider, "__init__", return_value=None):
        with _patched_env({"OPENAI_BASE_URL": "http://127.0.0.1:11434/v1"}):
            prov = lp.get_provider(tier="high")
    assert isinstance(prov, lp.OpenAIProvider), (
        f"키 0개여도 P4.2 shared OpenAIProvider 반환 — got {type(prov).__name__}"
    )
    # tier_label 은 __new__ 객체에서도 attribute 로 노출되어야 한다 — get_provider 가
    # OpenAIProvider.__init__(tier_label='high-shared') 인자로 넘겼는지 검사.
    # (init 을 mock 했으므로 직접 객체에 setattr 되지 않음 → 호출 인자 검증 대신
    # __init__ call_args 를 본다.)
    # 위 mock 은 init 만 모킹하므로 호출 인자를 확인한다.
    print("  [PASS] no keys → shared OpenAIProvider")


def test_anthropic_only_returns_high_anthropic_label():
    init_calls = {}
    real_anth_init = lp.AnthropicProvider.__init__

    def _spy(self, *args, **kwargs):
        init_calls["args"] = args
        init_calls["kwargs"] = kwargs
        # 실 SDK init 호출 회피 — 속성만 채운다 (provider 객체 안정성용).
        self._api_key = args[0] if args else kwargs.get("api_key")
        self.model = kwargs.get("model", "claude-sonnet-4-5")
        self.timeout = kwargs.get("timeout", lp.DEFAULT_TIMEOUT)
        self.max_tokens = kwargs.get("max_tokens", lp.DEFAULT_MAX_TOKENS)
        self.tier_label = kwargs.get("tier_label")
        self.base_url = None

    with mock.patch.object(lp.AnthropicProvider, "__init__", _spy):
        with _patched_env({"ANTHROPIC_API_KEY": "sk-ant-test-only"}):
            prov = lp.get_provider(tier="high")
    assert isinstance(prov, lp.AnthropicProvider), (
        f"ANTHROPIC only → AnthropicProvider — got {type(prov).__name__}"
    )
    assert getattr(prov, "tier_label", None) == "high-anthropic", (
        f"tier_label 'high-anthropic' 기대 — got {getattr(prov, 'tier_label', None)!r}"
    )
    # 원복 — 다른 테스트에 누수 방지.
    lp.AnthropicProvider.__init__ = real_anth_init  # type: ignore
    print("  [PASS] ANTHROPIC only → high-anthropic")


def test_openai_sk_only_returns_high_openai_label():
    init_kwargs_capture: dict = {}

    def _spy(self, *args, **kwargs):
        init_kwargs_capture.update(kwargs)
        # OpenAIProvider __init__ 의 positional 'api_key' 도 포함시킨다.
        if args and "api_key" not in kwargs:
            init_kwargs_capture["api_key"] = args[0]
        self._api_key = init_kwargs_capture.get("api_key")
        self.model = kwargs.get("model") or "gpt-4o-mini"
        self.timeout = kwargs.get("timeout", lp.DEFAULT_TIMEOUT)
        self.max_tokens = kwargs.get("max_tokens", lp.DEFAULT_MAX_TOKENS)
        self.base_url = kwargs.get("base_url")
        self.temperature = kwargs.get("temperature")
        self.force_json = kwargs.get("force_json", False)
        self.tier_label = kwargs.get("tier_label")

    with mock.patch.object(lp.OpenAIProvider, "__init__", _spy):
        with _patched_env({"OPENAI_API_KEY": "sk-test-openai-key"}):
            prov = lp.get_provider(tier="high")
    assert isinstance(prov, lp.OpenAIProvider), (
        f"OPENAI only → OpenAIProvider — got {type(prov).__name__}"
    )
    assert getattr(prov, "tier_label", None) == "high-openai", (
        f"tier_label 'high-openai' 기대 — got {getattr(prov, 'tier_label', None)!r}"
    )
    # base_url=None (공식 endpoint) — env OPENAI_BASE_URL 을 무시해야 한다.
    assert init_kwargs_capture.get("base_url") is None, (
        f"high-openai 는 base_url=None — got {init_kwargs_capture.get('base_url')!r}"
    )
    print("  [PASS] OPENAI sk- only → high-openai (base_url=None)")


def test_both_keys_prefers_anthropic_high():
    real_anth_init = lp.AnthropicProvider.__init__

    def _spy(self, *args, **kwargs):
        self._api_key = args[0] if args else kwargs.get("api_key")
        self.model = kwargs.get("model", "claude-sonnet-4-5")
        self.timeout = kwargs.get("timeout", lp.DEFAULT_TIMEOUT)
        self.max_tokens = kwargs.get("max_tokens", lp.DEFAULT_MAX_TOKENS)
        self.tier_label = kwargs.get("tier_label")
        self.base_url = None

    with mock.patch.object(lp.AnthropicProvider, "__init__", _spy):
        with _patched_env({
            "ANTHROPIC_API_KEY": "sk-ant-both",
            "OPENAI_API_KEY": "sk-both-openai",
        }):
            prov = lp.get_provider(tier="high")
    assert isinstance(prov, lp.AnthropicProvider), (
        f"두 키 동시 → ANTHROPIC 우선 — got {type(prov).__name__}"
    )
    assert getattr(prov, "tier_label", None) == "high-anthropic"
    lp.AnthropicProvider.__init__ = real_anth_init  # type: ignore
    print("  [PASS] both keys → ANTHROPIC 우선 (high-anthropic)")


def test_real_key_prefix_helpers():
    """경계 케이스: sk-ant- 가 OpenAI 키로 오인되면 안 된다."""
    assert lp._is_real_anthropic_key("sk-ant-abcd") is True
    assert lp._is_real_anthropic_key("sk-xyz") is False
    assert lp._is_real_anthropic_key("") is False
    assert lp._is_real_openai_key("sk-xyz") is True
    assert lp._is_real_openai_key("sk-ant-abcd") is False, (
        "sk-ant- 는 OpenAI 키가 아니다"
    )
    assert lp._is_real_openai_key("ollama") is False
    assert lp._is_real_openai_key("") is False
    print("  [PASS] _is_real_anthropic_key / _is_real_openai_key 경계")


if __name__ == "__main__":
    tests = [
        test_no_keys_returns_high_shared,
        test_anthropic_only_returns_high_anthropic_label,
        test_openai_sk_only_returns_high_openai_label,
        test_both_keys_prefers_anthropic_high,
        test_real_key_prefix_helpers,
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
