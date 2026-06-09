"""topic_eval.py 골격 단위 테스트 (Track D, R9, 2026-06-04).

DB·LLM 무의존. 순수 함수 (parse_llm_label / per_topic_metrics /
confusion / overall_accuracy / reinforce_advice) 동작만 검증.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from topic_eval import (  # noqa: E402
    confusion,
    overall_accuracy,
    parse_llm_label,
    per_topic_metrics,
    reinforce_advice,
    TOPICS,
)


def test_parse_llm_label_clean_word():
    assert parse_llm_label("positive_general") == "positive_general"
    assert parse_llm_label("  QUESTION  ") == "question"


def test_parse_llm_label_with_prefix_and_noise():
    # 모델이 설명을 덧붙여도 단일 라벨로 환원
    assert parse_llm_label("답: comparison") == "comparison"
    assert parse_llm_label("price_purchase (가격 언급)") == "price_purchase"


def test_parse_llm_label_unknown_to_other():
    assert parse_llm_label("???") == "other"
    assert parse_llm_label("") == "other"


def test_metrics_perfect_run():
    """모든 LLM 라벨이 auto 와 동일 → precision/recall=1.0 (해당 topic 만)."""
    rows = [
        {"auto_primary": "question", "llm_label": "question", "id": 1, "content": "?"},
        {"auto_primary": "question", "llm_label": "question", "id": 2, "content": "?"},
        {"auto_primary": "experience", "llm_label": "experience", "id": 3, "content": "x"},
    ]
    m = per_topic_metrics(rows)
    assert m["question"]["precision"] == 1.0
    assert m["question"]["recall"] == 1.0
    assert m["experience"]["f1"] == 1.0
    # support 0 topic 은 0
    assert m["other" if "other" in m else "comparison"]["support"] == 0
    assert overall_accuracy(rows) == 1.0


def test_metrics_half_wrong():
    """auto=question, llm=other 인 경우 question precision=0.5."""
    rows = [
        {"auto_primary": "question", "llm_label": "question", "id": 1, "content": "?"},
        {"auto_primary": "question", "llm_label": "other", "id": 2, "content": "??"},
    ]
    m = per_topic_metrics(rows)
    assert m["question"]["support"] == 2
    assert m["question"]["correct"] == 1
    assert m["question"]["precision"] == 0.5
    # llm_count: question=1, other=1 → recall = 1/1 = 1.0
    assert m["question"]["recall"] == 1.0
    assert overall_accuracy(rows) == 0.5


def test_confusion_matrix_shape():
    rows = [
        {"auto_primary": "question", "llm_label": "question", "id": 1, "content": "?"},
        {"auto_primary": "question", "llm_label": "other", "id": 2, "content": "??"},
        {"auto_primary": "experience", "llm_label": "positive_general", "id": 3, "content": "good"},
    ]
    cm = confusion(rows)
    assert cm["question"]["question"] == 1
    assert cm["question"]["other"] == 1
    assert cm["experience"]["positive_general"] == 1


def test_reinforce_advice_threshold():
    """precision < 0.30 인 topic 만 advice 에 등장."""
    metrics = {t: {"support": 0, "precision": 0.0, "recall": 0.0, "f1": 0.0} for t in TOPICS}
    metrics["question"] = {"support": 10, "precision": 0.20, "recall": 0.5, "f1": 0.28}
    metrics["experience"] = {"support": 10, "precision": 0.50, "recall": 0.5, "f1": 0.5}
    advice = reinforce_advice(metrics)
    assert any("question" in a for a in advice)
    assert not any("experience" in a for a in advice)
