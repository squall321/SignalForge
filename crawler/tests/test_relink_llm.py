"""relink_llm.py 단위 테스트 — R14 트랙 C.

검증 시나리오 (외부 DB/LLM 의존 없음, mock 사용):
  1. _classify_llm_text() — none 토큰, 빈 응답, 정상 응답, 너무 긴 응답 분류.
  2. _call_llm() — fake provider 의 client.chat.completions.create 가
     'Galaxy S22 Ultra' 를 반환할 때 정상 텍스트 추출.
  3. _call_llm() + match_product_code() 통합 — LLM 응답 텍스트가
     static dict 으로 'GS22U' 까지 매칭되는지 end-to-end 확인.

실행:
  cd crawler && python -m pytest tests/test_relink_llm.py -v
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.relink_llm import _classify_llm_text, _call_llm  # noqa: E402
from scripts.relink_products import match_product_code  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────
# 1. _classify_llm_text 응답 정규화
# ─────────────────────────────────────────────────────────────────────────
def test_classify_none_variants():
    assert _classify_llm_text("none") == "none"
    assert _classify_llm_text("None") == "none"
    assert _classify_llm_text(" NONE ") == "none"
    assert _classify_llm_text("") == "none"
    assert _classify_llm_text("없음") == "none"
    assert _classify_llm_text("n/a") == "none"
    assert _classify_llm_text("'none'") == "none"


def test_classify_normal_model_text():
    assert _classify_llm_text("Galaxy S22 Ultra") == "Galaxy S22 Ultra"
    assert _classify_llm_text("Note 7") == "Note 7"
    # 양쪽 따옴표 절단
    assert _classify_llm_text('"Z Fold 5"') == "Z Fold 5"
    # 첫 줄만 인식
    assert _classify_llm_text("Galaxy Watch 9\nadditional text") == "Galaxy Watch 9"


def test_classify_long_response_treated_as_none():
    # 프롬프트 무시한 장황한 응답 (>80자) — none 으로 폴백.
    bad = (
        "Well, looking at the text more carefully, it could be either Galaxy S22 "
        "or maybe the S23 Ultra family"
    )
    assert len(bad) > 80
    assert _classify_llm_text(bad) == "none"


# ─────────────────────────────────────────────────────────────────────────
# 2. _call_llm — fake provider 로 mock
# ─────────────────────────────────────────────────────────────────────────
class _FakeCompletions:
    """OpenAI SDK 의 client.chat.completions.create() 시그니처를 흉내냄."""

    def __init__(self, reply: str):
        self._reply = reply
        self.last_kwargs: dict = {}

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        choice = SimpleNamespace(message=SimpleNamespace(content=self._reply))
        return SimpleNamespace(choices=[choice])


class _FakeChat:
    def __init__(self, reply: str):
        self.completions = _FakeCompletions(reply)


class _FakeClient:
    def __init__(self, reply: str):
        self.chat = _FakeChat(reply)


def _make_fake_provider(reply: str, model: str = "qwen2.5:14b"):
    """provider._client + provider.model 만 노출하면 _call_llm 호환."""
    p = SimpleNamespace(
        _client=_FakeClient(reply),
        model=model,
        tier_label="test-fake",
        name="fake",
    )
    return p


def test_call_llm_returns_text():
    prov = _make_fake_provider("Galaxy S22 Ultra")
    out = _call_llm(prov, "Samsung's new flagship is amazing")
    assert out == "Galaxy S22 Ultra"


def test_call_llm_none_reply():
    prov = _make_fake_provider("none")
    out = _call_llm(prov, "Samsung makes nice TVs")
    assert out == "none"


def test_call_llm_passes_temperature_zero():
    prov = _make_fake_provider("Note 7")
    _call_llm(prov, "Galaxy Note explosion was a disaster")
    kw = prov._client.chat.completions.last_kwargs
    assert kw.get("temperature") == 0.0
    assert kw.get("model") == "qwen2.5:14b"
    # system 프롬프트가 LLM_SYSTEM 으로 들어감
    msgs = kw.get("messages", [])
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert "Samsung Galaxy model" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"


# ─────────────────────────────────────────────────────────────────────────
# 3. end-to-end — LLM 응답 → static match → product code
# ─────────────────────────────────────────────────────────────────────────
def test_e2e_llm_text_to_static_code():
    """LLM 이 'Galaxy S22 Ultra' 를 반환하면, static dict 가 'GS22U' 로 매칭."""
    prov = _make_fake_provider("Galaxy S22 Ultra")
    llm_text = _call_llm(prov, "Samsung's flagship has changed my life")
    norm = _classify_llm_text(llm_text or "")
    assert norm == "Galaxy S22 Ultra"
    code = match_product_code(norm)
    assert code == "GS22U"


def test_e2e_llm_text_korean_note():
    """LLM 이 '갤럭시 노트 7' 을 반환하면 'GN7' 매칭."""
    prov = _make_fake_provider("갤럭시 노트 7")
    llm_text = _call_llm(prov, "Samsung 의 폭발 이슈가 있었던 그 모델")
    norm = _classify_llm_text(llm_text or "")
    code = match_product_code(norm)
    assert code == "GN7"


def test_e2e_llm_none_returns_no_match():
    prov = _make_fake_provider("none")
    llm_text = _call_llm(prov, "Samsung TV is hackable")
    norm = _classify_llm_text(llm_text or "")
    assert norm == "none"
    # 'none' 은 갤럭시 컨텍스트 없는 단어 — static dict 가 None 반환
    code = match_product_code(norm)
    assert code is None


# ─────────────────────────────────────────────────────────────────────────
# pytest 없이 직접 실행
# ─────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [obj for name, obj in globals().items() if name.startswith("test_") and callable(obj)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
