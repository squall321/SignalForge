"""
리포트 공통 헬퍼 — DB 연결 / asyncpg DSN / 출력 디렉토리 / Webhook 알림.

asyncpg 만 사용 (crawler 의존성에 이미 있음). psycopg 추가 설치 불필요.
"""
from __future__ import annotations

import os
import json
import logging
from pathlib import Path
from typing import Optional

import asyncpg
import httpx

logger = logging.getLogger(__name__)

# 출력 디렉토리: 프로젝트 루트의 reports/
# crawler/reports/_common.py → 두 단계 위가 SignalForge/
REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = REPO_ROOT / "reports"


def dsn() -> str:
    """asyncpg 용 DSN 문자열 생성 (backend .env 패턴 재사용)."""
    # backend/.env 는 SQLAlchemy 형식 (postgresql+asyncpg://...) 이라 asyncpg 가 직접 못 씀.
    # → 순수 host/port/user/pwd/db 환경변수로 조립.
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = int(os.getenv("POSTGRES_PORT", "5434"))
    user = os.getenv("POSTGRES_USER", "signalforge")
    pwd = os.getenv("POSTGRES_PASSWORD", "signalforge_pass")
    db = os.getenv("POSTGRES_DB", "signalforge")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"


async def connect() -> asyncpg.Connection:
    return await asyncpg.connect(dsn())


def ensure_dir() -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR


def send_alert(text: str) -> None:
    """ALERT_WEBHOOK_URL 가 있으면 단순 JSON {"text": ...} 로 POST.

    실패는 로그만 남기고 삼킴 — 리포트 생성 자체는 절대 막지 않는다.
    """
    url = os.getenv("ALERT_WEBHOOK_URL")
    if not url:
        return
    try:
        httpx.post(url, json={"text": text}, timeout=10.0)
    except Exception as e:  # pragma: no cover — 네트워크 의존
        logger.warning(f"alert webhook 실패: {e}")


def fmt_pct(n: float) -> str:
    """0~1 또는 0~100 둘 다 안전. None 안전."""
    if n is None:
        return "-"
    if abs(n) <= 1.0:
        n = n * 100.0
    return f"{n:+.1f}%" if n != 0 else "0.0%"


def fmt_delta(curr: int, prev: int) -> str:
    """전주 대비 증감 (절대 + 퍼센트)."""
    if prev == 0 and curr == 0:
        return "0 (0.0%)"
    if prev == 0:
        return f"+{curr} (NEW)"
    diff = curr - prev
    pct = (diff / prev) * 100.0
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff} ({sign}{pct:.1f}%)"
