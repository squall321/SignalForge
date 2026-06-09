"""Multi-label topic eval — Track A (R24, 2026-06-05).

핵심 단위:
1) ``parse_llm_topics`` — JSON 배열 응답을 정확히 파싱 + fallback substring 매칭
2) ``row_metrics`` / ``per_topic_multilabel_f1`` — set 기반 Jaccard / F1
   계산이 수학적으로 정확
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from scripts.topic_eval_multilabel import (  # noqa: E402
    parse_llm_topics,
    per_topic_multilabel_f1,
    row_metrics,
)


# ---------------------------------------------------------------------------
# 케이스 1 — parse_llm_topics: JSON 배열 + 잡음/설명 + fallback
# ---------------------------------------------------------------------------
class TestParseLlmTopics:
    def test_clean_json_array(self):
        # 14b 가 정상 JSON 배열로 답한 경우
        assert parse_llm_topics('["positive_general"]') == ["positive_general"]
        assert parse_llm_topics('["price_purchase", "comparison"]') == [
            "price_purchase",
            "comparison",
        ]
        assert parse_llm_topics(
            '["experience", "negative_general", "service_repair"]'
        ) == ["experience", "negative_general", "service_repair"]

    def test_json_array_in_noisy_response(self):
        # 모델이 설명을 덧붙인 경우 — JSON 부분만 추출
        raw = '답: ["question", "comparison"] (질문이면서 비교)'
        assert parse_llm_topics(raw) == ["question", "comparison"]

    def test_json_array_invalid_label_filtered(self):
        # 사전에 없는 라벨은 무시
        raw = '["positive_general", "unknown_x", "experience"]'
        assert parse_llm_topics(raw) == ["positive_general", "experience"]

    def test_json_array_dedup(self):
        # 중복 라벨은 제거
        raw = '["question", "question", "experience"]'
        assert parse_llm_topics(raw) == ["question", "experience"]

    def test_json_array_max_3(self):
        # 4 개 이상 응답 시 앞 3 개만
        raw = (
            '["positive_general", "experience", "comparison", '
            '"price_purchase"]'
        )
        assert parse_llm_topics(raw) == [
            "positive_general",
            "experience",
            "comparison",
        ]

    def test_fallback_substring(self):
        # JSON 이 깨졌을 때 — 텍스트에서 라벨 substring 추출
        raw = "이 글은 question 같고 comparison 도 있어요"
        assert parse_llm_topics(raw) == ["question", "comparison"]

    def test_fallback_longer_label_priority(self):
        # 긴 라벨 우선 — 'positive_general' vs 'general'
        # ('general' 자체는 라벨이 아니므로 안전, 실제로는
        #  'negative_general' 과 'positive_general' 중복 매칭 가드 확인)
        raw = "정답: positive_general 만"
        assert parse_llm_topics(raw) == ["positive_general"]

    def test_empty_response(self):
        assert parse_llm_topics("") == []
        assert parse_llm_topics("   ") == []
        # JSON 빈 배열
        assert parse_llm_topics("[]") == []
        # 사전에 없는 라벨만 있는 응답 — 빈 리스트
        assert parse_llm_topics('["unknown1", "unknown2"]') == []


# ---------------------------------------------------------------------------
# 케이스 2 — row_metrics & per_topic_multilabel_f1: 수학적 정합성
# ---------------------------------------------------------------------------
class TestMultilabelMetrics:
    def test_row_metrics_exact_match(self):
        m = row_metrics(["question", "comparison"], ["question", "comparison"])
        assert m["exact"] == 1.0
        assert m["partial"] == 1.0
        assert m["jaccard"] == 1.0
        assert m["f1_micro"] == 1.0

    def test_row_metrics_partial(self):
        # auto={A,B}, llm={A,C} → ∩={A}=1, ∪={A,B,C}=3
        m = row_metrics(["positive_general", "experience"], ["positive_general", "question"])
        assert m["exact"] == 0.0
        assert m["partial"] == 1.0
        assert m["jaccard"] == pytest.approx(1 / 3, abs=1e-3)
        # F1_micro = 2·1 / (2+2) = 0.5
        assert m["f1_micro"] == pytest.approx(0.5, abs=1e-3)

    def test_row_metrics_disjoint(self):
        # ∩=0
        m = row_metrics(["positive_general"], ["negative_general"])
        assert m["exact"] == 0.0
        assert m["partial"] == 0.0
        assert m["jaccard"] == 0.0
        assert m["f1_micro"] == 0.0

    def test_row_metrics_subset(self):
        # auto={A,B,C}, llm={A} → ∩=1, ∪=3
        m = row_metrics(
            ["positive_general", "experience", "comparison"],
            ["positive_general"],
        )
        assert m["exact"] == 0.0
        assert m["partial"] == 1.0
        assert m["jaccard"] == pytest.approx(1 / 3, abs=1e-3)
        # F1_micro = 2·1 / (3+1) = 0.5
        assert m["f1_micro"] == pytest.approx(0.5, abs=1e-3)

    def test_row_metrics_empty_llm(self):
        m = row_metrics(["question"], [])
        assert m["exact"] == 0.0
        assert m["partial"] == 0.0
        assert m["jaccard"] == 0.0
        assert m["f1_micro"] == 0.0

    def test_per_topic_f1_micro(self):
        # 3 행 — question 의 precision/recall/F1 계산 검증
        rows = [
            # row1: auto={Q,C}, llm={Q}    → Q TP, C FN
            {"auto_topics": ["question", "comparison"], "llm_topics": ["question"]},
            # row2: auto={Q},   llm={Q,P}  → Q TP, P FP
            {
                "auto_topics": ["question"],
                "llm_topics": ["question", "price_purchase"],
            },
            # row3: auto={C},   llm={C}    → C TP
            {"auto_topics": ["comparison"], "llm_topics": ["comparison"]},
        ]
        m = per_topic_multilabel_f1(rows)
        # question: support_auto=2, support_llm=2, tp=2 → P=R=F1=1.0
        assert m["question"]["support_auto"] == 2
        assert m["question"]["support_llm"] == 2
        assert m["question"]["tp"] == 2
        assert m["question"]["precision"] == 1.0
        assert m["question"]["recall"] == 1.0
        assert m["question"]["f1"] == 1.0
        # comparison: auto=2, llm=1, tp=1 → P=0.5, R=1.0, F1=2*0.5*1/1.5=0.667
        assert m["comparison"]["support_auto"] == 2
        assert m["comparison"]["support_llm"] == 1
        assert m["comparison"]["tp"] == 1
        assert m["comparison"]["precision"] == 0.5
        assert m["comparison"]["recall"] == 1.0
        assert m["comparison"]["f1"] == pytest.approx(0.667, abs=1e-3)
        # price_purchase: auto=0, llm=1, tp=0 → P=R=F1=0
        assert m["price_purchase"]["support_auto"] == 0
        assert m["price_purchase"]["support_llm"] == 1
        assert m["price_purchase"]["tp"] == 0
        assert m["price_purchase"]["precision"] == 0.0
        assert m["price_purchase"]["recall"] == 0.0
        assert m["price_purchase"]["f1"] == 0.0
