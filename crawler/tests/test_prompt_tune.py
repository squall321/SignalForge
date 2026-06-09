"""topic_eval_multilabel.py 프롬프트 튜닝 (R25 Track A) 단위 테스트.

LLM·DB 무의존. mock LLM 응답에 대한 파서/프롬프트 분기 검증만 수행.

검증:
1. v2 프롬프트는 6-shot 예시 + primary 강조가 들어 있고, temp/max_tokens 가
   각각 0.2/100 이며, v1 (R24 기본) 은 그대로다.
2. mock LLM 응답에 대한 parse_llm_topics 가 R24 가짜 회신 (JSON 절단 / 일반
   substring) 을 안전하게 파싱한다.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.topic_eval_multilabel import (  # noqa: E402
    build_prompt,
    call_llm,
    llm_params,
    parse_llm_topics,
)


def test_prompt_v2_has_fewshot_and_tuned_decoder():
    """v2 는 6-shot + primary 강조 + temp/max_tok 상향."""
    p1 = build_prompt("S24 카메라 좋네요", version="v1")
    p2 = build_prompt("S24 카메라 좋네요", version="v2")

    # v1 은 few-shot 없음
    assert "예시 1:" not in p1
    assert "예시 6:" not in p1

    # v2 는 6 개 예시 모두 포함
    for i in range(1, 7):
        assert f"예시 {i}:" in p2, f"v2 에 예시 {i} 누락"

    # v2 는 primary/모호 가드 문구 포함
    assert "primary" in p2
    assert "모호하면 추가하지 마세요" in p2
    # comparison 가드 (R24 LLM 과다 예측 대응)
    assert "두 제품" in p2 or "두 제품/모델" in p2
    # experience 신호 강조
    assert "사용 기간" in p2

    # 본문 텍스트 주입 확인
    assert "S24 카메라 좋네요" in p1
    assert "S24 카메라 좋네요" in p2

    # 디코더 파라미터
    assert llm_params("v1") == (0.0, 60)
    assert llm_params("v2") == (0.2, 100)
    # 알 수 없는 버전은 v1 fallback
    assert llm_params("v9999") == (0.0, 60)


def test_call_llm_mock_parses_v2_json_array():
    """mock LLM 이 v2 형식 JSON 배열을 반환하면 parser 가 안전하게 처리."""
    # mock client — chat.completions.create 만 흉내
    client = MagicMock()
    fake_message = MagicMock()
    fake_message.content = '["experience", "negative_general"]'
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]
    client.chat.completions.create.return_value = fake_resp

    raw = call_llm(client, "1년 써본 결과 별로네요", "qwen2.5:14b", version="v2")
    assert raw == '["experience", "negative_general"]'

    # v2 디코더 인자 (temp 0.2, max_tokens 100) 로 호출됐는지 확인
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0.2
    assert kwargs["max_tokens"] == 100

    topics = parse_llm_topics(raw)
    assert topics == ["experience", "negative_general"]

    # R24 가 자주 겪은 절단 케이스 — fallback substring 도 동작
    cut = '["experience", "negative_'  # JSON 미완 + 부분 단어
    parsed = parse_llm_topics(cut)
    # JSON 실패 시 substring scan 으로 "experience" 회수
    assert "experience" in parsed
