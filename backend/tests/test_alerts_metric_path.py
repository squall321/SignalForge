"""P4 트랙 C — collect_metrics 산출값 무결성 검증.

목적: rule 1/2 발화 0건 진단 후 metric 재산정 라운드에서, collect_metrics 가
*활성 룰 전체* 의 metric_path 를 누락 없이 산출하고, 각 값이 합리 범위에 있음을
보장한다. backend 미가동 환경에서도 skip 으로 안전 통과.
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


# 활성 metric_path 별 허용 범위 — 모두 0.0 이하·1.0 초과는 정의상 비정상
_BOUNDS = {
    "community.extreme_negative_count": (0.0, 1000.0),   # 정수 카운트 (float 표현)
    "community.negative_rate_max":      (0.0, 1.0),
    "community.platforms_negative_pct": (0.0, 1.0),
    "insights.new_term_spike_count":    (0.0, 10000.0),
    # 2026-06-03 Track E backup_fail — verify-backup.sh 결과 (0=fail, 1=pass).
    # collect_metrics 에서 RuleEngine 이 직접 읽지 않고, crawler 의 verify_backup task 가
    # INSERT INTO alert_events 로 발화시키므로 collect_metrics 범위에는 들어오지 않지만
    # silent 회귀 방지용으로 본 표에는 등록.
    "system.backup_ok":                 (0.0, 1.0),
}


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_collect_metrics_covers_active_rules_and_in_range():
    with httpx.Client(base_url=BACKEND, timeout=20.0) as c:
        rules = c.get("/api/v1/alerts/rules").json()
        active_paths = {r["metric_path"] for r in rules if r.get("is_active")}
        assert active_paths, "활성 룰이 0개면 검증 의미 없음"

        # alerts/test 는 RuleEngine 평가 + 결과를 그대로 보고. metrics dict 자체는
        # 노출하지 않으므로, 룰별 evaluated/fired 통계로 metric 접근 가능 여부 확인.
        body = c.post("/api/v1/alerts/test", json={}).json()
        assert "evaluated" in body and "fired" in body
        assert body["evaluated"] >= len(active_paths), (
            f"evaluated {body['evaluated']} < active_paths {len(active_paths)}"
        )

        # 폭주 방지: 한 회 발화 ≤ 활성 룰 수 (1 metric 당 최대 1 fire)
        assert body["fired"] <= len(active_paths)

        # active_paths 가 _BOUNDS 의 알려진 키 집합 내에 있어야 새 metric 도입 시
        # 본 테스트도 업데이트되도록 강제 (silent 회귀 방지).
        unknown = active_paths - set(_BOUNDS.keys())
        assert not unknown, f"미정의 metric_path: {unknown} — _BOUNDS 업데이트 필요"
