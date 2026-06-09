"""
Compare Insight — 두 제품 이상의 핵심 지표를 LLM(14b 기대) 으로 비교 요약.

backend /api/v1/insights/compare-llm 가 호출하는 비즈니스 로직.
crawler/insight 패키지의 다른 모듈(grounding, llm_provider) 과 동일한 규칙을 따른다.

흐름:
    payload (제품별 KPI · 카테고리 · 부정 키워드)
      → metrics_to_markdown 표
      → LLM (tier='auto' → external → high(14b) → fast)
      → grounding score 검증 (낮으면 1회 재요청)
      → narrative (str)

payload schema (입력):
    {
      "period_days": 30,
      "products": [
        {
          "code": "GS25",
          "name_ko": "갤럭시 S25",
          "count": 12345,
          "sent_avg": -0.02,
          "neg_count": 1234,
          "pos_count": 4567,
          "top_categories": [{"code": "price", "name_ko": "가격", "n": 800}, ...],
          "neg_keywords": [{"keyword": "발열", "n": 42}, ...],
        },
        ...
      ],
    }

출력:
    (narrative: str, grounding_score: float)
    LLM 실패 → narrative=None, grounding_score=0.0
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

from insight.grounding import (  # noqa: E402
    metrics_to_markdown,
    validate_response,
)
from insight.llm_provider import LLMProvider, get_provider  # noqa: E402


logger = logging.getLogger(__name__)


# 비교 LLM 전용 시스템 hint — daily_insight 와 동일한 grounding 규칙을 따르되,
# "두 제품 간 비교" 라는 task 를 명시. 실제 system prompt 는 llm_provider 의
# SYSTEM_PROMPT_KO 가 우선 적용되고, 이 문자열은 instructions 안에 endorsement 로 합쳐진다.
COMPARE_SCHEMA_DESC = (
    "SignalForge 제품 비교 — N개 제품의 핵심 KPI(건수/감성/부정 비율/카테고리 TOP/"
    "부정 키워드 TOP) 가 같은 표 안에 정렬돼 있습니다. 표는 절대 변형하지 마세요."
)


def build_compare_prompt_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """compare payload → grounding.metrics_to_markdown 이 이해하는 dict 로 정규화.

    daily_insight 의 _metrics_to_payload 와 같은 역할.
    여기서는 by_product / by_category_neg / top_negative 슬롯에 데이터를 맞춰 넣어
    grounding 의 기존 검증 로직(숫자/키워드 hit)을 그대로 활용한다.
    """
    products = payload.get("products") or []
    period_days = int(payload.get("period_days") or 30)
    if not isinstance(products, list):
        products = []

    by_product = []
    by_category_neg_agg: Dict[str, Dict[str, Any]] = {}
    top_negative_terms: List[Dict[str, Any]] = []
    total_count = 0
    for p in products:
        code = p.get("code") or ""
        name_ko = p.get("name_ko") or code
        n = int(p.get("count") or 0)
        neg = int(p.get("neg_count") or 0)
        pos = int(p.get("pos_count") or 0)
        total_count += n
        by_product.append({
            "code": code,
            "name_ko": name_ko,
            "n": n,
            "neg": neg,
            "pos": pos,
        })
        for cat in (p.get("top_categories") or [])[:3]:
            ckey = str(cat.get("code") or cat.get("name_ko") or "")
            if not ckey:
                continue
            cur = by_category_neg_agg.setdefault(
                ckey,
                {"code": ckey, "name_ko": cat.get("name_ko") or ckey, "n": 0},
            )
            cur["n"] += int(cat.get("n") or 0)
        for kw in (p.get("neg_keywords") or [])[:5]:
            kw_text = str(kw.get("keyword") or "")
            if not kw_text:
                continue
            top_negative_terms.append({
                "product": code,
                "platform": None,
                "score": None,
                "text": f"{kw_text} ({kw.get('n') or 0}건)",
            })

    by_category_neg = sorted(
        by_category_neg_agg.values(), key=lambda x: -x["n"]
    )[:8]

    return {
        "target_date": f"period_days={period_days}",
        "total": total_count,
        "by_product": by_product,
        "by_category_neg": by_category_neg,
        "top_negative": top_negative_terms[:12],
    }


def _build_instructions(payload: Dict[str, Any]) -> str:
    """LLM 작성 지시문 — 비교 task 명시 + 표 인용 강제."""
    products = payload.get("products") or []
    codes = " vs ".join((p.get("code") or "?") for p in products) or "제품 비교"
    n = len(products)
    return (
        f"위 표를 근거로 {codes} ({n}개 제품) 의 한국어 '비교 분석' 보고서를 작성하세요.\n"
        "1) 4-6 문단, Markdown.\n"
        "2) 첫 문단 핵심 헤드라인 1-2문장 — 어느 제품이 어떤 면에서 강점/약점인지.\n"
        "3) 이어서 (a) 수집량/감성 평균 비교, (b) 부정 비율 차이와 의미, "
        "(c) 부정 카테고리 TOP 비교 (공통 vs 차별 항목), "
        "(d) 부정 키워드에서 읽히는 사용자 우려 차이.\n"
        "4) 모든 수치는 표에 적힌 값을 콤마 포함 형식으로 그대로 인용하고 **bold** 표기.\n"
        "5) 표에 없는 사실은 추측하지 말고 '데이터 없음' 으로 명시.\n"
        "6) 문체: 사실 중심·간결·'-습니다/입니다' 체.\n"
        "7) 마지막에 '## 권장 액션' 섹션으로 3-4 bullet — 각 제품별 우선순위."
    )


def generate_compare_narrative(
    payload: Dict[str, Any],
    *,
    provider: Optional[LLMProvider] = None,
    tier: str = "auto",
) -> Tuple[Optional[str], float, str]:
    """payload → (narrative, grounding_score, tier_label).

    실패 시 (None, 0.0, 'none') 반환.

    provider 를 외부에서 주입하면 그것을 사용 (테스트 친화). 아니면 get_provider(tier=tier).
    """
    products = payload.get("products") or []
    if not products or len(products) < 2:
        logger.info("compare payload: products < 2 → skip")
        return None, 0.0, "skipped"

    prov = provider or get_provider(tier=tier)
    if prov is None:
        logger.warning("compare LLM provider 미설정 — 비활성")
        return None, 0.0, "none"

    grounded_payload = build_compare_prompt_payload(payload)
    instructions = _build_instructions(payload)

    text_out = prov.summarize_json(
        grounded_payload,
        schema_desc=COMPARE_SCHEMA_DESC,
        instructions=instructions,
    )
    score = 0.0
    if text_out:
        score = validate_response(text_out, grounded_payload)
        if score < 0.4:
            logger.info("compare grounding 낮음(%.2f) — 강화 재요청", score)
            stronger = (
                instructions
                + "\n\n[CRITICAL] 직전 응답에서 수치 인용이 부족했습니다. "
                "표의 모든 제품 행의 건수/부정/긍정 값을 본문에 그대로 인용하세요."
            )
            retry = prov.summarize_json(
                grounded_payload,
                schema_desc=COMPARE_SCHEMA_DESC,
                instructions=stronger,
            )
            if retry:
                retry_score = validate_response(retry, grounded_payload)
                if retry_score > score:
                    text_out = retry
                    score = retry_score

    tier_label = getattr(prov, "tier_label", None) or getattr(prov, "name", "unknown")
    return text_out, float(score), str(tier_label)


__all__ = [
    "build_compare_prompt_payload",
    "generate_compare_narrative",
    "COMPARE_SCHEMA_DESC",
]
