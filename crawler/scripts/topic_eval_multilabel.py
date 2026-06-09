"""topic 분류기 multi-label joint F1 평가 — Track A (R24, 2026-06-05).

목적
----
R10 ~ R23 의 topic eval 은 **primary topic (auto[0] vs llm[0])** 만 비교해
multi-label 정보를 누락. R24 는 *set 기반 multi-label* 평가를 도입한다.

샘플링
------
- 총 100 건
- 우선순위: voc_records 의 ``cardinality(topics) >= 2`` 행 70 건 (multi-label)
- 나머지 30 건: ``cardinality(topics) = 1`` (single-label)
- 각 primary topic 에서 균등 추출 (가용 행 부족 시 가용 max 만큼)

LLM 평가
--------
qwen2.5:14b (Ollama OpenAI 호환).
프롬프트는 *최대 3 개 topic* 을 JSON 배열로 응답하도록 강제.

지표
----
- exact_match  : auto_set == llm_set
- partial_match: |∩| >= 1
- jaccard      : |∩| / |∪|
- f1_micro     : 2·|∩| / (|auto| + |llm|)
- per-topic precision/recall/F1  (multi-label, set 기준)
- macro-F1     : per-topic F1 평균
- primary F1   : top1 only (R10/R18/R23 비교용)

출력
----
- JSONL : 행별 {id, auto_topics, llm_topics, exact, partial, jaccard, f1_micro, round:'R24'}
- MD/JSON 요약 보고서

환경변수
--------
- DATABASE_URL                postgresql+asyncpg://...
- OLLAMA_BASE_URL             기본 http://127.0.0.1:11434/v1
- OLLAMA_EVAL_MODEL           기본 qwen2.5:14b
- TOPIC_EVAL_MULTI            multi-label 샘플 수 (기본 70)
- TOPIC_EVAL_SINGLE           single-label 샘플 수 (기본 30)
- TOPIC_EVAL_SEED             기본 20260605
- TOPIC_EVAL_OUT_DIR          기본 reports/
- TOPIC_EVAL_OUT_SUFFIX       기본 _r24_multilabel
- TOPIC_EVAL_PROMPT_V         "v1" (R24 기본, temp 0, max_tok 60) | "v2" (R25 튜닝: 6-shot, temp 0.2, max_tok 100) | "v3" (R26 튜닝: 8-shot + 미니 CoT + JSON 재시도, temp 0.2, max_tok 150) | "v3.1" (R27 듀얼 축 — primary/multi 별도 prompt+sampling)
- TOPIC_EVAL_ROUND            보고서/JSONL round 라벨 (기본 "R24"; v2 사용 시 "R25", v3 사용 시 "R26", v3.1 사용 시 "R27" 권장)
- TOPIC_EVAL_AXIS             "multi" (기본 — 단일 multi 호출) | "both" (v3.1 전용 — primary+multi 양축 측정, LLM 호출 2배)

실행
----
    # R24 baseline (변경 없음)
    /home/koopark/claude/SignalForge/.venv/bin/python -m scripts.topic_eval_multilabel

    # R25 — Track A 프롬프트 튜닝
    TOPIC_EVAL_PROMPT_V=v2 \\
    TOPIC_EVAL_ROUND=R25 \\
    TOPIC_EVAL_OUT_SUFFIX=_r25_multilabel \\
    /home/koopark/claude/SignalForge/.venv/bin/python -m scripts.topic_eval_multilabel

    # R26 — Track A 프롬프트 v3 (8-shot + 미니 CoT + JSON 재시도)
    TOPIC_EVAL_PROMPT_V=v3 \\
    TOPIC_EVAL_ROUND=R26 \\
    TOPIC_EVAL_OUT_SUFFIX=_r26_multilabel \\
    /home/koopark/claude/SignalForge/.venv/bin/python -m scripts.topic_eval_multilabel
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from scripts.topic_eval import LLM_LABELS, TOPICS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("topic_eval_multilabel")

DATABASE_URL = os.getenv("DATABASE_URL", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
OLLAMA_EVAL_MODEL = os.getenv("OLLAMA_EVAL_MODEL", "qwen2.5:14b")
N_MULTI = int(os.getenv("TOPIC_EVAL_MULTI", "70"))
N_SINGLE = int(os.getenv("TOPIC_EVAL_SINGLE", "30"))
SEED = int(os.getenv("TOPIC_EVAL_SEED", "20260605"))
OUT_DIR = os.getenv("TOPIC_EVAL_OUT_DIR", "/home/koopark/claude/SignalForge/reports")
OUT_SUFFIX = os.getenv("TOPIC_EVAL_OUT_SUFFIX", "_r24_multilabel")
PROMPT_V = os.getenv("TOPIC_EVAL_PROMPT_V", "v1").lower()
ROUND_LABEL = os.getenv("TOPIC_EVAL_ROUND", "R24")
AXIS = os.getenv("TOPIC_EVAL_AXIS", "multi").lower()  # "multi" | "both"

# R10/R18/R23 primary-label baseline (참고)
R10_OVERALL_PRIMARY = 0.678
R18_OVERALL_PRIMARY = 0.640
R23_OVERALL_PRIMARY = 0.406

# ---------------------------------------------------------------------------
# LLM 프롬프트 — multi-label JSON 배열 강제
# ---------------------------------------------------------------------------
PROMPT_TEMPLATE = """당신은 한국어/영어 짧은 댓글의 주제(topic)를 분류하는 분류기입니다.

