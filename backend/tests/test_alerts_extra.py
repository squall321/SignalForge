"""P4 트랙 D — alerts PATCH + channels endpoint smoke tests.

가동 중인 backend (http://127.0.0.1:8000) 에 HTTP 직접 호출.
test_alerts_endpoints.py 와 동일 패턴.
"""
import os
import pytest
import httpx


BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")


def _alive() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=1.5).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_channels_status_endpoint():
    with httpx.Client(base_url=BACKEND, timeout=10.0) as c:
        r = c.get("/api/v1/alerts/channels")
        assert r.status_code == 200
        body = r.json()
        # 스키마 검증
        assert "slack" in body and "websocket" in body
        assert {"enabled", "dry_run"} <= set(body["slack"].keys())
        assert "connections" in body["websocket"]
        # SLACK_WEBHOOK_URL 미설정 (현재 환경) → dry-run
        assert body["slack"]["dry_run"] is True
        assert body["slack"]["enabled"] is False
        assert isinstance(body["websocket"]["connections"], int)
        assert body["websocket"]["connections"] >= 0


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_patch_rule_toggle_and_threshold():
    """seed 룰 1번 (anomaly_z_high) 의 is_active 토글 + threshold 변경 후 원복."""
    with httpx.Client(base_url=BACKEND, timeout=10.0) as c:
        # 현재 상태 조회
        r0 = c.get("/api/v1/alerts/rules")
        assert r0.status_code == 200
        rule = next(x for x in r0.json() if x["name"] == "anomaly_z_high")
        rid = rule["id"]
        orig_active = rule["is_active"]
        orig_th = float(rule["threshold"])

        # PATCH — is_active 반전 + threshold 임시 변경
        r1 = c.patch(
            f"/api/v1/alerts/rules/{rid}",
            json={"is_active": not orig_active, "threshold": orig_th + 1.0},
        )
        assert r1.status_code == 200, r1.text
        out = r1.json()
        assert out["id"] == rid
        assert out["is_active"] == (not orig_active)
        assert abs(float(out["threshold"]) - (orig_th + 1.0)) < 1e-6

        # 원복
        r2 = c.patch(
            f"/api/v1/alerts/rules/{rid}",
            json={"is_active": orig_active, "threshold": orig_th},
        )
        assert r2.status_code == 200
        assert r2.json()["is_active"] is orig_active
        assert abs(float(r2.json()["threshold"]) - orig_th) < 1e-6


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_patch_rule_404_and_empty():
    with httpx.Client(base_url=BACKEND, timeout=10.0) as c:
        # 빈 patch → 400
        r0 = c.patch("/api/v1/alerts/rules/1", json={})
        assert r0.status_code == 400
        # 없는 id → 404
        r1 = c.patch("/api/v1/alerts/rules/999999", json={"is_active": True})
        assert r1.status_code == 404
