"""ProxyPool — Tor SOCKS5 + Optional HTTP Proxy 인터페이스.

Harvest 3p 트랙 P1: FMKorea 등 IP 단위로 차단된 사이트 대상 회복 헬퍼.
UA 회전이 무효한 경우 (PHPSESSID 페어 인증 / IP 레이트리밋) IP 경로 변경이 필요.

설계 원칙
---------
- **graceful**: env 토글 (`FMKOREA_USE_PROXY=true`) 가 켜져 있고 동시에
  실제 프록시 endpoint 가 응답할 때만 활성화.  미설정 또는 응답 실패 시
  ``None`` 을 반환 → 호출자가 직접 호출 경로로 자동 폴백.
- **읽기 전용**: DB / 파일 mutation 없음.  audit JSONL 은 호출자 책임.
- **단일 의존**: ``httpx`` 만 사용.  Tor 가동 여부는 ``socket`` 으로 cheap probe.

사용 예
-------
    from base.proxy_pool import build_proxy_client_kwargs

    extra = build_proxy_client_kwargs(prefix="FMKOREA")
    if extra:
        async with httpx.AsyncClient(**extra, ...) as client: ...
    else:
        async with httpx.AsyncClient(...) as client: ...   # 직접 호출

ENV 키 (prefix 인자로 sharding)
- ``<PREFIX>_USE_PROXY``     "true" 일 때만 활성화 (기본 false)
- ``<PREFIX>_PROXY_URL``     명시 override.  예: socks5://127.0.0.1:9050
- ``<PREFIX>_PROXY_VERIFY``  "false" 면 SSL verify 끔 (기본 true)

URL 우선순위
- ``<PREFIX>_PROXY_URL`` 명시값
- 기본 Tor SOCKS5: ``socks5://127.0.0.1:9050``  (Tor 표준 포트)
"""
from __future__ import annotations

import logging
import os
import socket
from typing import Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_TOR_PROXY = "socks5://127.0.0.1:9050"
PROBE_TIMEOUT_SEC = 1.0


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _probe_tcp(host: str, port: int, timeout: float = PROBE_TIMEOUT_SEC) -> bool:
    """proxy host:port 가 TCP 응답하는지 cheap 체크.  실패해도 raise 안 함."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def resolve_proxy_url(prefix: str = "FMKOREA") -> Optional[str]:
    """env 와 probe 결과를 종합해 사용 가능한 proxy URL 결정.

    Returns
    -------
    str | None
        활성 proxy URL.  비활성/실패 시 ``None``.
    """
    if not _env_bool(f"{prefix}_USE_PROXY", default=False):
        return None

    url = os.getenv(f"{prefix}_PROXY_URL", "").strip() or DEFAULT_TOR_PROXY
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port

    if not host or not port:
        logger.warning(
            "[proxy_pool] %s_PROXY_URL malformed (%s) — fallback to direct",
            prefix, url,
        )
        return None

    if not _probe_tcp(host, port):
        logger.warning(
            "[proxy_pool] %s proxy %s:%d unreachable — fallback to direct",
            prefix, host, port,
        )
        return None

    logger.info("[proxy_pool] %s using proxy %s", prefix, url)
    return url


def build_proxy_client_kwargs(prefix: str = "FMKOREA") -> Dict[str, object]:
    """httpx.AsyncClient(**kwargs) 에 합칠 dict 반환.

    환경/probe 가 비활성이면 빈 dict 반환 → 호출자가 그대로 펼치면
    직접 호출 경로가 됨 (graceful 폴백).
    """
    url = resolve_proxy_url(prefix=prefix)
    if not url:
        return {}
    kwargs: Dict[str, object] = {"proxy": url}
    if not _env_bool(f"{prefix}_PROXY_VERIFY", default=True):
        kwargs["verify"] = False
    return kwargs


def is_proxy_active(prefix: str = "FMKOREA") -> bool:
    """편의 진단 — 현재 prefix 의 proxy 가 가동 가능 상태인가."""
    return resolve_proxy_url(prefix=prefix) is not None
