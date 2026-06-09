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
