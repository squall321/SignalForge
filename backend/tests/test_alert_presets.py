"""P4.2 E5 — alerts 프리셋 endpoint smoke tests.

가동 중인 backend (http://127.0.0.1:8000) 에 HTTP 직접 호출.
test_alerts_endpoints.py 와 동일 패턴.

cleanup: 테스트 종료 시 DELETE /alerts/rules/{id} 로 생성한 룰 제거.

주의: FastAPI 는 응답 전송 후 dependency teardown 에서 session.commit() 을 호출한다.
같은 keep-alive 커넥션으로 곧바로 다음 요청을 보내면 commit 이 완료되기 전에 새 세션이
열려 직전 INSERT 가 보이지 않을 수 있다 (httpx 단일 client 재사용 시 재현).
→ POST 직후 짧은 sleep + 매 호출별 새 client 사용으로 안정화.
"""
import os
import time

import pytest
import httpx


BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")
EXPECTED_KEYS = {
    "high_burst_negative",
    "new_term_storm",
    "negative_rate_severe",
    "new_term_warning",
    "extreme_neg_singular",
}


def _alive() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=1.5).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_presets_list_returns_five():
    with httpx.Client(base_url=BACKEND, timeout=10.0) as c:
        r = c.get("/api/v1/alerts/presets")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 5
        keys = {p["key"] for p in body}
        assert keys == EXPECTED_KEYS
        # 스키마 — 첫 항목 필드 검증
        first = body[0]
        for f in ("key", "name", "metric_path", "op", "threshold", "severity", "cooldown_sec"):
            assert f in first, f


def _wait_visible(rule_id: int, present: bool, timeout: float = 2.0) -> bool:
    """get_db 의 응답-후-commit 패턴에 대비해, 룰의 가시성을 polling 한다."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with httpx.Client(base_url=BACKEND, timeout=5.0) as c:
            rules = c.get("/api/v1/alerts/rules").json()
            ids = {r["id"] for r in rules}
        if (rule_id in ids) == present:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_presets_apply_increments_rules_and_is_idempotent():
    """apply 호출 시 룰 생성 + 재호출 시 :exists 로 skip.

    대상: extreme_neg_singular (이름 '단일 플랫폼 위기' — seed 와 충돌 없음).
    선행 정리: 같은 이름의 잔존 룰이 있으면 먼저 삭제 후 visibility polling.

    검증 포인트:
    1) POST apply → created=1, skipped=[], 새 RuleOut 1개 (응답으로 직접 확인)
    2) 신규 룰이 GET /rules 에 등장 (polling — 응답 후 commit 대기 안전망)
    3) 동일 key 재호출 → created=0, skipped=["key:exists"]
    4) 알 수 없는 key → skipped=["key:unknown"], 200 유지
    """
    target_key = "extreme_neg_singular"
    target_name = "단일 플랫폼 위기"

    # 선행 정리 — 동일 이름 잔존 룰 제거 (있다면)
    with httpx.Client(base_url=BACKEND, timeout=5.0) as c:
        for rule in c.get("/api/v1/alerts/rules").json():
            if rule["name"] == target_name:
                c.delete(f"/api/v1/alerts/rules/{rule['id']}")
                _wait_visible(rule["id"], present=False)

    new_rule_id: int | None = None
    try:
        # (1) POST apply
        with httpx.Client(base_url=BACKEND, timeout=10.0) as c:
            r1 = c.post(
                "/api/v1/alerts/presets/apply",
                json={"keys": [target_key]},
            )
        assert r1.status_code == 200, r1.text
        body1 = r1.json()
        assert body1["requested"] == 1
        assert body1["created"] == 1
        assert body1["skipped"] == []
        assert len(body1["created_rules"]) == 1
        new_rule = body1["created_rules"][0]
        assert new_rule["name"] == target_name
        assert new_rule["metric_path"] == "community.extreme_negative_count"
        new_rule_id = new_rule["id"]

        # (2) GET 가시성
        assert _wait_visible(new_rule_id, present=True), "신규 룰이 listing 에 나타나지 않음"

        # (3) 재호출 → exists 로 skip
        with httpx.Client(base_url=BACKEND, timeout=10.0) as c:
            r2 = c.post(
                "/api/v1/alerts/presets/apply",
                json={"keys": [target_key]},
            )
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["created"] == 0
        assert body2["skipped"] == [f"{target_key}:exists"]

        # (4) 알 수 없는 key → :unknown 으로 skip (200)
        with httpx.Client(base_url=BACKEND, timeout=10.0) as c:
            r3 = c.post(
                "/api/v1/alerts/presets/apply",
                json={"keys": ["does_not_exist"]},
            )
        assert r3.status_code == 200
        assert r3.json()["skipped"] == ["does_not_exist:unknown"]
    finally:
        if new_rule_id is not None:
            with httpx.Client(base_url=BACKEND, timeout=5.0) as c:
                c.delete(f"/api/v1/alerts/rules/{new_rule_id}")
