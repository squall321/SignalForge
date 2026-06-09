"""Dual-axis topic eval — Track A v3.1 (R27, 2026-06-06).

R26 v3 는 multi-label 회수 (macro F1 0.651→0.658, +0.7pt) 에는 도움됐지만
primary top1 0.500→0.450 (-5pt) 회귀.

v3.1 듀얼 축:
  - primary 축 : 단일-라벨 prompt + temp 0.0 + max_tok 30 + max_retries 1
  - multi   축 : v3 multi prompt + temp 0.2 + max_tok 100 + max_retries 2

이 테스트는 *프롬프트와 sampling 파라미터가 축별로 분기되는지* 만 검증
(실제 LLM 호출은 R27 평가 스크립트에서 측정).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from scripts.topic_eval_multilabel import (  # noqa: E402
    build_prompt,
    llm_params,
)


class TestDualAxisRouting:
    def test_primary_axis_uses_single_label_template_and_deterministic_sampling(self):
        # v3.1 primary 축: temp 0.0, max_tok 30 (결정적 단답)
        temp, max_tok = llm_params("v3.1", axis="primary")
        assert temp == 0.0, "primary axis must be deterministic (temp 0.0)"
        assert max_tok == 30, "primary axis max_tok must be tight (30)"

        # prompt 자체가 *단일 라벨 강제* 문구를 포함
        prompt = build_prompt("S24U 1년 써본 결과 만족", version="v3.1", axis="primary")
        assert "1 개만" in prompt, "primary prompt must enforce single-label"
        assert "secondary 금지" in prompt, "primary prompt must forbid secondary"
        # 예시들도 모두 길이 1 의 JSON 배열
        assert '["experience"]' in prompt
        assert '["comparison"]' in prompt
        assert '["price_purchase"]' in prompt

    def test_multi_axis_reuses_v3_template_and_sampling(self):
        # v3.1 multi 축: temp 0.2, max_tok 100 (secondary 회수 유지)
        temp, max_tok = llm_params("v3.1", axis="multi")
        assert temp == 0.2, "multi axis keeps secondary recall sampling"
        assert max_tok == 100, "multi axis max_tok 100"

        # multi prompt 는 v3 multi prompt 와 동일 (macro F1 0.658 유지 목적)
        p_v31 = build_prompt("아이폰15 vs S24 비교", version="v3.1", axis="multi")
        p_v3 = build_prompt("아이폰15 vs S24 비교", version="v3", axis="multi")
        assert p_v31 == p_v3, "multi axis must reuse v3 prompt (macro F1 회복분 보존)"
        # primary prompt 와는 달라야 함 (R18 폭락 패턴 차단)
        p_primary = build_prompt(
            "아이폰15 vs S24 비교", version="v3.1", axis="primary"
        )
        assert p_v31 != p_primary, "primary와 multi prompt 분기 실패"
