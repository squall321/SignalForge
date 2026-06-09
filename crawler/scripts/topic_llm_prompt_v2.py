"""topic_llm_prompt_v2 — Track A R22 (2026-06-05).

R21 의 LLM apply 결과 (agree_rate 0.250, n=8) 분석에서 발견된 두 가지
disagree 패턴을 잡기 위한 v2 prompt.

발견 패턴 (reports/topic_llm_apply_2026-06-05.json 의 discovery 단계)
  1. comparison_as_default_drift
     비교어(vs / than / 보다 / 갈아탐) 가 한 단어만 보여도 14b 가
     experience / positive_general / negative_general 글을 comparison 으로
     끌어당김. n=8 중 4건이 → comparison.
  2. negative_to_emotion_only_collapse
     'haha' / 'ㅋㅋ' 같은 감정 토큰 한두 개가 부정 후기를 emotion_only 로
     붕괴시킴 (id=436498).

v2 의 변경
  - few-shot 4건 (각 topic 의 disagree 케이스를 정답과 함께 제시).
  - 부정 규칙 (negative rules) 4개를 prompt 상단에 명시:
      * 비교어가 보조적이면 comparison 금지
      * 감정 토큰 1~2개만으로 emotion_only 단정 금지
      * 평가 내용이 있으면 emotion_only 보다 positive_/negative_general 우선
      * 후기/경험 표현이 있으면 experience 우선
  - 출력 강제: 첫 줄 단어 1개만, 설명·따옴표·문장부호 금지.
  - temperature=0.0, top_p=0.0, seed=0 (가능한 경우) — deterministic.

사용 (topic_llm_apply.py 에서 호출):
    from scripts.topic_llm_prompt_v2 import build_prompt_v2, call_llm_v2
    raw = call_llm_v2(client, content, model)
    label = parse_llm_label(raw)
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("topic_llm_prompt_v2")


# ---------------------------------------------------------------------------
# v2 prompt
# ---------------------------------------------------------------------------
PROMPT_TEMPLATE_V2 = """당신은 한국어/영어 댓글의 *주된* topic 을 분류하는 분류기입니다.

다음 10개 중 *정확히 하나* 만 *단어 한 개* 로 답하세요.
(소문자, 설명·따옴표·문장부호 없음. 첫 줄에 단어 한 개만.)

라벨 정의:
- positive_general : 긍정 평가 ("좋네요", "최고", "추천", "great")
- negative_general : 부정 평가 ("별로", "실망", "trash", "bad", "worse")
- question         : 질문 ("어떻게", "되나요?", "?")
- comparison       : *비교 자체가 글의 주제* (대안 vs 대안, 갈아탐 결정)
- price_purchase   : 가격/구매/할인/예약/할부
- service_repair   : 수리/AS/서비스센터/리퍼
- experience       : 사용 후기 (기간 언급, "써본 결과", "daily driver", "shooting with")
- expectation      : 출시/루머/기대/유출
- emotion_only     : *감정 표현/이모지만* 있는 *짧은* 글 ("ㅋㅋㅋ", "ㅠㅠ", "haha" *단독*)
- other            : 위 어디에도 맞지 않음

*반드시 지킬 규칙*
1) 비교어(vs / than / 보다 / 갈아탐) 가 등장해도, 글의 주된 내용이 *후기·평가·질문* 이면
   comparison 이 아니라 experience / positive_general / negative_general / question 을 고르세요.
   comparison 은 *비교 자체가 주제* 일 때만 선택합니다.
2) 'haha' / 'ㅋㅋ' / 'ㅠㅠ' 같은 감정 토큰이 1~2개 들어 있어도, *평가 내용* 이 있으면
   emotion_only 가 아니라 negative_general / positive_general / experience 를 우선 선택하세요.
   emotion_only 는 *오직 감정 표현만 있는 짧은 글* 입니다.
3) 사용 후기·경험 표현(써본/daily driver/shooting with/using) 이 있으면 experience 를 우선.
4) 가격/모델 스펙(FHD, 사양, 가격) 자체가 글의 핵심이면 price_purchase 또는 negative_general
   (스펙 비판) 을 우선. 모델명을 단순 언급한다고 comparison 이 아닙니다.

예시 1 (experience, *not* comparison):
  글: "I also currently own an iPhone mini 12 but my Galaxy S23 Ultra is my daily driver, no accounting for taste."
  답: experience

예시 2 (negative_general, *not* emotion_only):
  글: "Post-editing makes it even worse haha. I'll try shooting with scene-specific optimization turned off."
  답: negative_general

예시 3 (negative_general, *not* comparison):
  글: "It's FHD, so the specs aren't that great, but it has a smart TV function."
  답: negative_general

예시 4 (positive_general, *not* comparison):
  글: "The writing feel of the iPad is so great that the Galaxy Tab has a comparative advantage."
  답: positive_general

이제 아래 글을 분류하세요. 단어 한 개만, 첫 줄에 답하세요.

글:
{text}

답:"""


def build_prompt_v2(content: str, max_chars: int = 800) -> str:
    """v2 prompt 빌더. 본문이 길면 앞 max_chars 자만 사용."""
    snippet = (content or "").strip()
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars] + "…"
    return PROMPT_TEMPLATE_V2.format(text=snippet)


def call_llm_v2(client, content: str, model: str) -> Optional[str]:
    """v2 prompt + deterministic 옵션으로 LLM 호출.

    실패 시 None.
    Ollama OpenAI-호환은 seed 를 무시할 수 있으나 안전을 위해 전달.
    """
    try:
        kwargs = dict(
            model=model,
            max_tokens=20,
            temperature=0.0,
            top_p=0.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "당신은 한국어/영어 댓글 topic 분류기. "
                        "단어 한 개만 답함. 설명·따옴표·문장부호 금지."
                    ),
                },
                {"role": "user", "content": build_prompt_v2(content)},
            ],
        )
        # seed 는 OpenAI-호환 endpoint 가 지원하면 사용, 아니면 무시.
        try:
            resp = client.chat.completions.create(seed=0, **kwargs)
        except TypeError:
            resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0] if resp.choices else None
        if choice is None or choice.message is None:
            return None
        return (choice.message.content or "").strip()
    except Exception as e:  # pragma: no cover
        log.warning("LLM v2 호출 실패: %s", e)
        return None