다음 9 개 topic 중 글에 *해당하는 것을 1~3 개* 골라 **JSON 배열로만** 답하세요.
설명·문장·코드 블럭 금지. 다른 텍스트 절대 출력 금지.

topic 목록:
- positive_general : 일반적인 긍정 평가
- negative_general : 일반적인 부정 평가
- question         : 질문
- comparison       : 비교/대조/갈아탐
- price_purchase   : 가격/구매/할인/예약/할부
- service_repair   : 수리/서비스센터/리퍼/AS
- experience       : 사용 후기 (기간 언급, "써본 결과")
- expectation      : 출시/루머/기대/유출
- emotion_only     : 감정 표현/이모지만 있는 짧은 글

응답 형식 (반드시 JSON 배열):
["topic1"]                       # 단일 topic
["topic1", "topic2"]             # 2 개
["topic1", "topic2", "topic3"]   # 최대 3 개

글:
{text}

답:"""


# R25 Track A 튜닝 — few-shot 6 + primary 강조 + comparison 가드.
# R24 분석:
#   - LLM 이 comparison 을 과다 예측 (support_llm 59 vs auto 19, recall 0.288)
#   - experience 누락 (support_llm 10 vs auto 26, precision 0.192)
#   - positive_general 저정밀 (precision 0.375)
# 대응:
#   - primary 1 개 우선, 명확할 때만 secondary/tertiary
#   - comparison: 두 제품 명시 비교만 (단순 언급 금지)
#   - experience: 사용 기간/실사용 기술 시 우선 선택
PROMPT_TEMPLATE_V2 = """당신은 한국어/영어 짧은 댓글의 주제(topic)를 분류하는 분류기입니다.

다음 9 개 topic 중 글의 *가장 핵심적인 주제 1 개 (primary)* 를 먼저 정하고,
글에 *명확히* 부합하면 secondary 를 최대 2 개 추가하세요. 모호하면 추가하지 마세요.
**JSON 배열로만** 답하고, 설명·문장·코드 블럭 절대 금지.

topic 목록:
- positive_general : 칭찬·만족·추천 표현이 주된 글
- negative_general : 불만·실망·비판이 주된 글 (비교 의도 없음)
- question         : 질문·문의·"~인가요?" 가 핵심
- comparison       : 두 제품/모델/세대를 *명시적으로* 비교 (A vs B, "갈아탔다")
- price_purchase   : 가격·구매·할인·예약·할부 언급 핵심
- service_repair   : 수리·서비스센터·리퍼·AS·보증 언급 핵심
- experience       : *사용 기간 명시* 또는 "써본 결과/실사용" 후기 (가장 강한 신호)
- expectation      : 출시 전 기대·루머·유출·"나오면" 언급
- emotion_only     : 감정 표현/이모지만 있는 매우 짧은 글

주의:
- 단순히 다른 제품 이름이 등장한다고 comparison 이 아님 (두 제품의 우열 비교 필요)
- 사용 기간(예: "6년째 쓴다") 이나 "써보니" 가 있으면 experience 우선
- 긍정/부정은 *글 전체 톤* 기준 — 한 단어 칭찬은 부족

예시 1:
글: "S24U 1년 넘게 써본 결과 카메라는 만족하는데 배터리가 빨리 닳네요"
답: ["experience", "negative_general"]

예시 2:
글: "아이폰15 vs 갤럭시 S24 뭐가 나을까요? 갈아타려는데 고민돼요"
답: ["comparison", "question"]

예시 3:
글: "Z폴드6 출고가 얼마예요? 사전예약 할인 있나요?"
답: ["price_purchase", "question"]

예시 4:
글: "서비스센터에서 액정 교체했는데 7만원 들었네요 ㅠㅠ"
답: ["service_repair", "price_purchase"]

예시 5:
글: "S25 울트라 루머 도는데 카메라 진짜 5억 화소면 ㅈㄴ 기대됨"
답: ["expectation"]

예시 6:
글: "ㅋㅋㅋㅋㅋㅋ"
답: ["emotion_only"]

글:
{text}

답:"""


# R26 Track A 프롬프트 v3 — v2 기반에 *최소 변경*: 2 예시 추가 + JSON 재시도.
#
# v3 (1차 시도, 8-shot + 우선순위 규칙) 결과: macro F1 0.616 (R25 0.651 대비 -0.035).
# 분석: 우선순위 규칙·미니 CoT 가 LLM 의 단순 판단을 흐트림. service_repair 0.880 → 0.762,
#        expectation 0.714 → 0.667 등 *튼튼하던 카테고리* 까지 흔들림.
# v3 최종: **v2 의 6 예시를 그대로 유지** + comparison/experience 가드용 예시 2 개만
#          추가. 규칙 섹션·미니 CoT 제거. JSON 재시도는 call_llm 단에서 처리.
PROMPT_TEMPLATE_V3 = """당신은 한국어/영어 짧은 댓글의 주제(topic)를 분류하는 분류기입니다.

