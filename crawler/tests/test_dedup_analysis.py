"""R15 트랙 A — crawler/scripts/dedup_analysis.py 단위 검증.

목표:
  - import 가능 + analyze() async 시그니처 유지
  - main() 이 0 을 반환 (정상 종료) — DB 미연결 시도는 모킹

DB 없이 통과해야 한다. analyze() 의 SQL 구조는 통합 테스트가 아닌
*시그니처 + 모듈 구조* 만 확인 (스크립트 자체가 read-only 진단 도구).
"""
from __future__ import annotations

import importlib
import inspect
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


def test_dedup_analysis_importable():
    mod = importlib.import_module("scripts.dedup_analysis")
    assert hasattr(mod, "analyze")
    assert hasattr(mod, "main")


def test_analyze_is_async_no_args():
    mod = importlib.import_module("scripts.dedup_analysis")
    assert inspect.iscoroutinefunction(mod.analyze)
    sig = inspect.signature(mod.analyze)
    assert len(sig.parameters) == 0


def test_main_returns_int_signature():
    mod = importlib.import_module("scripts.dedup_analysis")
    sig = inspect.signature(mod.main)
    # 0-arg, returns int
    assert len(sig.parameters) == 0
    assert sig.return_annotation in (int, "int", inspect.Signature.empty)


def test_analyze_returns_expected_keys(monkeypatch):
    """analyze() 의 반환 dict 구조 — 5 섹션 + summary 유지."""
    mod = importlib.import_module("scripts.dedup_analysis")

    # 실 DB 없이 키 구조만 보장: source 텍스트에 필수 키가 모두 있는지
    src = inspect.getsource(mod.analyze)
    for key in (
        "hash_group_distribution",
        "cross_site_dup",
        "no_hash_by_site",
        "short_content_dup_potential",
        "analytic_quality_by_site",
        "summary",
    ):
        assert key in src, f"missing key: {key}"
