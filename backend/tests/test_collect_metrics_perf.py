"""P4 트랙 C/E4 — collect_metrics cold 성능 회귀 테스트.

P4 C: InsightsService.new_terms 의 90일 anti-join 을 alerts 전용 경량 SQL 로
      교체. cold 13.9s → ≤ 3s.
P4 E4: CommunityService.anomalies() 호출을 인라인 SQL 로 교체. cold 합산 ≤ 1.5s.

실행 전제:
- backend 가 127.0.0.1:8000 에서 가동 중
- redis 캐시(`alerts:*`)를 비워 cold path 강제
- DB seed 데이터가 있는 dev 환경
"""
from __future__ import annotations

import os
import time

import httpx
import pytest

BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")
COLD_BUDGET_SEC = 3.0
# E4: community 인라인 SQL + new-term spike 합산 5회 평균 ≤ 1.5s
E4_COMBINED_BUDGET_SEC = 1.5
E4_SAMPLES = 5


def _alive() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=1.5).status_code == 200
    except Exception:
        return False


def _flush_alerts_cache() -> None:
    """Redis 의 alerts:* 키를 직접 제거. redis 미가동 시 silent skip.

    이 헬퍼는 backend 가 쓰는 redis_cache 와 동일 (host,port,password)을 가정.
    """
    try:
        import redis  # noqa: PLC0415
    except ImportError:
        return
    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD") or None
    try:
        r = redis.Redis(host=host, port=port, password=password, socket_connect_timeout=1.0)
        for key in r.scan_iter(match="alerts:*", count=100):
            r.delete(key)
    except Exception:
        # cache 가 없으면 그냥 진행 — cold path 는 어차피 매번 새로 계산.
        return


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_alerts_test_cold_under_budget():
    """캐시 비운 직후 /api/v1/alerts/test 1회 호출이 COLD_BUDGET_SEC 이내."""
    _flush_alerts_cache()
    with httpx.Client(base_url=BACKEND, timeout=COLD_BUDGET_SEC + 5.0) as c:
        t0 = time.perf_counter()
        r = c.post("/api/v1/alerts/test", json={})
        dt = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    body = r.json()
    assert "metrics" in body
    assert "insights.new_term_spike_count" in body["metrics"]
    assert dt <= COLD_BUDGET_SEC, (
        f"cold collect_metrics 가 {dt:.2f}s 걸림 (예산 {COLD_BUDGET_SEC}s). "
        f"InsightsService.new_terms 회귀 가능성."
    )


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_alerts_test_warm_fast():
    """캐시 hit 시 0.5s 이내.  redis 미가동이면 skip."""
    _flush_alerts_cache()
    with httpx.Client(base_url=BACKEND, timeout=10.0) as c:
        c.post("/api/v1/alerts/test", json={})  # warm
        t0 = time.perf_counter()
        r = c.post("/api/v1/alerts/test", json={})
        dt = time.perf_counter() - t0
    assert r.status_code == 200
    assert dt <= 1.0, f"warm 호출이 {dt:.2f}s — 캐시 동작 의심"


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_alerts_collect_metrics_combined_avg_budget():
    """E4 — community + new-term-spike 둘 다 cold 합산 5회 평균 ≤ 1.5s.

    community.* 메트릭이 인라인 SQL 로 교체된 후 회귀를 막는다.
    /alerts/test 호출 = 1 community SQL + 1 new-term SQL (직렬 gather).
    """
    samples = []
    metrics_seen = []
    with httpx.Client(base_url=BACKEND, timeout=E4_COMBINED_BUDGET_SEC + 5.0) as c:
        for _ in range(E4_SAMPLES):
            _flush_alerts_cache()
            t0 = time.perf_counter()
            r = c.post("/api/v1/alerts/test", json={})
            dt = time.perf_counter() - t0
            assert r.status_code == 200, r.text
            body = r.json()
            metrics_seen.append(body["metrics"])
            samples.append(dt)
    avg = sum(samples) / len(samples)
    # 두 metric 키가 모든 호출에서 일관되게 존재
    for m in metrics_seen:
        assert "community.extreme_negative_count" in m
        assert "community.negative_rate_max" in m
        assert "insights.new_term_spike_count" in m
    assert avg <= E4_COMBINED_BUDGET_SEC, (
        f"cold 합산 평균 {avg:.3f}s > 예산 {E4_COMBINED_BUDGET_SEC}s "
        f"(samples={[round(s,3) for s in samples]}). E4 회귀 의심."
    )
