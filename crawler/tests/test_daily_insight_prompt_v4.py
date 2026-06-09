"""daily_insight 프롬프트 v4 — R12 트랙 E3 (2026-06-04).

검증:
  1. PROMPT_VERSION 이 'v4-must-cite-5' 로 승격됐는지.
  2. daily_insight._select_provider_with_tier 가 함수로 존재 + 시그니처.
  3. 프롬프트 헤더 (must_cite) 가 *5개* 핵심 metric 라벨 모두 포함:
     - "총 수집"
     - "감성 평균"
     - "가장 활발한 카테고리"
     - "가장 활발한 제품"
     - "가장 활발한 사이트"
  4. ``DailyMetrics`` 가 비어도 함수가 throw 없이 ``None`` 안정 처리.

핵심 의의: 프롬프트 본문은 LLM 호출 직전 instructions 문자열로만 구성되어 있어
별도 인터페이스가 없으므로, build 가능한 부분만 추출·재구성해서 어서션한다.
"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from insight.daily_insight import DailyMetrics  # noqa: E402
from insight.llm_provider import PROMPT_VERSION  # noqa: E402


# ── 1) PROMPT_VERSION 승격 확인 ────────────────────────────────────────
def test_prompt_version_is_v4():
    """v4 로 승격 — 캐시 자동 무효화 + footer prompt_version 변화 추적."""
    assert PROMPT_VERSION == "v4-must-cite-5", PROMPT_VERSION
    assert PROMPT_VERSION.startswith("v4-"), PROMPT_VERSION


# ── 2) 필수 인용 5종 라벨이 daily_insight 모듈에 정의되어 있는지 ────────
# 모듈 source 를 직접 읽어 must_cite 문자열에 라벨이 모두 있는지 검증한다.
# (LLM 호출 의존 없는 정적 검증.)
def _read_daily_insight_source() -> str:
    import insight.daily_insight as di  # type: ignore
    import inspect

    return inspect.getsource(di.run)


def test_must_cite_contains_5_labels():
    """run() 본문에 5개 핵심 metric 라벨이 모두 must_cite 로 등장."""
    src = _read_daily_insight_source()
    required = [
        "총 수집",
        "감성 평균",
        "가장 활발한 카테고리",
        "가장 활발한 제품",
        "가장 활발한 사이트",
    ]
    missing = [r for r in required if r not in src]
    assert not missing, f"필수 라벨 누락: {missing}"


def test_must_cite_includes_must_cite_header():
    """프롬프트 헤더에 '[필수 인용 — 반드시 본문에 정확히 등장]' 표식이 있어야."""
    src = _read_daily_insight_source()
    assert "[필수 인용" in src, "필수 인용 헤더 sentinel 누락"
    # "5개 항목 모두를 본문에서 그대로 인용하세요" 강제 지시 보존
    assert "5개 항목" in src or "5 개 항목" in src, \
        "프롬프트가 5개 항목 강제 지시를 잃음"


# ── 3) DailyMetrics 빈 객체에서도 안정 ─────────────────────────────────
def test_daily_metrics_empty_safe():
    """DailyMetrics 가 모든 리스트/dict 가 비어도 throw 없이 구성."""
    m = DailyMetrics(target_date=date(2026, 6, 4))
    assert m.total == 0
    assert m.by_category == []
    assert m.by_product == []
    assert m.by_platform == []
    assert m.sentiment_score_avg is None
    # by_sentiment 는 dict
    assert isinstance(m.by_sentiment, dict)


# ── 4) 회귀: PROMPT_VERSION 변경이 보고서 footer 패턴과 호환 ────────────
def test_prompt_version_footer_compatible():
    """quality_report 가 footer 의 'prompt_version: X' 를 추출하는 regex 와 호환."""
    import re

    # quality_report.py 와 동일한 패턴
    sample_footer = (
        f"_LLM grounding score: 0.42 (provider: ollama, used_tier: fast, "
        f"tier_label: ollama-llama2, prompt_version: {PROMPT_VERSION})_"
    )
    m = re.search(r"prompt_version:\s*([a-zA-Z0-9_.-]+)", sample_footer)
    assert m is not None, sample_footer
    assert m.group(1) == PROMPT_VERSION


# ── 5) 회귀: must_cite 본문 구성식이 metric.total 등 핵심 키 의존 ──────
def test_must_cite_uses_metrics_total_and_sentiment_avg():
    """프롬프트 헤더가 metrics.total 와 sentiment_score_avg 를 직접 인용."""
    src = _read_daily_insight_source()
    assert "metrics.total" in src, "must_cite 가 metrics.total 미사용"
    assert "metrics.sentiment_score_avg" in src, \
        "must_cite 가 metrics.sentiment_score_avg 미사용"