다음 9 개 topic 중 글의 *가장 핵심적인 주제 1 개 (primary)* 를 먼저 정하고,
글에 *명확히* 부합하면 secondary 를 최대 2 개 추가하세요. 모호하면 추가하지 마세요.
**JSON 배열로만** 답하고, 설명·문장·코드 블럭 절대 금지.

topic 목록:
- positive_general : 칭찬·만족·추천 표현이 주된 글
- negative_general : 불만·실망·비판이 주된 글 (비교 의도 없음)
- question         : 질문·문의·"~인가요?" 가 핵심
- comparison       : 두 제품/모델/세대를 *명시적으로* 비교 (A vs B, "갈아탔다")
- price_purchase   : 가격·구매·할인·예약·할부 언급 핵심
- service_repair   : 수리·서비스센터·리퍼·AS·보증 언급 핵심
- experience       : *사용 기간 명시* 또는 "써본 결과/실사용" 후기 (가장 강한 신호)
- expectation      : 출시 전 기대·루머·유출·"나오면" 언급
- emotion_only     : 감정 표현/이모지만 있는 매우 짧은 글

주의:
- 단순히 다른 제품 이름이 등장한다고 comparison 이 아님 (두 제품의 우열 비교 필요)
- 사용 기간(예: "6년째 쓴다") 이나 "써보니" 가 있으면 experience 우선
- 긍정/부정은 *글 전체 톤* 기준 — 한 단어 칭찬은 부족

예시 1:
글: "S24U 1년 넘게 써본 결과 카메라는 만족하는데 배터리가 빨리 닳네요"
답: ["experience", "negative_general"]

예시 2:
글: "아이폰15 vs 갤럭시 S24 뭐가 나을까요? 갈아타려는데 고민돼요"
답: ["comparison", "question"]

예시 3:
글: "Z폴드6 출고가 얼마예요? 사전예약 할인 있나요?"
답: ["price_purchase", "question"]

예시 4:
글: "서비스센터에서 액정 교체했는데 7만원 들었네요 ㅠㅠ"
답: ["service_repair", "price_purchase"]

예시 5:
글: "S25 울트라 루머 도는데 카메라 진짜 5억 화소면 ㅈㄴ 기대됨"
답: ["expectation"]

예시 6:
글: "ㅋㅋㅋㅋㅋㅋ"
답: ["emotion_only"]

예시 7 (comparison 가드 — 단순 언급은 NOT comparison):
글: "Galaxy S20 6년째 쓰고 있는데 큰 문제 없어요. 친구가 아이폰 산다고 하더라"
답: ["experience", "positive_general"]

예시 8 (experience 가드 — 사용 기간 없으면 experience 아님):
글: "왜 이 폰을 1테라까지 가야 하나요? 512면 충분한데 돈낭비 같아요"
답: ["question", "negative_general"]

글:
{text}

답:"""


# R27 Track A 프롬프트 v3.1 (primary 축 전용) — *단일 라벨* 단답.
#
# 배경: R26 v3 는 multi-label 회수 (macro F1 0.651→0.658) 에는 도움됐지만
#       primary top1 0.500→0.450 (-5pt) 회귀. 원인:
#       (1) temp 0.2 stochastic sampling 이 결정적 단일 라벨 선택을 흔든다
#       (2) v3 의 가드 예시들이 "2 개" 출력을 강조해 primary-only 케이스도
#           secondary 를 끌어와 top1 라벨이 흔들린다
#       (3) max_retries=2 의 재호출도 단일-라벨 분산을 확대
#
# v3.1 primary 축: temp 0.0 + 단일 라벨 응답 (1 개 강제) + max_retries 1.
# 모델 학습 신호가 *단답* 으로 모이도록 예시는 모두 1-label.
PROMPT_TEMPLATE_V31_PRIMARY = """당신은 한국어/영어 짧은 댓글의 *가장 핵심적인* 주제(topic) 를 1 개만 고르는 분류기입니다.

다음 9 개 topic 중 글의 *주제 1 개* 를 골라 **JSON 배열로만** 답하세요.
**반드시 1 개만** — secondary 금지. 설명·문장·코드 블럭 금지.

topic 목록:
- positive_general : 칭찬·만족·추천 표현이 주된 글
- negative_general : 불만·실망·비판이 주된 글
- question         : 질문·문의·"~인가요?" 가 핵심
- comparison       : 두 제품/모델/세대를 *명시적으로* 비교 (A vs B)
- price_purchase   : 가격·구매·할인·예약·할부 언급 핵심
- service_repair   : 수리·서비스센터·리퍼·AS·보증 언급 핵심
- experience       : *사용 기간 명시* 또는 "써본 결과" 후기
- expectation      : 출시 전 기대·루머·유출·"나오면" 언급
- emotion_only     : 감정 표현/이모지만 있는 매우 짧은 글

예시 1:
글: "S24U 1년 넘게 써본 결과 카메라는 만족하는데 배터리가 빨리 닳네요"
답: ["experience"]

예시 2:
글: "아이폰15 vs 갤럭시 S24 뭐가 나을까요?"
답: ["comparison"]

