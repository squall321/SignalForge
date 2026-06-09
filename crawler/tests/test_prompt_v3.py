"""Track A R26 — topic_eval_multilabel.py v3 프롬프트 단위 테스트.

검증 대상 (스크립트 호출 없이 순수 함수 단위):
1. ``build_prompt(version="v3")`` 가 v3 템플릿(8-shot + 미니 CoT 규칙) 을 적용하는지
2. ``llm_params("v3")`` 가 (temperature=0.2, max_tokens=150) 을 돌려주는지
3. ``call_llm(..., version="v3")`` 가 *잘못된 JSON* 응답에 대해
   ``max_retries>=2`` 재시도를 수행하고, 마지막 raw 를 반환하는지
4. ``_is_valid_topic_json`` 헬퍼가 빈 응답/오답을 False, 정상 JSON 을 True 로 평가

DB 의존성·LLM 의존성 없이 OpenAI client 는 FakeClient 로 대체.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.topic_eval_multilabel import (
    _is_valid_topic_json,
    build_prompt,
    call_llm,
    llm_params,
    parse_llm_topics,
)


# ---------------------------------------------------------------------------
# Helpers — FakeClient 가 chat.completions.create 시그니처를 흉내.
# ---------------------------------------------------------------------------
class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = SimpleNamespace(content=content)


class _FakeResp:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def create(self, **_kwargs):  # noqa: ANN003
        idx = min(self.calls, len(self.replies) - 1)
        self.calls += 1
        return _FakeResp(self.replies[idx])


class FakeClient:
    def __init__(self, replies):
        self.chat = SimpleNamespace(completions=FakeCompletions(replies))


# ---------------------------------------------------------------------------
# Case 1 — v3 프롬프트 빌드 + 파라미터 + 유효성 헬퍼
# ---------------------------------------------------------------------------
def test_prompt_v3_build_and_params() -> None:
    prompt = build_prompt("S24 1년 써본 결과 만족합니다", version="v3")

    # v3 만 가지고 있는 가드 문구 (R25 v2 와 구별 — 8-shot)
    assert "예시 7" in prompt, "v3 7 번째 few-shot 누락"
    assert "예시 8" in prompt, "v3 8 번째 few-shot 누락"
    # v3 추가 가드 (comparison/experience guard hints)
    assert "comparison 가드" in prompt, "v3 comparison 가드 라벨 누락"
    assert "experience 가드" in prompt, "v3 experience 가드 라벨 누락"
    assert "S24 1년 써본 결과 만족합니다" in prompt, "본문 치환 실패"
    # v2 와 차이 — v2 는 6 예시, v3 는 8 예시
    prompt_v2 = build_prompt("같은 본문", version="v2")
    assert "예시 7" not in prompt_v2, "v2 에는 예시 7 이 있으면 안 됨"

    # 파라미터: v3 는 temp 0.2, max_tokens 150
    temp, max_tok = llm_params("v3")
    assert temp == 0.2
    assert max_tok == 150

    # v2/v1 영향 없음
    assert llm_params("v2") == (0.2, 100)
    assert llm_params("v1") == (0.0, 60)

    # JSON validity helper
    assert _is_valid_topic_json('["experience", "negative_general"]') is True
    assert _is_valid_topic_json("그냥 답변입니다") is False  # 토픽 라벨 없음
    assert _is_valid_topic_json("") is False
    # substring fallback (긴 라벨 우선 매칭) — 다른 형식이라도 유효 처리
    assert _is_valid_topic_json("정답: experience") is True


# ---------------------------------------------------------------------------
# Case 2 — call_llm(version="v3") 재시도 동작
# ---------------------------------------------------------------------------
def test_call_llm_v3_retries_on_invalid_json() -> None:
    # 첫 응답은 형식 깨짐(라벨 없음) → 재시도, 두 번째는 정상
    client_retry = FakeClient(
        replies=[
            "응답 깨짐 — 라벨 없음",
            '["experience", "negative_general"]',
        ]
    )
    raw = call_llm(client_retry, "S24 1년 사용", model="m", version="v3")
    assert raw == '["experience", "negative_general"]'
    # 정확히 2 회 호출됨 (재시도 1 회)
    assert client_retry.chat.completions.calls == 2
    # 파싱 결과 검증
    topics = parse_llm_topics(raw)
    assert topics == ["experience", "negative_general"]

    # 전부 실패해도 *마지막 raw* 를 반환 (fallback substring 매칭 단계에 위임)
    client_all_bad = FakeClient(
        replies=["깨짐1", "깨짐2", "깨짐3"]
    )
    raw_bad = call_llm(client_all_bad, "ㅋㅋ", model="m", version="v3")
    # v3 default max_retries=2 → 최대 2 회 호출 (1차 + 1 재시도)
    assert client_all_bad.chat.completions.calls == 2
    assert raw_bad == "깨짐2"  # 마지막 시도 raw

    # v1 은 retry 없음 — 1 회 호출만
    client_v1 = FakeClient(replies=["positive_general"])
    raw_v1 = call_llm(client_v1, "좋아요", model="m", version="v1")
    assert client_v1.chat.completions.calls == 1
    assert raw_v1 == "positive_general"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
