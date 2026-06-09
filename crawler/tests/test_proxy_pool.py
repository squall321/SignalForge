"""proxy_pool 단위 테스트 — Harvest 3p 트랙 P1.

검증
----
1. env 미설정 → resolve_proxy_url None.
2. env=true 이지만 probe 실패 → None (graceful 폴백).
3. env=true + 사용 가능한 endpoint → 활성 URL 반환.
4. malformed URL → None.
5. build_proxy_client_kwargs — 비활성 시 {}, 활성 시 proxy 키 포함.
6. PROXY_VERIFY=false → verify=False 포함.
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from base import proxy_pool  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """매 테스트마다 prefix 환경변수 초기화."""
    for k in (
        "FMKOREA_USE_PROXY", "FMKOREA_PROXY_URL", "FMKOREA_PROXY_VERIFY",
        "TEST_USE_PROXY", "TEST_PROXY_URL", "TEST_PROXY_VERIFY",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


# ── 1) env 미설정 — 비활성 ───────────────────────────────────────────────
def test_resolve_returns_none_when_env_disabled():
    assert proxy_pool.resolve_proxy_url(prefix="FMKOREA") is None
    assert proxy_pool.build_proxy_client_kwargs(prefix="FMKOREA") == {}


# ── 2) env=true 이지만 probe 실패 — graceful 폴백 ────────────────────────
def test_resolve_returns_none_when_probe_fails(monkeypatch):
    monkeypatch.setenv("FMKOREA_USE_PROXY", "true")
    monkeypatch.setenv("FMKOREA_PROXY_URL", "socks5://127.0.0.1:9")  # closed port

    with patch.object(proxy_pool, "_probe_tcp", return_value=False):
        url = proxy_pool.resolve_proxy_url(prefix="FMKOREA")
    assert url is None
    # kwargs 도 빈 dict — 호출자가 펼쳐도 무해.
    with patch.object(proxy_pool, "_probe_tcp", return_value=False):
        assert proxy_pool.build_proxy_client_kwargs(prefix="FMKOREA") == {}


# ── 3) env=true + 사용 가능 endpoint — 활성 URL ──────────────────────────
def test_resolve_returns_url_when_probe_ok(monkeypatch):
    monkeypatch.setenv("FMKOREA_USE_PROXY", "true")
    monkeypatch.setenv("FMKOREA_PROXY_URL", "socks5://127.0.0.1:9050")

    with patch.object(proxy_pool, "_probe_tcp", return_value=True):
        url = proxy_pool.resolve_proxy_url(prefix="FMKOREA")
    assert url == "socks5://127.0.0.1:9050"

    with patch.object(proxy_pool, "_probe_tcp", return_value=True):
        kw = proxy_pool.build_proxy_client_kwargs(prefix="FMKOREA")
    assert kw == {"proxy": "socks5://127.0.0.1:9050"}


# ── 4) 기본값 (URL 미명시) — Tor 표준 endpoint ──────────────────────────
def test_resolve_uses_default_tor_when_no_url(monkeypatch):
    monkeypatch.setenv("FMKOREA_USE_PROXY", "true")
    # FMKOREA_PROXY_URL 미설정

    with patch.object(proxy_pool, "_probe_tcp", return_value=True):
        url = proxy_pool.resolve_proxy_url(prefix="FMKOREA")
    assert url == proxy_pool.DEFAULT_TOR_PROXY
    assert url.startswith("socks5://")


# ── 5) malformed URL — None ────────────────────────────────────────────
def test_resolve_handles_malformed_url(monkeypatch):
    monkeypatch.setenv("FMKOREA_USE_PROXY", "true")
    monkeypatch.setenv("FMKOREA_PROXY_URL", "not-a-url")

    assert proxy_pool.resolve_proxy_url(prefix="FMKOREA") is None


# ── 6) PROXY_VERIFY=false → verify=False 추가 ───────────────────────────
def test_verify_false_propagates(monkeypatch):
    monkeypatch.setenv("FMKOREA_USE_PROXY", "true")
    monkeypatch.setenv("FMKOREA_PROXY_VERIFY", "false")

    with patch.object(proxy_pool, "_probe_tcp", return_value=True):
        kw = proxy_pool.build_proxy_client_kwargs(prefix="FMKOREA")
    assert kw.get("proxy") == proxy_pool.DEFAULT_TOR_PROXY
    assert kw.get("verify") is False


# ── 7) prefix sharding — TEST 와 FMKOREA 가 독립 ────────────────────────
def test_prefix_independent(monkeypatch):
    monkeypatch.setenv("FMKOREA_USE_PROXY", "true")
    # TEST_USE_PROXY 는 미설정 → 비활성

    with patch.object(proxy_pool, "_probe_tcp", return_value=True):
        assert proxy_pool.is_proxy_active(prefix="FMKOREA") is True
        assert proxy_pool.is_proxy_active(prefix="TEST") is False


# ── 8) _probe_tcp — 닫힌 포트 False (실측 — fast, no env mock) ──────────
def test_probe_tcp_closed_port_returns_false():
    # 0 은 reserved — connect 시 즉시 fail.
    assert proxy_pool._probe_tcp("127.0.0.1", 1, timeout=0.2) is False


# ── 9) FMKorea 통합 — proxy_kwargs 가 httpx.AsyncClient 와 호환 ─────────
def test_proxy_kwargs_compatible_with_httpx():
    """build_proxy_client_kwargs() 결과를 httpx.AsyncClient 에 펼쳐도
    TypeError 가 발생하지 않아야 함 (graceful interface 계약)."""
    import httpx

    # 비활성 (env 없음) — {} 펼침
    kw = proxy_pool.build_proxy_client_kwargs(prefix="FMKOREA")

    async def _make():
        async with httpx.AsyncClient(**kw, timeout=1.0) as cli:
            return cli is not None

    assert asyncio.run(_make()) is True
