"""topic_eval_v2.py 골격 단위 테스트 (Track D, R14, 2026-06-04).

DB·LLM 무의존. v2 가 더한 순수 함수 (topic_quota / regression_table)
및 100건 합산 검증.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.topic_eval_v2 import (  # noqa: E402
    topic_quota,
    regression_table,
    R10_F1,
    R10_OVERALL,
)
from scripts.topic_eval import TOPICS  # noqa: E402


def test_topic_quota_sums_to_100():
    """11 * 9 topics + 1 추가 = 100 정확히."""
    q = topic_quota(11, "question")
    assert sum(q.values()) == 100
    assert q["question"] == 12
    assert q["emotion_only"] == 11
    # 모든 topic 이 포함
    for t in TOPICS:
        assert t in q


def test_topic_quota_ignores_unknown_extra():
    """알 수 없는 extra_topic 은 무시 (총 9*11 = 99 만)."""
    q = topic_quota(11, "unknown_label")
    assert sum(q.values()) == 99
    for t in TOPICS:
        assert q[t] == 11


def test_regression_table_contains_all_topics_and_overall():
    """R13 결과가 R10 와 동일하다 가정 → Δ 0.000 행이 모두 등장, overall 행 포함."""
    metrics = {t: {"f1": R10_F1[t], "precision": 1.0, "recall": 1.0, "support": 11, "llm_count": 11, "correct": 11} for t in TOPICS}
    lines = regression_table(metrics, overall_now=R10_OVERALL)
    joined = "\n".join(lines)
    for t in TOPICS:
        assert t in joined
    assert "overall" in joined
    assert "+0.000" in joined  # Δ overall


def test_regression_table_shows_delta_sign():
    """R13 가 R10 보다 0.05 높다 → +0.050 행이 등장."""
    metrics = {t: {"f1": R10_F1[t] + 0.05, "precision": 1.0, "recall": 1.0, "support": 11, "llm_count": 11, "correct": 11} for t in TOPICS}
    lines = regression_table(metrics, overall_now=R10_OVERALL + 0.05)
    joined = "\n".join(lines)
    assert "+0.050" in joined
