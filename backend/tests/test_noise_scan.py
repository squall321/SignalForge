"""Track E — /api/v1/_internal/noise-scan endpoint live smoke test.

가동 중인 backend (http://127.0.0.1:8000) 에 직접 HTTP 호출.
conftest 의 engine.dispose autouse fixture 와 TestClient 가 cross-loop 충돌하므로
다른 endpoint 테스트(test_alerts_endpoints.py)와 동일한 live-server 패턴 사용.

검증:
  - 200 응답 + JSON 스키마 (hours, min_repeat, platform, count, patterns)
  - patterns[i] 가 (platform, preview, n) 키 보유, n >= min_repeat
  - Instiz 잠금 문구('회원만 볼 수', '1시간 내 작성') 가 결과에 부재 (필터 영구화 효과)
  - platform=instiz 필터링 동작
  - 외부 host 차단 (localhost-only 가드) — backend 가 127.0.0.1 로만 노출되므로
    여기서는 응답 200/스키마로 우회 검증.

실행:
    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_noise_scan.py -v
"""
import os

import httpx
import pytest


BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")

# Instiz 필터가 차단해야 하는 잠금 문구.
_INSTIZ_LOCK_PHRASES = ("회원만 볼 수", "1시간 내 작성")


def _alive() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=1.5).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_noise_scan_live():
    with httpx.Client(base_url=BACKEND, timeout=20.0) as c:
        # 1) 기본 호출 — 전체 플랫폼 24h min_repeat=10.
        r = c.get("/api/v1/_internal/noise-scan", params={"hours": 24, "min_repeat": 10, "limit": 20})
        assert r.status_code == 200, f"status={r.status_code} body={r.text}"
        body = r.json()
        assert set(body.keys()) >= {"hours", "min_repeat", "platform", "count", "patterns"}, body
        assert body["hours"] == 24
        assert body["min_repeat"] == 10
        assert body["platform"] is None
        assert isinstance(body["patterns"], list)
        assert body["count"] == len(body["patterns"])

        for p in body["patterns"]:
            assert set(p.keys()) >= {"platform", "preview", "n"}, p
            assert isinstance(p["platform"], str) and p["platform"]
            assert isinstance(p["preview"], str)
            assert isinstance(p["n"], int) and p["n"] >= 10

        # 2) Instiz 잠금 문구가 결과에 없어야 한다 (필터 영구화 효과).
        instiz_lock_leaks = [
            p for p in body["patterns"]
            if p["platform"] == "instiz" and any(ph in p["preview"] for ph in _INSTIZ_LOCK_PHRASES)
        ]
        assert not instiz_lock_leaks, f"Instiz 잠금 문구 누출: {instiz_lock_leaks}"

        # 3) platform=instiz 필터 동작.
        r2 = c.get(
            "/api/v1/_internal/noise-scan",
            params={"platform": "instiz", "hours": 24, "min_repeat": 5, "limit": 10},
        )
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["platform"] == "instiz"
        for p in body2["patterns"]:
            assert p["platform"] == "instiz", p