예시 3:
글: "Z폴드6 출고가 얼마예요?"
답: ["price_purchase"]

예시 4:
글: "서비스센터에서 액정 교체했네요"
답: ["service_repair"]

예시 5:
글: "S25 울트라 루머 도는데 카메라 5억 화소면 기대됨"
답: ["expectation"]

예시 6:
글: "ㅋㅋㅋㅋㅋㅋ"
답: ["emotion_only"]

글:
{text}

답:"""


def build_prompt(content: str, version: str = "v1", axis: str = "multi") -> str:
    """주어진 prompt 버전 + axis 에 맞는 prompt 빌드.

    axis="primary"  : v3.1 단일-라벨 템플릿 강제 (version 무관)
    axis="multi"    : 기존 v1/v2/v3 분기
    """
    snippet = (content or "").strip()
    if len(snippet) > 800:
        snippet = snippet[:800] + "…"
    if axis == "primary":
        # v3.1 은 axis="primary" 일 때 전용 단일-라벨 prompt 를 쓴다.
        # v1/v2/v3 은 axis="primary" 가 호출돼도 자기 자신의 template 사용
        # (호환성 유지) — 새 R27 듀얼축 평가는 v3.1 만 사용한다.
        if version == "v3.1":
            return PROMPT_TEMPLATE_V31_PRIMARY.format(text=snippet)
    if version in ("v3", "v3.1"):
        # v3.1 multi 축은 v3 multi prompt 재사용 (macro F1 회복분 유지)
        tmpl = PROMPT_TEMPLATE_V3
    elif version == "v2":
        tmpl = PROMPT_TEMPLATE_V2
    else:
        tmpl = PROMPT_TEMPLATE
    return tmpl.format(text=snippet)


_JSON_ARRAY_RE = re.compile(r"\[[^\[\]]*\]")


def parse_llm_topics(raw: str) -> List[str]:
    """LLM 응답에서 topic 배열 추출.

    1) JSON 배열 시도 (e.g. ``["positive_general", "experience"]``)
    2) 실패 시 fallback — 응답 전체에서 topic 라벨 substring 매칭 (긴 라벨 우선)
       (단일 라벨이라도 list 로 반환)
    빈 결과는 그대로 ``[]`` (LLM 이 분류 못한 경우).
    """
    if not raw:
        return []
    s = raw.strip()
    # JSON 배열 우선 매칭
    m = _JSON_ARRAY_RE.search(s)
    if m:
        snippet = m.group(0)
        try:
            arr = json.loads(snippet)
            if isinstance(arr, list):
                out: List[str] = []
                for item in arr:
                    if not isinstance(item, str):
                        continue
                    lab = item.strip().lower()
                    if lab in TOPICS:
                        if lab not in out:
                            out.append(lab)
                if out:
                    return out[:3]
        except Exception:
            pass
    # Fallback — substring scan (긴 라벨 우선; 같은 토픽 중복 제거)
    s_lower = s.lower()
    found: List[Tuple[int, str]] = []  # (pos, label)
    for label in sorted(TOPICS, key=len, reverse=True):
        idx = s_lower.find(label)
        if idx >= 0:
            found.append((idx, label))
    if not found:
        return []
    # 등장 순서 보존, 중복 제거
    found.sort(key=lambda x: x[0])
    seen: set = set()
    out2: List[str] = []
    for _, lab in found:
        if lab not in seen:
            seen.add(lab)
            out2.append(lab)
        if len(out2) >= 3:
            break
    return out2


def llm_params(version: str, axis: str = "multi") -> Tuple[float, int]:
    """프롬프트 버전 + 축 별 (temperature, max_tokens).

    - v1            : temp 0.0, max 60  — 결정적 단답
    - v2            : temp 0.2, max 100 — secondary 회수
    - v3            : temp 0.2, max 150 — 8-shot 길이 + JSON 재시도 여유
    - v3.1 primary  : temp 0.0, max 30  — 결정적 1-label (R27)
    - v3.1 multi    : temp 0.2, max 100 — v3 multi 와 동일 sampling
    """
    if version == "v3.1":
        if axis == "primary":
            return 0.0, 30
        # multi 축은 v3 와 동일 (단, max_tok 100 으로 약간 조임 → 평균 응답 길이가
        # 짧기 때문에 출력 노이즈 감소)
        return 0.2, 100
    if version == "v3":
        return 0.2, 150
    if version == "v2":
        return 0.2, 100
    return 0.0, 60


def _is_valid_topic_json(raw: str) -> bool:
    """LLM 응답이 *유효한 topic JSON 배열* 인지 빠르게 검사 (parse 결과 비공집합)."""
    parsed = parse_llm_topics(raw)
    return bool(parsed)


def call_llm(
    client,
    content: str,
    model: str,
    version: str = "v1",
    max_retries: int = 1,
    axis: str = "multi",
) -> Optional[str]:
    """LLM 호출 + (v3/v3.1 multi 한정) JSON 형식 재시도.

    v3              : max_retries=2 까지 재호출
    v3.1 primary    : max_retries=1 (단일-라벨 분산 차단)
    v3.1 multi      : max_retries=2 (v3 와 동일)
    """
    temp, max_tok = llm_params(version, axis=axis)
    if version == "v3":
        max_retries = max(max_retries, 2)
    elif version == "v3.1":
        if axis == "primary":
            max_retries = 1
        else:
            max_retries = max(max_retries, 2)
    # primary 축은 secondary 가 1 개여야 한다는 강한 system 시그널
    if axis == "primary":
        system_msg = (
            "당신은 한국어/영어 댓글 topic 분류기. "
            "출력은 반드시 길이 1 의 JSON 배열 한 줄. "
            "예: [\"experience\"]. "
            "설명·주석·코드 블록 금지. 2 개 이상 답하지 마시오."
        )
    else:
        system_msg = (
            "당신은 한국어/영어 댓글 topic 분류기. "
            "출력은 반드시 JSON 배열 한 줄. "
            "예: [\"experience\", \"negative_general\"]. "
            "설명·주석·코드 블록 금지."
        )
    last_raw: Optional[str] = None
    attempts = max(1, max_retries)
    for attempt in range(attempts):
        try:
            resp = client.chat.completions.create(
                model=model,
                max_tokens=max_tok,
                temperature=temp,
                messages=[
                    {"role": "system", "content": system_msg},
                    {
                        "role": "user",
                        "content": build_prompt(content, version=version, axis=axis),
                    },
                ],
            )
            choice = resp.choices[0] if resp.choices else None
            if choice is None or choice.message is None:
                continue
            raw = (choice.message.content or "").strip()
            last_raw = raw
            need_retry_v3 = (
                version == "v3" and not _is_valid_topic_json(raw)
            )
            need_retry_v31_multi = (
                version == "v3.1" and axis == "multi"
                and not _is_valid_topic_json(raw)
            )
            if (need_retry_v3 or need_retry_v31_multi):
                if attempt < attempts - 1:
                    continue
            return raw
        except Exception as e:  # pragma: no cover
            log.warning("LLM 호출 실패 (attempt=%d): %s", attempt + 1, e)
            continue
    return last_raw


# ---------------------------------------------------------------------------
# 샘플링 — multi-label 우선 70 + single-label 30
# ---------------------------------------------------------------------------
async def sample_multilabel(seed: int, n_multi: int, n_single: int) -> List[Dict]:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL 환경변수가 비어 있습니다")

    # 9 topics 균등 분배 — multi: 70/9 ≈ 8(나머지 7+8)
    multi_quota: Dict[str, int] = {}
    per_multi = n_multi // len(TOPICS)
    remainder_multi = n_multi - per_multi * len(TOPICS)
    for i, t in enumerate(TOPICS):
        multi_quota[t] = per_multi + (1 if i < remainder_multi else 0)

    single_quota: Dict[str, int] = {}
    per_single = n_single // len(TOPICS)
    remainder_single = n_single - per_single * len(TOPICS)
    for i, t in enumerate(TOPICS):
        single_quota[t] = per_single + (1 if i < remainder_single else 0)

    eng = create_async_engine(DATABASE_URL)
    rows: List[Dict] = []
    actual_multi_quota: Dict[str, int] = {}
    actual_single_quota: Dict[str, int] = {}
    try:
        async with eng.connect() as conn:
            # multi-label
            for topic, lim in multi_quota.items():
                if lim <= 0:
                    continue
                stmt = text(
                    """
                    SELECT id, topics,
                           COALESCE(content_translated, content_original) AS content
                    FROM voc_records
                    WHERE topics IS NOT NULL
                      AND cardinality(topics) >= 2
                      AND topics[1] = :topic
                      AND COALESCE(content_translated, content_original) IS NOT NULL
                    ORDER BY md5(id::text || :seed)
                    LIMIT :lim
                    """
                )
                r = await conn.execute(
                    stmt, {"topic": topic, "seed": str(seed), "lim": lim}
                )
                fetched = r.fetchall()
                actual_multi_quota[topic] = len(fetched)
                for row in fetched:
                    rows.append(
                        {
                            "id": row[0],
                            "auto_topics": list(row[1]),
                            "auto_primary": topic,
                            "content": row[2],
                            "tier": "multi",
                        }
                    )

            # single-label — multi 부족분 보충 위해 부족 수량만큼 추가 single 차감
            deficit_multi = sum(
                max(0, multi_quota[t] - actual_multi_quota.get(t, 0)) for t in TOPICS
            )
            # 부족하면 single 쿼터 늘려서 총 100 건 유지
            extra_per_topic = deficit_multi // len(TOPICS)
            extra_remainder = deficit_multi - extra_per_topic * len(TOPICS)
            for i, t in enumerate(TOPICS):
                single_quota[t] += extra_per_topic + (1 if i < extra_remainder else 0)

            for topic, lim in single_quota.items():
                if lim <= 0:
                    continue
                stmt = text(
                    """
                    SELECT id, topics,
                           COALESCE(content_translated, content_original) AS content
                    FROM voc_records
                    WHERE topics IS NOT NULL
                      AND cardinality(topics) = 1
                      AND topics[1] = :topic
                      AND COALESCE(content_translated, content_original) IS NOT NULL
                    ORDER BY md5(id::text || :seed)
                    LIMIT :lim
                    """
                )
                r = await conn.execute(
                    stmt, {"topic": topic, "seed": str(seed), "lim": lim}
                )
                fetched = r.fetchall()
                actual_single_quota[topic] = len(fetched)
                for row in fetched:
                    rows.append(
                        {
                            "id": row[0],
                            "auto_topics": list(row[1]),
                            "auto_primary": topic,
                            "content": row[2],
                            "tier": "single",
                        }
                    )
    finally:
        await eng.dispose()

    random.Random(seed).shuffle(rows)
    log.info(
        "샘플링 — multi=%d (요청 %d) single=%d (요청 %d) 총 %d",
        sum(actual_multi_quota.values()), n_multi,
        sum(actual_single_quota.values()), n_single,
        len(rows),
    )
    return rows


# ---------------------------------------------------------------------------
# 지표 계산
# ---------------------------------------------------------------------------
def row_metrics(auto: List[str], llm: List[str]) -> Dict[str, float]:
    """단일 행에 대한 set 기반 metrics."""
    A = set(auto)
    L = set(llm)
    inter = A & L
    union = A | L
    exact = 1.0 if A == L and A else 0.0
    partial = 1.0 if len(inter) >= 1 else 0.0
    jacc = (len(inter) / len(union)) if union else 0.0
    denom = len(A) + len(L)
    f1m = (2 * len(inter) / denom) if denom else 0.0
    return {
        "exact": exact,
        "partial": partial,
        "jaccard": jacc,
        "f1_micro": f1m,
        "auto_size": len(A),
        "llm_size": len(L),
        "inter_size": len(inter),
    }


def per_topic_multilabel_f1(rows: List[Dict]) -> Dict[str, Dict[str, float]]:
    """multi-label per-topic precision/recall/F1.

    - support : auto_set 에 topic 포함한 행 수 (실제 가진 자동 라벨)
    - llm_cnt : llm_set 에 topic 포함한 행 수
    - tp      : 둘 다 포함
    - precision = tp / auto_cnt
    - recall    = tp / llm_cnt
    """
    auto_cnt: Counter = Counter()
    llm_cnt: Counter = Counter()
    tp: Counter = Counter()
    for r in rows:
        A = set(r["auto_topics"])
        L = set(r["llm_topics"])
        for t in A:
            auto_cnt[t] += 1
        for t in L:
            llm_cnt[t] += 1
        for t in A & L:
            tp[t] += 1
    out: Dict[str, Dict[str, float]] = {}
    for t in TOPICS:
        a = auto_cnt.get(t, 0)
        l = llm_cnt.get(t, 0)
        c = tp.get(t, 0)
        prec = c / a if a else 0.0
        rec = c / l if l else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        out[t] = {
            "support_auto": a,
            "support_llm": l,
            "tp": c,
            "precision": round(prec, 3),
            "recall": round(rec, 3),
            "f1": round(f1, 3),
        }
    return out


def primary_f1(rows: List[Dict]) -> Tuple[float, Dict[str, float]]:
    """top1 only — R10/R18/R23 비교용."""
    correct = 0
    per_topic: Dict[str, Dict[str, int]] = {
        t: {"a": 0, "l": 0, "tp": 0} for t in TOPICS
    }
    for r in rows:
        a = r["auto_topics"][0] if r["auto_topics"] else None
        l = r["llm_topics"][0] if r["llm_topics"] else None
        if a == l and a is not None:
            correct += 1
        if a in per_topic:
            per_topic[a]["a"] += 1
        if l in per_topic:
            per_topic[l]["l"] += 1
        if a == l and a in per_topic:
            per_topic[a]["tp"] += 1
    overall = round(correct / len(rows), 3) if rows else 0.0
    f1_map: Dict[str, float] = {}
    for t in TOPICS:
        a = per_topic[t]["a"]
        l = per_topic[t]["l"]
        c = per_topic[t]["tp"]
        prec = c / a if a else 0.0
        rec = c / l if l else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        f1_map[t] = round(f1, 3)
    return overall, f1_map


def overall_multilabel_metrics(rows: List[Dict]) -> Dict[str, float]:
    if not rows:
        return {
            "exact_match": 0.0,
            "partial_match": 0.0,
            "jaccard_avg": 0.0,
            "f1_micro_avg": 0.0,
        }
    n = len(rows)
    em = sum(r["row_metrics"]["exact"] for r in rows) / n
    pm = sum(r["row_metrics"]["partial"] for r in rows) / n
    jv = sum(r["row_metrics"]["jaccard"] for r in rows) / n
    fv = sum(r["row_metrics"]["f1_micro"] for r in rows) / n
    return {
        "exact_match": round(em, 3),
        "partial_match": round(pm, 3),
        "jaccard_avg": round(jv, 3),
        "f1_micro_avg": round(fv, 3),
    }


def set_size_distribution(rows: List[Dict]) -> Dict[str, Dict[int, int]]:
    auto_dist: Counter = Counter()
    llm_dist: Counter = Counter()
    for r in rows:
        auto_dist[len(set(r["auto_topics"]))] += 1
        llm_dist[len(set(r["llm_topics"]))] += 1
    return {
        "auto": dict(sorted(auto_dist.items())),
        "llm": dict(sorted(llm_dist.items())),
    }


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------
def write_jsonl(rows: List[Dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            payload = {
                "id": r["id"],
                "round": ROUND_LABEL,
                "tier": r["tier"],
                "auto_topics": r["auto_topics"],
                "llm_topics": r["llm_topics"],
                # R27 듀얼 축 — primary 별도 호출 결과 (있을 때만)
                "llm_primary_topics": r.get("llm_primary_topics", []),
                "exact": r["row_metrics"]["exact"],
                "partial": r["row_metrics"]["partial"],
                "jaccard": round(r["row_metrics"]["jaccard"], 3),
                "f1_micro": round(r["row_metrics"]["f1_micro"], 3),
            }
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_report(
    out_md: str,
    out_json: str,
    rows: List[Dict],
    overall: Dict[str, float],
    per_topic: Dict[str, Dict[str, float]],
    macro_f1: float,
    prim_overall: float,
    prim_per_topic: Dict[str, float],
    set_dist: Dict[str, Dict[int, int]],
    mismatches: List[Dict],
    model_name: str,
) -> None:
    n = len(rows)
    n_multi = sum(1 for r in rows if r["tier"] == "multi")
    n_single = n - n_multi

    lines: List[str] = []
    lines.append(
        f"# Topic 분류기 Multi-label Joint F1 — {ROUND_LABEL} "
        f"({date.today().isoformat()})\n"
    )
    lines.append(f"- 프롬프트 버전: `{PROMPT_V}` (axis=`{AXIS}`)")
    lines.append(f"- 평가 모델: `{model_name}`")
    lines.append(
        f"- 샘플: {n}건 (multi={n_multi} / single={n_single})"
    )
    lines.append(f"- 평가: auto_set = `voc.topics`, llm_set = LLM JSON 응답")
    if AXIS == "both" and PROMPT_V == "v3.1":
        lines.append(
            "- **R27 듀얼 축**: primary 는 단일-라벨 prompt (temp 0.0, max_tok 30, "
            "max_retries 1), multi 는 v3 prompt (temp 0.2, max_tok 100) 별도 호출"
        )
    lines.append("")

    lines.append("## Overall Multi-label Metrics\n")
    lines.append("| 지표 | 값 |")
    lines.append("|---|---:|")
    lines.append(f"| Exact match (set equality) | **{overall['exact_match']:.3f}** |")
    lines.append(f"| Partial match (∩ ≥ 1) | **{overall['partial_match']:.3f}** |")
    lines.append(f"| Jaccard 평균 | **{overall['jaccard_avg']:.3f}** |")
    lines.append(f"| F1-micro 평균 (row 단위) | **{overall['f1_micro_avg']:.3f}** |")
    lines.append(f"| F1-macro (per-topic 평균) | **{macro_f1:.3f}** |")
    lines.append("")

    lines.append("## Primary-label F1 vs R10 / R18 / R23 / R24\n")
    lines.append(
        f"- {ROUND_LABEL} primary 정확도 (top1 only): **{prim_overall:.3f}**"
    )
    lines.append(
        f"- R10 0.678 / R18 v1 0.640 / R23 0.406 / R24 0.430 / R25 0.500 / "
        f"R26 0.450 / **{ROUND_LABEL} {prim_overall:.3f}**\n"
    )
    lines.append(
        f"| topic | {ROUND_LABEL} primary F1 | {ROUND_LABEL} multi F1 "
        "| Δ(multi-primary) |"
    )
    lines.append("|---|---:|---:|---:|")
    for t in TOPICS:
        pf = prim_per_topic.get(t, 0.0)
        mf = per_topic[t]["f1"]
        d = mf - pf
        sign = "+" if d >= 0 else ""
        lines.append(f"| {t} | {pf:.3f} | {mf:.3f} | {sign}{d:.3f} |")
    lines.append("")

    lines.append("## Per-topic Multi-label F1 (set 기반)\n")
    lines.append("| topic | auto_support | llm_support | TP | precision | recall | F1 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for t in TOPICS:
        m = per_topic[t]
        lines.append(
            f"| {t} | {m['support_auto']} | {m['support_llm']} | {m['tp']} "
            f"| {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} |"
        )
    lines.append("")

    lines.append("## Set 크기 분포\n")
    lines.append("| 크기 | auto | llm |")
    lines.append("|---|---:|---:|")
    sizes = sorted(set(list(set_dist["auto"].keys()) + list(set_dist["llm"].keys())))
    for k in sizes:
        a = set_dist["auto"].get(k, 0)
        l = set_dist["llm"].get(k, 0)
        lines.append(f"| {k} | {a} | {l} |")
    lines.append("")

    lines.append("## 잘못 분류 예시 (Jaccard < 0.5, 최대 12건)\n")
    if not mismatches:
        lines.append("- (없음)\n")
    else:
        for b in mismatches:
            lines.append(
                f"- id={b['id']} | auto=`{b['auto']}` → llm=`{b['llm']}` | "
                f"jacc={b['jaccard']:.2f} | \"{b['content']}\""
            )
        lines.append("")

    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    payload = {
        "date": date.today().isoformat(),
        "round": ROUND_LABEL,
        "prompt_version": PROMPT_V,
        "axis": AXIS,
        "model": model_name,
        "n_total": n,
        "n_multi": n_multi,
        "n_single": n_single,
        "overall_multilabel": overall,
        "macro_f1_multilabel": round(macro_f1, 3),
        "primary_overall": prim_overall,
        "primary_per_topic_f1": prim_per_topic,
        "per_topic_multilabel": per_topic,
        "set_size_distribution": set_dist,
        "baselines": {
            "R10_primary_overall": R10_OVERALL_PRIMARY,
            "R18_primary_overall": R18_OVERALL_PRIMARY,
            "R23_primary_overall": R23_OVERALL_PRIMARY,
            "R24_primary_overall": 0.430,
            "R24_macro_f1": 0.539,
            "R25_primary_overall": 0.500,
            "R25_macro_f1": 0.651,
            "R26_primary_overall": 0.450,
            "R26_macro_f1": 0.658,
        },
        "mismatches": mismatches,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def collect_mismatches(rows: List[Dict], limit: int = 12) -> List[Dict]:
    bad = [r for r in rows if r["row_metrics"]["jaccard"] < 0.5]
    return [
        {
            "id": r["id"],
            "auto": r["auto_topics"],
            "llm": r["llm_topics"],
            "jaccard": r["row_metrics"]["jaccard"],
            "content": (r["content"] or "")[:200],
        }
        for r in bad[:limit]
    ]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
async def main() -> int:
    rows = await sample_multilabel(SEED, N_MULTI, N_SINGLE)
    if not rows:
        log.error("샘플 0건 — 종료")
        return 1

    from openai import OpenAI
    client = OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL, timeout=60)

    log.info(
        "프롬프트 버전=%s round=%s axis=%s", PROMPT_V, ROUND_LABEL, AXIS
    )
    dual = (AXIS == "both" and PROMPT_V == "v3.1")
    for i, r in enumerate(rows, 1):
        if dual:
            # R27 듀얼 축 — primary 호출 (단일-라벨, temp 0.0) + multi 호출
            raw_p = call_llm(
                client, r["content"], OLLAMA_EVAL_MODEL,
                version=PROMPT_V, axis="primary",
            )
            primary_topics = parse_llm_topics(raw_p or "")
            r["llm_primary_topics"] = primary_topics[:1]  # top1 강제
            r["llm_primary_raw"] = raw_p or ""

            raw_m = call_llm(
                client, r["content"], OLLAMA_EVAL_MODEL,
                version=PROMPT_V, axis="multi",
            )
            r["llm_raw"] = raw_m or ""
            r["llm_topics"] = parse_llm_topics(raw_m or "")
            r["row_metrics"] = row_metrics(r["auto_topics"], r["llm_topics"])
        else:
            raw = call_llm(
                client, r["content"], OLLAMA_EVAL_MODEL,
                version=PROMPT_V, axis="multi",
            )
            r["llm_raw"] = raw or ""
            r["llm_topics"] = parse_llm_topics(raw or "")
            r["llm_primary_topics"] = r["llm_topics"][:1]  # 동일 호출 재사용
            r["row_metrics"] = row_metrics(r["auto_topics"], r["llm_topics"])

        if i % 10 == 0:
            log.info(
                "[%d/%d] auto=%s llm=%s prim=%s jacc=%.2f",
                i, len(rows),
                r["auto_topics"], r["llm_topics"],
                r.get("llm_primary_topics", []),
                r["row_metrics"]["jaccard"],
            )

    overall = overall_multilabel_metrics(rows)
    per_topic = per_topic_multilabel_f1(rows)
    macro_f1 = (
        sum(per_topic[t]["f1"] for t in TOPICS) / len(TOPICS)
        if TOPICS else 0.0
    )

    # R27 듀얼 축 — primary 지표는 *별도 호출 결과* 로 계산
    if dual:
        primary_rows = [
            {
                "auto_topics": r["auto_topics"],
                "llm_topics": r["llm_primary_topics"],
            }
            for r in rows
        ]
        prim_overall, prim_per_topic = primary_f1(primary_rows)
    else:
        prim_overall, prim_per_topic = primary_f1(rows)
    set_dist = set_size_distribution(rows)
    mm = collect_mismatches(rows, limit=12)

    today = date.today().isoformat()
    out_md = os.path.join(OUT_DIR, f"topic_eval_{today}{OUT_SUFFIX}.md")
    out_json = os.path.join(OUT_DIR, f"topic_eval_{today}{OUT_SUFFIX}.json")
    out_jsonl = os.path.join(OUT_DIR, f"topic_eval_{today}{OUT_SUFFIX}.jsonl")

    write_jsonl(rows, out_jsonl)
    write_report(
        out_md, out_json, rows, overall, per_topic, macro_f1,
        prim_overall, prim_per_topic, set_dist, mm, OLLAMA_EVAL_MODEL,
    )
    log.info(
        "완료 — exact=%.3f partial=%.3f jacc=%.3f f1m=%.3f macro=%.3f prim=%.3f",
        overall["exact_match"], overall["partial_match"],
        overall["jaccard_avg"], overall["f1_micro_avg"],
        macro_f1, prim_overall,
    )
    log.info("보고서: %s", out_md)
    log.info("JSONL: %s", out_jsonl)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
