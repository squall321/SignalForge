"""external tier (OpenAI 호환 외부 서버) 분기 단위 테스트.

키 미입력 → None / 키 입력 → OpenAIProvider tier_label="external:<model>".
auto tier 시 external → high → fast 폴백 순서.
"""
import os
import sys
import pytest
from importlib import reload

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _clear(monkeypatch):
    for k in (
        "EXTERNAL_API_KEY", "EXTERNAL_BASE_URL", "EXTERNAL_MODEL",
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL",
        "OPENAI_HIGH_MODEL_SHARED", "OPENAI_MODEL", "LLM_QUALITY_TIER",
    ):
        monkeypatch.delenv(k, raising=False)


def test_external_keys_missing_returns_none(monkeypatch):
    _clear(monkeypatch)
    import insight.llm_provider as L
    reload(L)
    assert L.get_provider(tier="external") is None


def test_external_keys_full_returns_provider(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("EXTERNAL_API_KEY", "sk-test-123")
    monkeypatch.setenv("EXTERNAL_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("EXTERNAL_MODEL", "test-model")
    import insight.llm_provider as L
    reload(L)
    p = L.get_provider(tier="external")
    assert p is not None
    assert p.tier_label == "external:test-model"


def test_external_partial_keys_returns_none(monkeypatch):
    """일부만 입력 (base_url 누락) → None."""
    _clear(monkeypatch)
    monkeypatch.setenv("EXTERNAL_API_KEY", "sk-test-123")
    monkeypatch.setenv("EXTERNAL_MODEL", "test-model")
    # EXTERNAL_BASE_URL 미설정
    import insight.llm_provider as L
    reload(L)
    assert L.get_provider(tier="external") is None


def test_auto_tier_external_first(monkeypatch):
    """auto: external 키 있으면 우선."""
    _clear(monkeypatch)
    monkeypatch.setenv("EXTERNAL_API_KEY", "sk-test-123")
    monkeypatch.setenv("EXTERNAL_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("EXTERNAL_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    monkeypatch.setenv("OPENAI_HIGH_MODEL_SHARED", "qwen2.5:14b")
    import insight.llm_provider as L
    reload(L)
    p = L.get_provider(tier="auto")
    assert p is not None
    assert p.tier_label == "external:test-model"


def test_auto_tier_fallback_to_high_when_external_missing(monkeypatch):
    """auto: external 없으면 high → fast 폴백."""
    _clear(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OPENAI_HIGH_MODEL_SHARED", "qwen2.5:14b")
    import insight.llm_provider as L
    reload(L)
    p = L.get_provider(tier="auto")
    assert p is not None
    assert p.tier_label.startswith("high-shared")


def test_prefer_external(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("EXTERNAL_API_KEY", "sk-test-123")
    monkeypatch.setenv("EXTERNAL_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("EXTERNAL_MODEL", "test-model")
    import insight.llm_provider as L
    reload(L)
    p = L.get_provider(prefer="external")
    assert p is not None
    assert p.tier_label == "external:test-model"


def test_prefer_external_no_keys(monkeypatch):
    _clear(monkeypatch)
    import insight.llm_provider as L
    reload(L)
    assert L.get_provider(prefer="external") is None
