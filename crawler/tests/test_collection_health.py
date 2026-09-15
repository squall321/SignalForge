"""insight.collection_health 단위 테스트 (1 케이스).

evaluate_violations — 임계 룰의 정확한 발화/미발화 확인:
  * baseline 0 사이트 → skip (이미 비활성)
  * 24h 0 + baseline > 0 → critical
  * 24h > 0 이지만 baseline 의 10% 미만 → warning
  * 24h >= baseline 의 10% → 미발화
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.collection_health import (  # noqa: E402
    _overall_status,
    evaluate_violations,
)


def test_evaluate_violations_critical_warning_and_skip():
    """4 사이트 합성 — critical 1 / warning 1 / skip 2 (baseline 0, 정상)."""
    stats: List[Dict[str, Any]] = [
        # critical — 평소 잘 들어오던 사이트가 0건
        {"code": "site_dead", "n_24h": 0, "baseline_24h_avg": 50.0,
         "last_collected": "2026-06-04T00:00:00+00:00", "hours_since": 50.0},
        # warning — 평소의 10% 미만 (5 / 50 = 10%, 임계는 strict <, 4건이면 < 10%)
        {"code": "site_slow", "n_24h": 4, "baseline_24h_avg": 50.0,
         "last_collected": "2026-06-06T00:00:00+00:00", "hours_since": 2.0},
        # 정상 — 평소의 10% 이상
        {"code": "site_ok", "n_24h": 30, "baseline_24h_avg": 50.0,
         "last_collected": "2026-06-06T01:00:00+00:00", "hours_since": 1.0},
        # skip — baseline 0 (이미 비활성/차단 사이트)
        {"code": "site_blocked", "n_24h": 0, "baseline_24h_avg": 0.0,
         "last_collected": None, "hours_since": None},
    ]
    violations = evaluate_violations(stats)
    codes = {v["code"]: v["severity"] for v in violations}
    assert codes == {"site_dead": "critical", "site_slow": "warning"}, codes
    # severity 의 reason 문자열에 핵심 정보 포함
    crit = next(v for v in violations if v["code"] == "site_dead")
    assert "0건" in crit["reason"]
    assert "site_dead" in crit["metric"]
    # 전체 상태
    assert _overall_status(violations) == "critical"
    assert _overall_status([v for v in violations if v["severity"] == "warning"]) == "warning"
    assert _overall_status([]) == "ok"


if __name__ == "__main__":
    test_evaluate_violations_critical_warning_and_skip()
    print("OK")


# ── 무산출 실행 감지 — baseline 0 사각지대 보완 ──────────────────────
def test_zero_yield_flags_broken_source():
    """beat 가 계속 돌리는데 산출이 0이면 '은퇴'가 아니라 '고장'이다."""
    from insight.collection_health import evaluate_zero_yield
    out = evaluate_zero_yield([{"code": "googlenews", "runs": 32, "failed": 32, "items": 0}])
    assert len(out) == 1
    v = out[0]
    assert v["severity"] == "critical"
    assert v["metric"] == "collection.zero_yield.googlenews"
    assert "32회 실행" in v["reason"] and "32회 실패" in v["reason"]


def test_zero_yield_metric_namespace_separate():
    """collection.* 와 쿨다운이 섞이면 한쪽이 다른 쪽을 침묵시킨다."""
    from insight.collection_health import evaluate_zero_yield, evaluate_violations
    zy = evaluate_zero_yield([{"code": "wpnews", "runs": 5, "failed": 0, "items": 0}])
    base = evaluate_violations([{"code": "wpnews", "n_24h": 0,
                                 "baseline_24h_avg": 10.0, "hours_since": 400}])
    assert zy[0]["metric"] != base[0]["metric"]


def test_zero_yield_empty_when_no_runs():
    from insight.collection_health import evaluate_zero_yield
    assert evaluate_zero_yield([]) == []


def test_zero_yield_names_the_block_cause():
    """원인을 이름으로 불러야 대응이 나온다.

    androidcentral 은 "34회 실행 0건" 으로만 35일간 리포트에 떠 있었고 아무도
    움직이지 않았다. 실제 원인은 stile 챌린지 벽이었고 재시도로는 안 뚫린다.
    """
    from insight.collection_health import evaluate_zero_yield
    out = evaluate_zero_yield([{
        "code": "androidcentral", "runs": 34, "failed": 34, "items": 0,
        "blocked": 34,
        "blocked_detail": "blocked: 요청 36건 중 18건이 차단 응답 (50%)",
    }])
    assert len(out) == 1
    reason = out[0]["reason"]
    assert "차단됨" in reason, reason
    assert "18건이 차단 응답" in reason, reason
    assert "접근 방식을 바꿔야" in reason, reason
    assert "은퇴가 아니라 고장" not in reason


def test_zero_yield_without_block_keeps_old_wording():
    from insight.collection_health import evaluate_zero_yield
    out = evaluate_zero_yield([{
        "code": "somesite", "runs": 5, "failed": 0, "items": 0,
        "blocked": 0, "blocked_detail": None,
    }])
    reason = out[0]["reason"]
    assert "전부 0건 반환" in reason
    assert "은퇴가 아니라 고장이다" in reason


def test_zero_yield_tolerates_missing_block_fields():
    """옛 호출부가 blocked 키 없이 넘겨도 깨지지 않아야 한다."""
    from insight.collection_health import evaluate_zero_yield
    out = evaluate_zero_yield([{"code": "x", "runs": 5, "failed": 2, "items": 0}])
    assert "2회 실패" in out[0]["reason"]


def test_fetched_but_no_new_is_not_an_alert():
    """긁었는데 신규가 없는 것은 고장이 아니다.

    ifixit 는 700건을 긁고 신규 저장 0건인데 "고장이다"로 경보가 떴다.
    이 소음이 진짜 장애(35일 막혀 있던 androidcentral)를 묻는다.
    """
    from insight.collection_health import evaluate_zero_yield
    out = evaluate_zero_yield([{
        "code": "ifixit", "runs": 12, "failed": 0, "items": 0,
        "blocked": 0, "blocked_detail": None, "fetched": 700, "fetch_known": 12,
    }])
    assert out == [], f"정상 소스에 경보가 떴다: {out}"


def test_fetched_zero_is_still_an_alert():
    """정말 아무것도 못 긁은 것은 여전히 경보다."""
    from insight.collection_health import evaluate_zero_yield
    out = evaluate_zero_yield([{
        "code": "deadsite", "runs": 12, "failed": 0, "items": 0,
        "blocked": 0, "blocked_detail": None, "fetched": 0, "fetch_known": 12,
    }])
    assert len(out) == 1


def test_blocked_alerts_even_when_something_was_fetched():
    """차단은 일부를 긁었더라도 알려야 한다 — 접근 방식 문제다."""
    from insight.collection_health import evaluate_zero_yield
    out = evaluate_zero_yield([{
        "code": "androidcentral", "runs": 12, "failed": 12, "items": 0,
        "blocked": 12, "blocked_detail": "blocked: 요청 36건 중 18건이 차단 응답 (50%)",
        "fetched": 3, "fetch_known": 12,
    }])
    assert len(out) == 1 and "차단됨" in out[0]["reason"]


def test_unknown_fetch_count_keeps_old_behaviour():
    """items_fetched 가 NULL 인 옛 행은 '모름'이라 판단하지 않는다."""
    from insight.collection_health import evaluate_zero_yield
    out = evaluate_zero_yield([{
        "code": "old", "runs": 12, "failed": 0, "items": 0,
        "blocked": 0, "blocked_detail": None, "fetched": 0, "fetch_known": 0,
    }])
    assert len(out) == 1
