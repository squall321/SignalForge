"""topic_llm_apply v2 prompt — Track A R22 단위 테스트.

목적:
  1. build_prompt_v2() 가 few-shot 4건 + 부정 규칙을 모두 포함하는지 검증.
  2. call_llm_v2() 가 mock 클라이언트로 정상 호출/파싱되는지 검증.

DB·네트워크 미사용. mock 만 사용.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from scripts.topic_eval import parse_llm_label
from scripts.topic_llm_prompt_v2 import (
    build_prompt_v2,
    call_llm_v2,
    PROMPT_TEMPLATE_V2,
)


# ---------------------------------------------------------------------------
# 1. build_prompt_v2 — 필수 요소 포함 검증
# ---------------------------------------------------------------------------
def test_build_prompt_v2_contains_required_elements():
    """v2 prompt 는 few-shot 4건 + 부정 규칙 4종을 모두 포함해야 한다."""
    p = build_prompt_v2("S25 좋네요")
    # 본문 삽입
    assert "S25 좋네요" in p
    # 부정 규칙 (R21 발견 패턴 대응)
    assert "비교어" in p and "comparison 이 아니라" in p  # rule 1
    assert "haha" in p and "emotion_only" in p  # rule 2
    assert "experience 를 우선" in p  # rule 3
    # few-shot 예시 4건 — 정답 라벨이 모두 등장
    assert "예시 1" in p and "예시 2" in p
    assert "예시 3" in p and "예시 4" in p
    # 각 예시 정답
    assert "답: experience" in p
    assert "답: negative_general" in p
    assert "답: positive_general" in p
    # 모든 라벨 옵션 포함
    for label in (
        "positive_general",
        "negative_general",
        "question",
        "comparison",
        "price_purchase",
        "service_repair",
        "experience",
        "expectation",
        "emotion_only",
        "other",
    ):
        assert label in p, f"라벨 {label} 누락"


def test_build_prompt_v2_truncates_long_content():
    """긴 본문은 max_chars 로 잘리고 '…' 표시되어야 한다."""
    long_text = "a" * 2000
    p = build_prompt_v2(long_text, max_chars=800)
    assert "a" * 800 + "…" in p
    assert "a" * 801 not in p


def test_build_prompt_v2_handles_empty_content():
    """빈 본문도 예외 없이 처리되어야 한다."""
    p = build_prompt_v2("")
    assert "글:\n\n\n답:" in p or "글:\n\n답:" in p


# ---------------------------------------------------------------------------
# 2. call_llm_v2 — mock 클라이언트로 동작 검증
# ---------------------------------------------------------------------------
def _make_mock_client(answer: str):
    """OpenAI 호환 chat.completions.create mock 을 반환."""
    client = MagicMock()
    msg = MagicMock()
    msg.content = answer
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    client.chat.completions.create.return_value = resp
    return client


def test_call_llm_v2_returns_label_and_uses_deterministic_args():
    """call_llm_v2 는 temperature=0.0, max_tokens 20 으로 호출하고 응답을 반환한다."""
    client = _make_mock_client("experience")
    raw = call_llm_v2(client, "갤럭시 S24 한 달 써본 후기", "qwen2.5:14b")
    assert raw == "experience"
    assert parse_llm_label(raw) == "experience"
    # 호출 인자 검증 — deterministic 보장
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "qwen2.5:14b"
    assert kwargs["temperature"] == 0.0
    assert kwargs["max_tokens"] == 20
    # system + user 두 메시지
    assert len(kwargs["messages"]) == 2
    assert kwargs["messages"][0]["role"] == "system"
    assert kwargs["messages"][1]["role"] == "user"
    # v2 본문이 user 에 들어가야 한다
    assert "갤럭시 S24 한 달 써본 후기" in kwargs["messages"][1]["content"]


def test_call_llm_v2_handles_exception_gracefully():
    """LLM 호출 실패 시 None 반환 (예외 누수 금지)."""
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("boom")
    raw = call_llm_v2(client, "any", "qwen2.5:14b")
    assert raw is None


def test_call_llm_v2_falls_back_when_seed_unsupported():
    """seed 파라미터를 지원하지 않는 클라이언트는 TypeError 후 재시도."""
    client = MagicMock()
    msg = MagicMock()
    msg.content = "comparison"
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]

    call_count = {"n": 0}

    def maybe_seed(**kwargs):
        call_count["n"] += 1
        if "seed" in kwargs and call_count["n"] == 1:
            raise TypeError("unexpected keyword 'seed'")
        return resp

    client.chat.completions.create.side_effect = maybe_seed
    raw = call_llm_v2(client, "iPad vs Galaxy Tab", "qwen2.5:14b")
    assert raw == "comparison"
    # 두 번 호출 — 첫 번째는 seed 포함, 두 번째는 fallback
    assert call_count["n"] == 2
