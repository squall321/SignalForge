"""FMKorea + Tor SOCKS5 graceful 통합 단위 테스트 — Harvest 4 트랙 H2 (CASE B).

목적
----
Host (apptainer 외부) 에 Tor 가 설치되어 있지 않은 환경에서
``FMKOREA_USE_PROXY=true`` 를 켜더라도 fmkorea 크롤러가 다음을 만족해야 한다:

1. proxy_pool.build_proxy_client_kwargs("FMKOREA") 가 빈 dict 를 반환 (probe 실패 폴백).
2. 빈 kwargs 를 httpx.AsyncClient 에 펼쳐도 TypeError 발생 없음.
3. fmkorea.py 가 ``build_proxy_client_kwargs`` 를 import 해서 직접 호출 경로로
   안전하게 폴백할 수 있는 인터페이스를 유지.

CASE A (Tor 가용) 는 reports/fmkorea_policy.md 에 명시. 본 테스트는 CASE B 만 다룬다.
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from base import proxy_pool  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in (
        "FMKOREA_USE_PROXY",
        "FMKOREA_PROXY_URL",
        "FMKOREA_PROXY_VERIFY",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


def test_fmkorea_graceful_fallback_when_tor_unreachable(monkeypatch):
    """CASE B: FMKOREA_USE_PROXY=true 이지만 9050 포트 closed → kwargs 비어야 함."""
    monkeypatch.setenv("FMKOREA_USE_PROXY", "true")
    # 기본 URL 사용 (socks5://127.0.0.1:9050).  Discovery 결과 host 에 Tor 부재.

    # probe 실패를 강제 — 실제 host 가 Tor 를 설치하더라도 단위 테스트는 결정적.
    with patch.object(proxy_pool, "_probe_tcp", return_value=False):
        kwargs = proxy_pool.build_proxy_client_kwargs(prefix="FMKOREA")

    # 핵심 계약: 폴백 시 빈 dict — fmkorea.py 의 **proxy_kwargs 펼침이 무해해야 한다.
    assert kwargs == {}


def test_fmkorea_proxy_kwargs_spreadable_into_httpx_async_client():
    """fmkorea.py 의 다음 패턴이 환경에 상관없이 항상 동작해야 함.

        proxy_kwargs = build_proxy_client_kwargs(prefix="FMKOREA")
        async with httpx.AsyncClient(**proxy_kwargs, timeout=30.0): ...
    """
    import httpx

    # env 미설정 — 비활성 경로 (실측 그대로)
    kwargs = proxy_pool.build_proxy_client_kwargs(prefix="FMKOREA")
    assert kwargs == {}

    async def _smoke():
        async with httpx.AsyncClient(**kwargs, timeout=1.0) as cli:
            return cli is not None

    assert asyncio.run(_smoke()) is True


def test_fmkorea_module_imports_build_proxy_client_kwargs():
    """fmkorea.py 가 build_proxy_client_kwargs 를 실제로 import 해서 사용 중인지
    소스 레벨에서 확인 — 추후 리팩터링으로 graceful 인터페이스가 끊기면 즉시 감지."""
    from platforms import fmkorea as fmk

    # crawler/platforms/fmkorea.py 내부에 build_proxy_client_kwargs 호출 잔존 확인.
    src = fmk.__file__
    with open(src, "r", encoding="utf-8") as f:
        body = f.read()
    assert "build_proxy_client_kwargs" in body, (
        "fmkorea.py 가 build_proxy_client_kwargs 호출을 잃었음 — graceful 폴백 깨짐"
    )
    assert "prefix=\"FMKOREA\"" in body or "prefix='FMKOREA'" in body, (
        "fmkorea.py 가 FMKOREA prefix 를 명시적으로 사용하지 않음"
    )
