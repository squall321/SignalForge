"""insight.collection_trend v2 — 사이트 자동 분류 + markdown 보고서 (1 케이스).

검증 항목
---------
1. classify_site/classify_sites 가 healthy/moderate/low/dying/dead 를 정확히 분류.
2. render_markdown 이 분류·변동·일별 총량 섹션을 모두 포함.
3. save_snapshot 이 json + md 양쪽 파일 생성.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.collection_trend import (  # noqa: E402
    classify_site,
    classify_sites,
    compute_site_stats,
    render_markdown,
    save_snapshot,
)


def test_classification_and_markdown_and_snapshot():
    # 7일 합성 — 5범주 골고루
    matrix = {
        "site_healthy":  [100, 110, 90, 105, 95, 100, 100],   # mean = 100  → healthy
        "site_moderate": [20, 25, 15, 22, 18, 20, 20],         # mean ≈ 20  → moderate
        "site_low":      [5, 4, 6, 5, 4, 5, 5],                # mean ≈ 5   → low
        "site_dying":    [10, 5, 3, 0, 0, 0, 0],               # mean ≈ 2.6 → 이건 사실 low (≥1)
        "site_truly_dying":[3, 2, 0, 0, 0, 0, 0],              # mean ≈ 0.71, 첫반=5/3 후반=0 → dying
        "site_dead":     [0, 0, 0, 0, 0, 0, 0],                # 전부 0 → dead
    }
    stats = compute_site_stats(matrix)
    by_code = {s["code"]: s for s in stats}

    # 개별 classify_site
    assert classify_site(by_code["site_healthy"]) == "healthy", by_code["site_healthy"]
    assert classify_site(by_code["site_moderate"]) == "moderate", by_code["site_moderate"]
    assert classify_site(by_code["site_low"]) == "low", by_code["site_low"]
    assert classify_site(by_code["site_truly_dying"]) == "dying", by_code["site_truly_dying"]
    assert classify_site(by_code["site_dead"]) == "dead", by_code["site_dead"]

    # classify_sites 그룹화
    cls = classify_sites(stats)
    assert "site_healthy" in cls["healthy"], cls
    assert "site_moderate" in cls["moderate"], cls
    assert "site_low" in cls["low"], cls
    assert "site_truly_dying" in cls["dying"], cls
    assert "site_dead" in cls["dead"], cls
    # counts 합 = stats 길이
    assert sum(cls["counts"].values()) == len(stats), cls["counts"]

    # render_markdown — 핵심 섹션 포함
    payload = {
        "generated_at": "2026-06-06T00:00:00+00:00",
        "days": 7,
        "active_sites": len(matrix),
        "daily_totals": [
            {"date": f"2026-06-0{i+1}", "total": sum(s[i] for s in matrix.values())}
            for i in range(7)
        ],
        "site_stats": stats,
        "volatile_sites": [
            {"code": "site_truly_dying", "kind": "trend_down",
             "mean_per_day": 0.71, "cv": 1.3,
             "half_ratio_delta": -1.0,
             "reasons": ["후반 -100% (반토막 비교)"]},
        ],
        "classification": cls,
        "thresholds": {},
        "summary": {
            "total_voc": sum(sum(s) for s in matrix.values()),
            "mean_per_day": 100.0,
            "volatile_count": 1,
            "trend_down_count": 1,
            "trend_up_count": 0,
            "volatile_swing_count": 0,
            "class_counts": cls["counts"],
        },
    }
    md = render_markdown(payload)
    assert "# 수집 트렌드 보고서" in md
    assert "## 사이트 상태 분류" in md
    assert "## 일별 총량" in md
    assert "## 변동 사이트" in md
    assert "## 조치 대상" in md
    assert "site_truly_dying" in md
    assert "site_dead" in md
    # 분류 카운트 표
    assert f"| {cls['counts']['healthy']} |" in md

    # save_snapshot — json + md 양쪽 생성
    with tempfile.TemporaryDirectory() as tmp:
        out = save_snapshot(payload, report_dir=Path(tmp))
        assert out["json"].exists(), out
        assert out["md"].exists(), out
        # json 파싱 가능
        loaded = json.loads(out["json"].read_text(encoding="utf-8"))
        assert loaded["days"] == 7
        # md 본문 일치
        assert out["md"].read_text(encoding="utf-8") == md


if __name__ == "__main__":
    test_classification_and_markdown_and_snapshot()
    print("OK")
