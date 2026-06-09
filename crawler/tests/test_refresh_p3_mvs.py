"""tasks.run_refresh_p3_mvs 단위 테스트.

전제: 0004_p3_objects 마이그레이션 적용 후 platform_health + country_daily MV 가
존재하며, 최소 1회 비-CONCURRENTLY REFRESH 가 완료된 상태(populated).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks import run_refresh_p3_mvs  # noqa: E402


def test_run_refresh_p3_mvs_returns_ok_for_both():
    """양 MV 모두 status=ok 로 반환되고 elapsed_ms 가 합리적 범위."""
    result = run_refresh_p3_mvs()
    assert result["status"] == "done", f"전체 작업 실패: {result}"

    for mv in ("platform_health", "country_daily"):
        assert mv in result, f"{mv} 결과 누락: {result}"
        sub = result[mv]
        assert sub["status"] == "ok", f"{mv} refresh 실패: {sub}"
        assert sub["elapsed_ms"] >= 0
        # 정상 환경(voc 114k+) 에서 두 MV 합계가 1초 미만이어야 한다.
        # 워크스테이션 부하 여유 두고 30초 상한.
        assert sub["elapsed_ms"] < 30_000, f"{mv} REFRESH 비정상적으로 느림: {sub}"


def test_refresh_p3_mvs_beat_registered():
    """beat_schedule 에 refresh-p3-mvs-30m 가 등록되어 있는지."""
    from celery_app import app

    assert "refresh-p3-mvs-30m" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["refresh-p3-mvs-30m"]
    assert entry["task"] == "tasks.run_refresh_p3_mvs"
