"""tasks.refresh_mv_voc_daily 단위 테스트.

실 DB(127.0.0.1:5434)에 mv_voc_daily 가 존재한다는 전제 (P1-3 마이그레이션 적용 후).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks import refresh_mv_voc_daily  # noqa: E402


def test_refresh_mv_voc_daily_returns_ok():
    """REFRESH 가 ok 상태로 반환되고 elapsed_ms 가 양수인지 확인."""
    result = refresh_mv_voc_daily()
    assert result["status"] == "ok", f"refresh 실패: {result}"
    assert result["elapsed_ms"] >= 0
    # 정상 환경에서는 100k voc → mv 2~3k, 1초 미만이어야 한다.
    # 워크스테이션 부하 여유 두고 30초 상한.
    assert result["elapsed_ms"] < 30_000, f"REFRESH 가 비정상적으로 느림: {result['elapsed_ms']}ms"


def test_refresh_mv_voc_daily_beat_registered():
    """beat_schedule 에 refresh-mv-voc-daily-30m 가 등록되어 있는지."""
    from celery_app import app

    assert "refresh-mv-voc-daily-30m" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["refresh-mv-voc-daily-30m"]
    assert entry["task"] == "tasks.refresh_mv_voc_daily"
