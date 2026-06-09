"""R14 트랙 A — crawler/scripts/dedup_voc.py 단위 검증.

목표:
  - BaseCrawler._content_hash 의 정의 (sha256 첫 16자, <30자 시 None) 보존.
  - dedup_voc 스크립트 import 가능 + measure_duplicate_rate 시그니처 유지.

DB 없이도 통과 가능한 정적 검증으로 한정 (CI/로컬 공통).
"""
from __future__ import annotations

import hashlib
import importlib
import os
import sys

# crawler 패키지 경로 등록
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from base.crawler import BaseCrawler  # noqa: E402


def _expected_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def test_content_hash_returns_first_16_of_sha256():
    body = "A" * 64
    assert BaseCrawler._content_hash(body) == _expected_hash(body)


def test_content_hash_skip_short_body():
    # 30자 미만 → None
    assert BaseCrawler._content_hash("hi") is None
    assert BaseCrawler._content_hash("x" * 29) is None
    # 정확히 30자 → 해시 산출
    s30 = "y" * 30
    assert BaseCrawler._content_hash(s30) == _expected_hash(s30)


def test_dedup_script_importable_and_has_measure_fn():
    mod = importlib.import_module("scripts.dedup_voc")
    assert hasattr(mod, "measure_duplicate_rate")
    assert hasattr(mod, "dedup_run")
    # measure_duplicate_rate 는 1개 인자 (db 세션) — sig 보존
    import inspect
    sig = inspect.signature(mod.measure_duplicate_rate)
    assert len(sig.parameters) == 1
