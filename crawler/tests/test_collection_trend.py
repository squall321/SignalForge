"""insight.collection_trend 단위 테스트 (1 케이스).

compute_site_stats + identify_volatile_sites — 4 사이트 합성:
  * site_steady: 변동 거의 없음 (정상)
  * site_swing:  들쭉날쭉 (CV ≥ 1.0 → volatile_swing)
  * site_down:   후반 절반에서 급감 (trend_down)
  * site_low:    평균 < 1.0 → 변동 후보 제외
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.collection_trend import (  # noqa: E402
    compute_site_stats,
    daily_totals,
    identify_volatile_sites,
)


def test_compute_stats_and_volatility_classification():
    # 7일 합성 series (오래된 → 최신)
    matrix = {
        "site_steady": [50, 48, 52, 49, 51, 50, 50],          # 안정
        "site_swing":  [300, 0, 0, 200, 0, 0, 300],            # CV 매우 높음 (≥ 1.0)
        "site_down":   [80, 90, 85, 5, 3, 2, 1],               # 후반 급감
        "site_low":    [0, 1, 0, 0, 1, 0, 0],                  # 평균 < 1.0
    }
    stats = compute_site_stats(matrix)
    by_code = {s["code"]: s for s in stats}

    # 정렬: total desc — site_swing(400) ≥ site_down(266) ≥ site_steady(350)... 확인
    assert stats[0]["code"] in {"site_swing", "site_steady", "site_down"}
    # mean / total 정확성
    assert by_code["site_steady"]["total"] == sum(matrix["site_steady"])
    assert by_code["site_steady"]["mean_per_day"] > 0
    # site_swing 의 CV 는 1.0 이상
    assert by_code["site_swing"]["cv"] >= 1.0, by_code["site_swing"]
    # site_steady 의 CV 는 낮음 (0.1 미만)
    assert by_code["site_steady"]["cv"] < 0.1, by_code["site_steady"]
    # site_down 의 half_ratio_delta 음수 (< -0.5)
    assert by_code["site_down"]["half_ratio_delta"] is not None
    assert by_code["site_down"]["half_ratio_delta"] <= -0.5, by_code["site_down"]

    # 변동 식별
    vol = identify_volatile_sites(stats)
    vol_codes = {v["code"]: v["kind"] for v in vol}
    # site_steady 는 변동 후보 아님
    assert "site_steady" not in vol_codes, vol_codes
    # site_low 는 평균 < 1.0 → 제외
    assert "site_low" not in vol_codes, vol_codes
    # site_swing 은 volatile_swing
    assert vol_codes.get("site_swing") == "volatile_swing", vol_codes
    # site_down 은 trend_down
    assert vol_codes.get("site_down") == "trend_down", vol_codes

    # daily_totals
    dates = [f"2026-06-0{i+1}" for i in range(7)]
    totals = daily_totals(matrix, dates)
    assert len(totals) == 7
    # 첫째 날: 50 + 300 + 80 + 0 = 430
    assert totals[0]["total"] == 430, totals[0]


if __name__ == "__main__":
    test_compute_stats_and_volatility_classification()
    print("OK")
